// SPDX-License-Identifier: Apache-2.0

package storage

import (
	"context"
	"database/sql"
	"database/sql/driver"
	"errors"
	"io"
	"reflect"
	"strings"
	"testing"
	"time"
)

// A purpose-built driver exercises database/sql scan/iteration/close behavior.
// No extra dependency or production test hook is added.
type metadataDriverState struct {
	mode       string
	queries    []string
	arguments  [][]driver.NamedValue
	rowsClosed int
}

type metadataConnector struct{ state *metadataDriverState }
type metadataDriver struct{ state *metadataDriverState }
type metadataConn struct{ state *metadataDriverState }
type metadataRows struct {
	state *metadataDriverState
	index int
}

func (c metadataConnector) Connect(context.Context) (driver.Conn, error) {
	return &metadataConn{state: c.state}, nil
}
func (c metadataConnector) Driver() driver.Driver { return metadataDriver{state: c.state} }
func (d metadataDriver) Open(string) (driver.Conn, error) {
	return &metadataConn{state: d.state}, nil
}
func (c *metadataConn) Prepare(string) (driver.Stmt, error) {
	return nil, errors.New("unexpected Prepare")
}
func (c *metadataConn) Close() error { return nil }
func (c *metadataConn) Begin() (driver.Tx, error) {
	return nil, errors.New("unexpected Begin")
}
func (c *metadataConn) QueryContext(ctx context.Context, query string, args []driver.NamedValue) (driver.Rows, error) {
	c.state.queries = append(c.state.queries, query)
	c.state.arguments = append(c.state.arguments, append([]driver.NamedValue(nil), args...))
	switch c.state.mode {
	case "query-error":
		return nil, errors.New("injected query failure")
	case "wait-context":
		<-ctx.Done()
		return nil, ctx.Err()
	}
	return &metadataRows{state: c.state}, nil
}
func (r *metadataRows) Columns() []string {
	return []string{"id", "user_id", "feed_id", "title", "published_at", "timezone"}
}
func (r *metadataRows) Close() error {
	r.state.rowsClosed++
	return nil
}
func (r *metadataRows) Next(dest []driver.Value) error {
	if r.index > 0 {
		if r.state.mode == "late-error" {
			return errors.New("injected rows error after valid row")
		}
		return io.EOF
	}
	r.index++
	values := []driver.Value{int64(7), int64(3), int64(2), "fresh title", time.Date(2026, 3, 8, 2, 30, 0, 123456000, time.UTC), "UTC"}
	if r.state.mode == "scan-error" {
		values[0] = "not-an-integer"
	}
	copy(dest, values)
	return nil
}

func metadataTestStore(t *testing.T, mode string) (*Storage, *metadataDriverState, *sql.DB) {
	t.Helper()
	state := &metadataDriverState{mode: mode}
	db := sql.OpenDB(metadataConnector{state: state})
	db.SetMaxOpenConns(1)
	t.Cleanup(func() { _ = db.Close() })
	return NewStorage(db), state, db
}

func TestMetadataStorageBoundSingleProjection(t *testing.T) {
	s, state, db := metadataTestStore(t, "")
	entries, err := s.EntryMetadataByIDs(context.Background(), 3, []int64{7, 9, 7})
	if err != nil || len(entries) != 1 || entries[0].ID != 7 {
		t.Fatalf("result=%v error=%v", entries, err)
	}
	if len(state.queries) != 1 || state.queries[0] != entryMetadataSQL || state.rowsClosed != 1 {
		t.Fatalf("unexpected query/rows lifecycle: %+v", state)
	}
	args := state.arguments[0]
	if len(args) != 2 || args[0].Value != int64(3) || args[1].Value != "{7,9,7}" {
		t.Fatalf("query must bind identity and full candidate array: %v", args)
	}
	lower := strings.ToLower(entryMetadataSQL)
	for _, forbidden := range []string{"content", "cookie", "enclosure", "select *", "insert ", "update ", "delete ", ";"} {
		if strings.Contains(lower, forbidden) {
			t.Fatalf("forbidden projection token: %s", forbidden)
		}
	}
	for _, required := range []string{"e.user_id = $1", "e.id = ANY($2)", "f.user_id = e.user_id", "c.user_id = e.user_id", "ORDER BY e.id ASC"} {
		if !strings.Contains(entryMetadataSQL, required) {
			t.Fatalf("missing scope invariant: %s", required)
		}
	}
	if db.Stats().InUse != 0 {
		t.Fatal("connection not released")
	}
}

func TestMetadataStorageRejectsBeforeQuery(t *testing.T) {
	cases := []struct {
		user int64
		ids  []int64
	}{
		{0, []int64{1}}, {-1, []int64{1}}, {1, []int64{0}},
		{1, []int64{-1}}, {1, make([]int64, MaxEntryMetadataIDs+1)},
	}
	for _, tc := range cases {
		s, state, _ := metadataTestStore(t, "")
		if result, err := s.EntryMetadataByIDs(context.Background(), tc.user, tc.ids); err == nil || result != nil {
			t.Fatalf("invalid request returned %v / %v", result, err)
		}
		if len(state.queries) != 0 {
			t.Fatal("invalid request reached database")
		}
	}
	s, state, _ := metadataTestStore(t, "")
	result, err := s.EntryMetadataByIDs(context.Background(), 1, []int64{})
	if err != nil || !reflect.DeepEqual(result, []EntryMetadata{}) || len(state.queries) != 0 {
		t.Fatalf("empty request: %v / %v", result, err)
	}
	ids := make([]int64, MaxEntryMetadataIDs)
	for i := range ids {
		ids[i] = int64(i + 1)
	}
	if _, err := s.EntryMetadataByIDs(context.Background(), 1, ids); err != nil || len(state.queries) != 1 {
		t.Fatalf("exact cap not accepted: %v", err)
	}
}

func TestMetadataStorageFailuresNeverReturnPartialRows(t *testing.T) {
	for _, mode := range []string{"query-error", "scan-error", "late-error"} {
		t.Run(mode, func(t *testing.T) {
			s, state, db := metadataTestStore(t, mode)
			result, err := s.EntryMetadataByIDs(context.Background(), 1, []int64{7})
			if err == nil || result != nil {
				t.Fatalf("failure exposed partial/success result: %v / %v", result, err)
			}
			if mode != "query-error" && state.rowsClosed != 1 {
				t.Fatalf("rows not closed: %d", state.rowsClosed)
			}
			if db.Stats().InUse != 0 {
				t.Fatal("connection leaked on error")
			}
		})
	}
}

func TestMetadataStorageHonorsCancellation(t *testing.T) {
	s, state, db := metadataTestStore(t, "wait-context")
	ctx, cancel := context.WithTimeout(context.Background(), 25*time.Millisecond)
	defer cancel()
	result, err := s.EntryMetadataByIDs(ctx, 1, []int64{7})
	if result != nil || !errors.Is(err, context.DeadlineExceeded) || db.Stats().InUse != 0 {
		t.Fatalf("deadline result=%v err=%v stats=%+v", result, err, db.Stats())
	}
	if len(state.queries) != 1 {
		t.Fatal("expected one cancellable query")
	}
	ctx, stop := context.WithCancel(context.Background())
	stop()
	if result, err := s.EntryMetadataByIDs(ctx, 1, []int64{7}); result != nil || !errors.Is(err, context.Canceled) {
		t.Fatalf("cancellation result=%v error=%v", result, err)
	}
	if len(state.queries) != 1 {
		t.Fatal("already canceled request reached database")
	}
}
