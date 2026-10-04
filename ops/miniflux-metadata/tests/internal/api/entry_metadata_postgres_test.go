// SPDX-License-Identifier: Apache-2.0

package api

import (
	"bytes"
	"context"
	"database/sql"
	"database/sql/driver"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"net/http/httptest"
	"net/url"
	"os"
	"reflect"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/lib/pq"
	"miniflux.app/v2/internal/config"
	"miniflux.app/v2/internal/database"
	"miniflux.app/v2/internal/model"
	"miniflux.app/v2/internal/storage"
)

// The wrapper observes real lib/pq traffic, including unchanged authentication.
// It records SQL text only; it never records passwords/tokens or SQL parameters.
type metadataAudit struct {
	mu      sync.Mutex
	queries []string
	execs   []string
	fault   string
}

type metadataAuditConnector struct {
	inner driver.Connector
	audit *metadataAudit
}
type metadataAuditConn struct {
	driver.Conn
	audit *metadataAudit
}
type metadataFaultRows struct {
	driver.Rows
	fault string
	seen  bool
}

func (c metadataAuditConnector) Driver() driver.Driver { return c.inner.Driver() }
func (c metadataAuditConnector) Connect(ctx context.Context) (driver.Conn, error) {
	conn, err := c.inner.Connect(ctx)
	if err != nil {
		return nil, err
	}
	return &metadataAuditConn{Conn: conn, audit: c.audit}, nil
}
func (a *metadataAudit) reset(fault string) {
	a.mu.Lock()
	defer a.mu.Unlock()
	a.queries, a.execs, a.fault = nil, nil, fault
}
func (a *metadataAudit) snapshot() ([]string, []string) {
	a.mu.Lock()
	defer a.mu.Unlock()
	return append([]string(nil), a.queries...), append([]string(nil), a.execs...)
}
func isMetadataQuery(query string) bool { return strings.Contains(query, "e.id = ANY($2)") }
func (c *metadataAuditConn) QueryContext(ctx context.Context, query string, args []driver.NamedValue) (driver.Rows, error) {
	c.audit.mu.Lock()
	c.audit.queries = append(c.audit.queries, query)
	fault := c.audit.fault
	c.audit.mu.Unlock()
	metadata := isMetadataQuery(query)
	if fault == "auth-query" && !metadata || fault == "metadata-query" && metadata {
		return nil, errors.New("injected database query error")
	}
	rows, err := c.Conn.(driver.QueryerContext).QueryContext(ctx, query, args)
	if err == nil && metadata && (fault == "metadata-scan" || fault == "metadata-late") {
		return &metadataFaultRows{Rows: rows, fault: fault}, nil
	}
	return rows, err
}
func (c *metadataAuditConn) ExecContext(ctx context.Context, query string, args []driver.NamedValue) (driver.Result, error) {
	c.audit.mu.Lock()
	c.audit.execs = append(c.audit.execs, query)
	c.audit.mu.Unlock()
	return c.Conn.(driver.ExecerContext).ExecContext(ctx, query, args)
}
func (r *metadataFaultRows) Next(dest []driver.Value) error {
	if r.seen && r.fault == "metadata-late" {
		return errors.New("injected late iteration failure")
	}
	err := r.Rows.Next(dest)
	if err == nil {
		r.seen = true
		if r.fault == "metadata-scan" {
			dest[0] = "not-an-int64"
		}
	}
	return err
}

type metadataPGFixture struct {
	db      *sql.DB
	store   *storage.Storage
	handler http.Handler
	audit   *metadataAudit
	users   [3]*model.User
	tokens  [3]string
	feeds   [3]int64
	cats    [3]int64
}

func newMetadataPGFixture(t *testing.T) *metadataPGFixture {
	t.Helper()
	dsn := os.Getenv("ISSUE73_POSTGRES_URL")
	if dsn == "" {
		if os.Getenv("ISSUE73_REQUIRE_POSTGRES") == "1" {
			t.Fatal("required disposable PostgreSQL URL is missing")
		}
		t.Skip("set ISSUE73_POSTGRES_URL and ISSUE73_DISPOSABLE_POSTGRES=1 for real PostgreSQL integration")
	}
	u, err := url.Parse(dsn)
	if err != nil || u.Scheme != "postgres" || (u.Hostname() != "127.0.0.1" && u.Hostname() != "localhost") || u.Path != "/issue73_metadata_test" || u.Port() != "55473" || u.RawQuery != "sslmode=disable" || u.User == nil || u.User.Username() != "issue73" || os.Getenv("ISSUE73_DISPOSABLE_POSTGRES") != "1" {
		t.Fatal("refusing non-fixture database: require loopback postgres URL, issue73 user, issue73_metadata_test database and explicit disposable marker")
	}
	base, err := sql.Open("postgres", dsn)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = base.Close() })
	schema := fmt.Sprintf("issue73_%d_%d", os.Getpid(), time.Now().UnixNano())
	if _, err := base.Exec("CREATE SCHEMA " + pq.QuoteIdentifier(schema)); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() {
		if _, err := base.Exec("DROP SCHEMA " + pq.QuoteIdentifier(schema) + " CASCADE"); err != nil {
			t.Errorf("fixture schema cleanup: %v", err)
		}
	})
	q := u.Query()
	q.Set("search_path", schema)
	u.RawQuery = q.Encode()
	connector, err := pq.NewConnector(u.String())
	if err != nil {
		t.Fatal(err)
	}
	audit := &metadataAudit{}
	db := sql.OpenDB(metadataAuditConnector{inner: connector, audit: audit})
	db.SetMaxOpenConns(4)
	t.Cleanup(func() { _ = db.Close() })
	if err := database.Migrate(db); err != nil {
		t.Fatal(err)
	}
	if err := database.IsSchemaUpToDate(db); err != nil {
		t.Fatal(err)
	}
	f := &metadataPGFixture{db: db, store: storage.NewStorage(db), audit: audit}
	f.handler = NewHandler(f.store, nil)
	for i := range f.users {
		user, err := f.store.CreateUser(&model.UserCreationRequest{Username: fmt.Sprintf("metadata_user_%d", i), Password: "synthetic-test-password", IsAdmin: i == 2})
		if err != nil {
			t.Fatal(err)
		}
		f.users[i] = user
		f.tokens[i] = fmt.Sprintf("synthetic-issue73-fixture-token-%d", i)
		f.exec(t, `INSERT INTO api_keys (user_id,token,description) VALUES ($1,$2,'fixture')`, user.ID, f.tokens[i])
		if err := db.QueryRow(`SELECT id FROM categories WHERE user_id=$1 LIMIT 1`, user.ID).Scan(&f.cats[i]); err != nil {
			t.Fatal(err)
		}
		if err := db.QueryRow(`INSERT INTO feeds (user_id,category_id,title,feed_url,site_url,cookie) VALUES ($1,$2,'synthetic feed',$3,'https://example.invalid','synthetic-cookie-secret') RETURNING id`, user.ID, f.cats[i], fmt.Sprintf("https://example.invalid/%d", i)).Scan(&f.feeds[i]); err != nil {
			t.Fatal(err)
		}
		f.entry(t, int64(i+1), i, "unread", "2026-03-08T09:59:59.123456Z")
	}
	f.entry(t, 4, 0, "read", "2026-11-01T09:30:00.123456Z")
	f.entry(t, 5, 0, "removed", "2026-10-01T00:00:00Z")
	f.exec(t, `INSERT INTO enclosures(user_id,entry_id,url,mime_type) VALUES ($1,1,'https://example.invalid/private-enclosure','audio/mp3')`, f.users[0].ID)
	audit.reset("")
	return f
}
func (f *metadataPGFixture) exec(t *testing.T, query string, args ...any) {
	t.Helper()
	if _, err := f.db.Exec(query, args...); err != nil {
		t.Fatal(err)
	}
}
func (f *metadataPGFixture) entry(t *testing.T, id int64, user int, status, published string) {
	t.Helper()
	f.exec(t, `INSERT INTO entries (id,user_id,feed_id,hash,published_at,changed_at,title,url,author,content,status) VALUES ($1,$2,$3,$4,$5,$5,$6,'https://example.invalid/entry','',repeat('private-body-',100000),$7)`, id, f.users[user].ID, f.feeds[user], fmt.Sprintf("hash-%d", id), published, fmt.Sprintf("Title %d", id), status)
}
func (f *metadataPGFixture) call(method, path, body, token, username, password string, ctx context.Context) *httptest.ResponseRecorder {
	r := httptest.NewRequest(method, path, strings.NewReader(body))
	r.Header.Set("Content-Type", "application/json")
	if token != "" {
		r.Header.Set("X-Auth-Token", token)
	}
	if username != "" || password != "" {
		r.SetBasicAuth(username, password)
	}
	if ctx != nil {
		r = r.WithContext(ctx)
	}
	w := httptest.NewRecorder()
	f.handler.ServeHTTP(w, r)
	return w
}
func metadataIDs(t *testing.T, w *httptest.ResponseRecorder, userID int64) []int64 {
	t.Helper()
	if w.Code != http.StatusOK || w.Header().Get("X-Reader-Entry-Metadata") != "1" || w.Header().Get("Cache-Control") != "no-store" {
		t.Fatalf("bad metadata response: %d %v %s", w.Code, w.Header(), w.Body.String())
	}
	var root map[string]json.RawMessage
	if err := json.Unmarshal(w.Body.Bytes(), &root); err != nil || len(root) != 1 || root["entries"] == nil {
		t.Fatalf("invalid root: %s (%v)", w.Body.String(), err)
	}
	var rows []map[string]json.RawMessage
	if err := json.Unmarshal(root["entries"], &rows); err != nil || rows == nil {
		t.Fatalf("invalid entries: %s (%v)", root["entries"], err)
	}
	ids := make([]int64, 0, len(rows))
	for _, row := range rows {
		if len(row) != 6 {
			t.Fatalf("wrong field count: %v", row)
		}
		for _, key := range []string{"id", "user_id", "feed_id", "title", "url", "published_at"} {
			if row[key] == nil {
				t.Fatalf("missing field %q", key)
			}
		}
		var id, uid int64
		var title, articleURL string
		var published time.Time
		if json.Unmarshal(row["id"], &id) != nil || json.Unmarshal(row["user_id"], &uid) != nil || json.Unmarshal(row["title"], &title) != nil || json.Unmarshal(row["url"], &articleURL) != nil || articleURL == "" || json.Unmarshal(row["published_at"], &published) != nil || uid != userID {
			t.Fatalf("invalid metadata types/ownership: %v", row)
		}
		if len(ids) > 0 && id <= ids[len(ids)-1] {
			t.Fatalf("not strictly ascending/deduplicated: %d", id)
		}
		ids = append(ids, id)
	}
	return ids
}
func (f *metadataPGFixture) assertQueryAudit(t *testing.T, count, authWrites int) {
	t.Helper()
	queries, execs := f.audit.snapshot()
	seen := 0
	for _, query := range queries {
		if isMetadataQuery(query) {
			seen++
			lower := strings.ToLower(query)
			for _, bad := range []string{"content", "cookie", "enclosure", "select *", "insert ", "update ", "delete "} {
				if strings.Contains(lower, bad) {
					t.Fatalf("forbidden metadata query token %s", bad)
				}
			}
		}
		if strings.Contains(strings.ToLower(query), "enclosures") {
			t.Fatal("metadata request queried enclosures")
		}
	}
	if seen != count || len(execs) != authWrites {
		t.Fatalf("metadata queries=%d want=%d; writes=%d want=%d: %v", seen, count, len(execs), authWrites, execs)
	}
	for _, query := range execs {
		if query != `UPDATE users SET last_login_at=now() WHERE id=$1` && query != `UPDATE api_keys SET last_used_at=now() WHERE user_id=$1 and token=$2` {
			t.Fatalf("unexpected DML: %s", query)
		}
	}
}

func TestMetadataPostgres(t *testing.T) {
	f := newMetadataPGFixture(t)
	// The ordinary entry route uses the application's media-proxy configuration.
	// Match initialized application defaults without reading environment or secrets.
	// This parent test is deliberately serial; restore state before parallel tests.
	previousOptions := config.Opts
	config.Opts = config.NewConfigOptions()
	t.Cleanup(func() { config.Opts = previousOptions })
	const endpoint = "/v1/entries/metadata"
	const mixed = `{"entry_ids":[5,4,3,2,1,1,999999999]}`

	t.Run("real-auth-ownership-admin-and-precedence", func(t *testing.T) {
		for i, want := range [][]int64{{1, 4}, {2}, {3}} {
			for _, basic := range []bool{false, true} {
				token, username, password, writes := f.tokens[i], "", "", 2
				if basic {
					token, username, password, writes = "", f.users[i].Username, "synthetic-test-password", 1
				}
				f.audit.reset("")
				w := f.call(http.MethodPost, endpoint, mixed, token, username, password, nil)
				if got := metadataIDs(t, w, f.users[i].ID); !reflect.DeepEqual(got, want) {
					t.Fatalf("user=%d basic=%t got=%v want=%v", i, basic, got, want)
				}
				f.assertQueryAudit(t, 1, writes)
			}
		}
		f.audit.reset("")
		w := f.call(http.MethodPost, endpoint, mixed, f.tokens[0], f.users[1].Username, "synthetic-test-password", nil)
		if got := metadataIDs(t, w, f.users[0].ID); !reflect.DeepEqual(got, []int64{1, 4}) {
			t.Fatal("token did not take precedence")
		}
		f.assertQueryAudit(t, 1, 2)
		var login, used sql.NullTime
		if err := f.db.QueryRow(`SELECT u.last_login_at,k.last_used_at FROM users u JOIN api_keys k ON k.user_id=u.id WHERE u.id=$1`, f.users[0].ID).Scan(&login, &used); err != nil || !login.Valid || !used.Valid {
			t.Fatalf("existing auth timestamps missing: %v", err)
		}
	})

	t.Run("auth-failures-no-metadata-query", func(t *testing.T) {
		cases := []struct{ token, user, password string }{
			{"", "", ""}, {"invalid", "", ""}, {"invalid", f.users[0].Username, "synthetic-test-password"},
			{"", f.users[0].Username, "incorrect"}, {"", "nonexistent", "incorrect"}, {"", f.users[0].Username, ""},
		}
		f.exec(t, `INSERT INTO api_keys(user_id,token,description) VALUES ($1,'revoked-fixture','revoked')`, f.users[0].ID)
		f.exec(t, `DELETE FROM api_keys WHERE token='revoked-fixture'`)
		cases = append(cases, struct{ token, user, password string }{"revoked-fixture", "", ""})
		for _, tc := range cases {
			f.audit.reset("")
			w := f.call(http.MethodPost, endpoint, mixed, tc.token, tc.user, tc.password, nil)
			if w.Code != http.StatusUnauthorized {
				t.Fatalf("got %d, want 401", w.Code)
			}
			f.assertQueryAudit(t, 0, 0)
		}
		for _, basic := range []bool{false, true} {
			f.audit.reset("auth-query")
			token, user, password, want := f.tokens[0], "", "", http.StatusInternalServerError
			if basic {
				token, user, password, want = "", f.users[0].Username, "synthetic-test-password", http.StatusUnauthorized
			}
			w := f.call(http.MethodPost, endpoint, mixed, token, user, password, nil)
			if w.Code != want {
				t.Fatalf("upstream auth failure behavior changed: %d want %d", w.Code, want)
			}
			f.assertQueryAudit(t, 0, 0)
		}
		f.audit.reset("")
	})

	t.Run("corrupt-parent-ownership-and-missing-parent", func(t *testing.T) {
		f.exec(t, `UPDATE feeds SET user_id=$1 WHERE id=$2`, f.users[1].ID, f.feeds[0])
		w := f.call(http.MethodPost, endpoint, mixed, f.tokens[0], "", "", nil)
		if got := metadataIDs(t, w, f.users[0].ID); len(got) != 0 {
			t.Fatalf("foreign feed rows leaked: %v", got)
		}
		f.exec(t, `UPDATE feeds SET user_id=$1,category_id=$2 WHERE id=$3`, f.users[0].ID, f.cats[1], f.feeds[0])
		w = f.call(http.MethodPost, endpoint, mixed, f.tokens[0], "", "", nil)
		if got := metadataIDs(t, w, f.users[0].ID); len(got) != 0 {
			t.Fatalf("foreign category rows leaked: %v", got)
		}
		f.exec(t, `UPDATE feeds SET category_id=$1 WHERE id=$2`, f.cats[0], f.feeds[0])
		// Real foreign keys remove orphan rows; no constraint bypass is needed.
		var doomed int64
		if err := f.db.QueryRow(`INSERT INTO feeds(user_id,category_id,title,feed_url,site_url) VALUES ($1,$2,'deleted','https://example.invalid/deleted','https://example.invalid') RETURNING id`, f.users[0].ID, f.cats[0]).Scan(&doomed); err != nil {
			t.Fatal(err)
		}
		f.exec(t, `INSERT INTO entries(id,user_id,feed_id,hash,published_at,changed_at,title,url,author) VALUES(77,$1,$2,'deleted',now(),now(),'deleted','https://example.invalid','')`, f.users[0].ID, doomed)
		f.exec(t, `DELETE FROM feeds WHERE id=$1`, doomed)
		w = f.call(http.MethodPost, endpoint, `{"entry_ids":[2,5,77,999999999]}`, f.tokens[0], "", "", nil)
		if got := metadataIDs(t, w, f.users[0].ID); len(got) != 0 {
			t.Fatalf("foreign/removed/deleted/missing IDs distinguishable: %v", got)
		}
	})

	t.Run("body-independent-payload-current-title-and-exact-fields", func(t *testing.T) {
		body := `{"entry_ids":[1]}`
		before := f.call(http.MethodPost, endpoint, body, f.tokens[0], "", "", nil)
		metadataIDs(t, before, f.users[0].ID)
		f.exec(t, `UPDATE entries SET content=repeat('larger-private-body-',250000) WHERE id=1`)
		f.exec(t, `INSERT INTO enclosures(user_id,entry_id,url) SELECT $1,1,'https://example.invalid/enclosure/'||n FROM generate_series(1,100) n`, f.users[0].ID)
		f.audit.reset("")
		after := f.call(http.MethodPost, endpoint, body, f.tokens[0], "", "", nil)
		if !bytes.Equal(before.Body.Bytes(), after.Body.Bytes()) {
			t.Fatal("body/enclosure growth changed metadata response")
		}
		f.assertQueryAudit(t, 1, 2)
		var changedBefore, changedAfter time.Time
		if err := f.db.QueryRow(`SELECT changed_at FROM entries WHERE id=1`).Scan(&changedBefore); err != nil {
			t.Fatal(err)
		}
		f.exec(t, `UPDATE entries SET title='fresh title without changed_at' WHERE id=1`)
		if err := f.db.QueryRow(`SELECT changed_at FROM entries WHERE id=1`).Scan(&changedAfter); err != nil || !changedBefore.Equal(changedAfter) {
			t.Fatal("fixture changed changed_at")
		}
		w := f.call(http.MethodPost, endpoint, body, f.tokens[0], "", "", nil)
		if !strings.Contains(w.Body.String(), "fresh title without changed_at") {
			t.Fatal("stale metadata title")
		}
	})

	t.Run("current-url-without-changed-at-preserves-business-state", func(t *testing.T) {
		var beforeChanged, afterChanged time.Time
		var beforeStatus, afterStatus string
		var beforeStarred, afterStarred bool
		if err := f.db.QueryRow(`SELECT changed_at,status,starred FROM entries WHERE id=1`).Scan(&beforeChanged, &beforeStatus, &beforeStarred); err != nil {
			t.Fatal(err)
		}
		f.exec(t, `UPDATE entries SET url='https://example.invalid/current-url-without-changed-at' WHERE id=1`)
		f.audit.reset("")
		w := f.call(http.MethodPost, endpoint, `{"entry_ids":[1]}`, f.tokens[0], "", "", nil)
		metadataIDs(t, w, f.users[0].ID)
		if !strings.Contains(w.Body.String(), `"url":"https://example.invalid/current-url-without-changed-at"`) {
			t.Fatal("metadata did not expose the current URL")
		}
		f.assertQueryAudit(t, 1, 2)
		if err := f.db.QueryRow(`SELECT changed_at,status,starred FROM entries WHERE id=1`).Scan(&afterChanged, &afterStatus, &afterStarred); err != nil || !beforeChanged.Equal(afterChanged) || beforeStatus != afterStatus || beforeStarred != afterStarred {
			t.Fatal("metadata changed article business state")
		}
	})

	t.Run("timezone-parity-with-ordinary-query", func(t *testing.T) {
		for _, tz := range []string{"UTC", "America/Los_Angeles", "Asia/Kathmandu"} {
			f.exec(t, `UPDATE users SET timezone=$1 WHERE id=$2`, tz, f.users[0].ID)
			for _, date := range []string{"2026-03-08T09:59:59.123456Z", "2026-03-08T10:00:00.654321Z", "2026-11-01T08:30:00.123456Z", "2026-11-01T09:30:00.123456Z", "2026-10-01T12:34:56.123456+05:45", "0001-01-01T00:00:00Z"} {
				f.exec(t, `UPDATE entries SET published_at=$1 WHERE id=1`, date)
				ordinary, err := f.store.NewEntryQueryBuilder(f.users[0].ID).WithEntryIDs(1).GetEntry()
				if err != nil || ordinary == nil {
					t.Fatalf("ordinary query: %v", err)
				}
				metadata, err := f.store.EntryMetadataByIDs(context.Background(), f.users[0].ID, []int64{1})
				if err != nil || len(metadata) != 1 || metadata[0].PublishedAt.Format(time.RFC3339Nano) != ordinary.Date.Format(time.RFC3339Nano) {
					t.Fatalf("timezone=%s date=%s parity failure: metadata=%v ordinary=%v err=%v", tz, date, metadata, ordinary.Date, err)
				}
			}
		}
		f.exec(t, `UPDATE users SET timezone='UTC' WHERE id=$1`, f.users[0].ID)
		f.exec(t, `UPDATE entries SET published_at='2026-10-01T00:00:00Z' WHERE id=1`)
	})

	t.Run("limits-and-spoofing", func(t *testing.T) {
		for _, body := range []string{`{"entry_ids":[1],"user_id":2}`, `{"entry_ids":[1],"content":true}`, `{"entry_ids":[1],"entry_ids":[]}`, `{"entry_ids":[0]}`, `{"entry_ids":[-1]}`, `{"entry_ids":[1.1]}`, `{"entry_ids":[9223372036854775808]}`, `{"entry_ids":null}`, `{"entry_ids":[]} {}`, `{"entry_ids":[` + strings.Repeat("1,", 10000) + `1]}`} {
			f.audit.reset("")
			w := f.call(http.MethodPost, endpoint, body, f.tokens[0], "", "", nil)
			if w.Code != http.StatusBadRequest {
				t.Fatalf("invalid body got %d", w.Code)
			}
			f.assertQueryAudit(t, 0, 2)
		}
		for _, query := range []string{"?", "?user_id=2", "?limit=1", "?search=anything"} {
			f.audit.reset("")
			if w := f.call(http.MethodPost, endpoint+query, `{"entry_ids":[]}`, f.tokens[0], "", "", nil); w.Code != http.StatusBadRequest {
				t.Fatalf("query spoofing got %d", w.Code)
			}
			f.assertQueryAudit(t, 0, 2)
		}
		f.audit.reset("")
		w := f.call(http.MethodPost, endpoint, `{"entry_ids":[]}`, f.tokens[0], "", "", nil)
		metadataIDs(t, w, f.users[0].ID)
		f.assertQueryAudit(t, 0, 2)
		w = f.call(http.MethodPost, endpoint, `{"entry_ids":[9223372036854775807]}`, f.tokens[0], "", "", nil)
		metadataIDs(t, w, f.users[0].ID)
		f.exec(t, `INSERT INTO entries(id,user_id,feed_id,hash,published_at,changed_at,title,url,author) SELECT n,$1,$2,'boundary-'||n,now(),now(),'boundary title','https://example.invalid','' FROM generate_series(100,10099) n`, f.users[0].ID, f.feeds[0])
		ids := make([]int64, 10000)
		for i := range ids {
			ids[i] = int64(100 + i)
		}
		encoded, err := json.Marshal(map[string]any{"entry_ids": ids})
		if err != nil {
			t.Fatal(err)
		}
		f.audit.reset("")
		w = f.call(http.MethodPost, endpoint, string(encoded), f.tokens[0], "", "", nil)
		if got := metadataIDs(t, w, f.users[0].ID); !reflect.DeepEqual(got, ids) {
			t.Fatalf("exact 10000 cap truncated or reordered: count=%d", len(got))
		}
		f.assertQueryAudit(t, 1, 2)
		f.exec(t, `DELETE FROM entries WHERE id BETWEEN 100 AND 10099`)
	})

	t.Run("query-scan-late-errors-never-success", func(t *testing.T) {
		for _, fault := range []string{"metadata-query", "metadata-scan", "metadata-late"} {
			f.audit.reset(fault)
			w := f.call(http.MethodPost, endpoint, `{"entry_ids":[1,4]}`, f.tokens[0], "", "", nil)
			if w.Code != http.StatusInternalServerError || strings.Contains(w.Body.String(), `"entries"`) {
				t.Fatalf("%s returned partial/success: %d %s", fault, w.Code, w.Body.String())
			}
			if f.db.Stats().InUse != 0 {
				t.Fatalf("%s leaked connection: %+v", fault, f.db.Stats())
			}
			f.assertQueryAudit(t, 1, 2)
		}
		f.audit.reset("")
	})

	t.Run("real-postgres-request-cancel-and-server-deadline", func(t *testing.T) {
		for _, requestTimeout := range []time.Duration{75 * time.Millisecond, 0} {
			tx, err := f.db.Begin()
			if err != nil {
				t.Fatal(err)
			}
			if _, err := tx.Exec(`LOCK TABLE entries IN ACCESS EXCLUSIVE MODE`); err != nil {
				_ = tx.Rollback()
				t.Fatal(err)
			}
			ctx := context.Background()
			cancel := func() {}
			if requestTimeout != 0 {
				ctx, cancel = context.WithTimeout(ctx, requestTimeout)
			}
			start := time.Now()
			w := f.call(http.MethodPost, endpoint, `{"entry_ids":[1]}`, f.tokens[0], "", "", ctx)
			elapsed := time.Since(start)
			cancel()
			_ = tx.Rollback()
			if w.Code != http.StatusInternalServerError || strings.Contains(w.Body.String(), `"entries"`) {
				t.Fatalf("canceled query returned %d %s", w.Code, w.Body.String())
			}
			if requestTimeout != 0 && elapsed > 2*time.Second || requestTimeout == 0 && (elapsed < 4*time.Second || elapsed > 8*time.Second) {
				t.Fatalf("wrong cancellation/deadline duration: %v", elapsed)
			}
			if f.db.Stats().InUse != 0 {
				t.Fatalf("connection leaked after cancellation: %+v", f.db.Stats())
			}
			w = f.call(http.MethodPost, endpoint, `{"entry_ids":[1]}`, f.tokens[0], "", "", nil)
			metadataIDs(t, w, f.users[0].ID)
		}
	})

	t.Run("existing-routes-and-options", func(t *testing.T) {
		w := f.call(http.MethodOptions, endpoint, "", "", "", "", nil)
		if w.Code != http.StatusNoContent || w.Header().Get("Access-Control-Allow-Methods") != "GET, POST, PUT, DELETE, OPTIONS" {
			t.Fatalf("CORS changed: %d %v", w.Code, w.Header())
		}
		for _, method := range []string{http.MethodPut, http.MethodDelete} {
			w = f.call(method, endpoint, `{"entry_ids":[1]}`, f.tokens[0], "", "", nil)
			if w.Code == http.StatusOK || w.Header().Get("X-Reader-Entry-Metadata") != "" {
				t.Fatalf("wrong method reached projection: %s %d", method, w.Code)
			}
		}
		w = f.call(http.MethodGet, "/v1/entries/1", "", f.tokens[0], "", "", nil)
		if w.Code != http.StatusOK || !strings.Contains(w.Body.String(), "larger-private-body-") || !strings.Contains(w.Body.String(), `"enclosures"`) {
			t.Fatalf("ordinary entry response changed: %d", w.Code)
		}
		w = f.call(http.MethodGet, "/v1/me", "", f.tokens[0], "", "", nil)
		if w.Code != http.StatusOK || !strings.Contains(w.Body.String(), f.users[0].Username) {
			t.Fatalf("ordinary identity endpoint failed: %d", w.Code)
		}
	})
}
