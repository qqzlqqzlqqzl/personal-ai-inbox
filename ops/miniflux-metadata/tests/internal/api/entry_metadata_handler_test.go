// SPDX-License-Identifier: Apache-2.0

package api

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"miniflux.app/v2/internal/http/request"
	"miniflux.app/v2/internal/storage"
)

func TestMetadataMissingAuthentication(t *testing.T) {
	for _, authorization := range []string{"", "Bearer invalid", "Basic invalid", "Basic Og=="} {
		t.Run(authorization, func(t *testing.T) {
			r := httptest.NewRequest(http.MethodPost, "/v1/entries/metadata", strings.NewReader(`{"entry_ids":[1]}`))
			r.Header.Set("Authorization", authorization)
			w := httptest.NewRecorder()
			NewHandler(nil, nil).ServeHTTP(w, r)
			if w.Code != http.StatusUnauthorized {
				t.Fatalf("got %d, want 401", w.Code)
			}
		})
	}
}

func TestMetadataParserRejectsInvalidRequestsBeforeDB(t *testing.T) {
	oversizedIDs := `{"entry_ids":[` + strings.Repeat("1,", storage.MaxEntryMetadataIDs) + "1]}"
	for _, body := range []string{
		``, `null`, `{}`, `{"entry_ids":null}`, `{"entry_ids":[0]}`,
		`{"entry_ids":[-1]}`, `{"entry_ids":[1.5]}`, `{"entry_ids":["1"]}`,
		`{"entry_ids":[9223372036854775808]}`, `{"entry_ids":[],"user_id":2}`,
		`{"entry_ids":[],"status":"removed"}`, `{"entry_ids":[],"entry_ids":[1]}`, `{"ENTRY_IDS":[]}`, `{"entry_ids":[]} {}`,
		oversizedIDs, strings.Repeat(" ", maxEntryMetadataRequestBytes+1),
	} {
		r := httptest.NewRequest(http.MethodPost, "/v1/entries/metadata", strings.NewReader(body))
		ctx := context.WithValue(r.Context(), request.UserIDContextKey, int64(1))
		ctx = context.WithValue(ctx, request.IsAuthenticatedContextKey, true)
		w := httptest.NewRecorder()
		(&handler{}).getEntryMetadataHandler(w, r.WithContext(ctx))
		if w.Code != http.StatusBadRequest {
			t.Fatalf("got %d, want 400 for %.80q", w.Code, body)
		}
	}
}

func TestMetadataEmptyIDsAndUnsupportedQuery(t *testing.T) {
	for _, query := range []string{"", "?", "?user_id=2", "?search=ignored", "?limit=1"} {
		r := httptest.NewRequest(http.MethodPost, "/v1/entries/metadata"+query, strings.NewReader(`{"entry_ids":[]}`))
		ctx := context.WithValue(r.Context(), request.UserIDContextKey, int64(1))
		ctx = context.WithValue(ctx, request.IsAuthenticatedContextKey, true)
		w := httptest.NewRecorder()
		(&handler{}).getEntryMetadataHandler(w, r.WithContext(ctx))
		want := http.StatusBadRequest
		if query == "" {
			want = http.StatusOK
			var result map[string]any
			if err := json.Unmarshal(w.Body.Bytes(), &result); err != nil {
				t.Fatal(err)
			}
			if entries, ok := result["entries"].([]any); !ok || len(entries) != 0 || len(result) != 1 {
				t.Fatalf("unexpected empty response: %s", w.Body.String())
			}
		}
		if w.Code != want || w.Header().Get("Cache-Control") != "no-store" {
			t.Fatalf("unexpected response status %d, headers %v", w.Code, w.Header())
		}
	}
}

func TestMetadataExactBodyBoundary(t *testing.T) {
	for _, size := range []int{maxEntryMetadataRequestBytes - 1, maxEntryMetadataRequestBytes, maxEntryMetadataRequestBytes + 1} {
		body := `{"entry_ids":[]}`
		body += strings.Repeat(" ", size-len(body))
		r := httptest.NewRequest(http.MethodPost, "/v1/entries/metadata", strings.NewReader(body))
		ctx := context.WithValue(r.Context(), request.UserIDContextKey, int64(1))
		ctx = context.WithValue(ctx, request.IsAuthenticatedContextKey, true)
		w := httptest.NewRecorder()
		(&handler{}).getEntryMetadataHandler(w, r.WithContext(ctx))
		want := http.StatusOK
		if size > maxEntryMetadataRequestBytes {
			want = http.StatusBadRequest
		}
		if w.Code != want {
			t.Fatalf("size %d: got %d, want %d", size, w.Code, want)
		}
	}
}

func TestMetadataDirectHandlerRequiresTrustedIdentity(t *testing.T) {
	for _, uid := range []int64{-1, 0, 1} {
		for _, authenticated := range []bool{false, true} {
			if uid > 0 && authenticated {
				continue
			}
			r := httptest.NewRequest(http.MethodPost, "/v1/entries/metadata", strings.NewReader(`{"entry_ids":[1]}`))
			ctx := context.WithValue(r.Context(), request.UserIDContextKey, uid)
			ctx = context.WithValue(ctx, request.IsAuthenticatedContextKey, authenticated)
			w := httptest.NewRecorder()
			(&handler{}).getEntryMetadataHandler(w, r.WithContext(ctx))
			if w.Code != http.StatusUnauthorized {
				t.Fatalf("identity %d/%t: %d", uid, authenticated, w.Code)
			}
		}
	}
}
