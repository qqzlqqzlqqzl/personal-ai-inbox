export const MAX_SNAPSHOT_ENTRIES = 60
export const MAX_SNAPSHOT_BYTES = 384 * 1024
export const SNAPSHOT_MAX_AGE = 7 * 24 * 60 * 60 * 1000

// The caller supplies createHomeIdentity's canonical server and the verified id.
export const snapshotOwnerKey = (identity) =>
  identity ? JSON.stringify([identity.server, identity.userId]) : null

export function articleSnapshotQuery({ content, settings, info, ai, calendar, categoryHidden }) {
  const scope = info.from
  const signature = JSON.stringify([
    scope, String(info.id ?? ""), settings.showStatus,
    content.filterString || "", content.filterDate || null,
    ["starred", "history"].includes(scope) ? "changed_at" : settings.orderBy,
    settings.orderDirection, settings.pageSize, settings.removeDuplicates,
    scope === "feed" ? null : Boolean(settings.showHiddenFeeds || categoryHidden),
    ai.ai_view, ai.ai_min, ai.ai_sort, ai.direction, Boolean(ai.has_note), ai.limit,
    calendar.day, calendar.zone,
  ])
  const isDefault = scope === "all" && settings.showStatus === "unread" &&
    !content.filterString && !content.filterDate && !settings.showHiddenFeeds &&
    ai.ai_view === "recommended" && !ai.has_note && ai.ai_sort === "fresh" &&
    ai.direction === "desc" && ai.ai_min === undefined
  return { signature, isDefault }
}

const text = (value, limit) => typeof value === "string" ? value.slice(0, limit) : undefined
const fields = (source, keys) => Object.fromEntries(keys.flatMap(key => {
  const value = source?.[key]
  if (typeof value === "string") return [[key, text(value, 1024)]]
  if (typeof value === "boolean" || Number.isFinite(value) || value === null) return [[key, value]]
  return []
}))
const strings = (values) => Array.isArray(values)
  ? values.slice(0, 12).filter(value => typeof value === "string").map(value => text(value, 128))
  : undefined

function snapshotEntry(entry) {
  const ai = entry.ai
  const excerpt = text(entry.content, 4096)?.replace(/<[^>]*>/g, " ").replace(/\s+/g, " ").trim()
  return {
    ...fields(entry, ["id", "title", "feed_id", "published_at", "status", "starred",
      "author", "reading_time", "coverWidth", "coverHeight"]),
    url: text(entry.url, 4096),
    coverSource: text(entry.coverSource, 4096),
    content_deferred: true,
    feed: {
      ...fields(entry.feed, ["id", "title", "icon_id"]),
      site_url: text(entry.feed?.site_url, 4096),
      feed_url: text(entry.feed?.feed_url, 4096),
      icon: fields(entry.feed?.icon, ["icon_id", "external_icon_id"]),
    },
    enclosures: Array.isArray(entry.enclosures) ? entry.enclosures.slice(0, 4).map(enclosure => ({
      ...fields(enclosure, ["id", "mime_type", "size"]),
      url: text(enclosure?.url, 4096),
    })) : undefined,
    card: { ...fields(entry.card, ["title"]), summary: text(entry.card?.summary ||
      (ai?.state === "done" ? ai.summary : null) || excerpt, 900) },
    ai: ai ? {
      ...fields(ai, ["state", "summary", "score", "technical_score", "business_score",
        "reason", "error", "has_note"]),
      tags: strings(ai.tags),
      cover_url: text(ai.cover_url, 4096),
      content_quality: ai.content_quality ? {
        ...fields(ai.content_quality, ["policy_version", "access", "information", "recommendation_eligible"]),
        reason_codes: strings(ai.content_quality.reason_codes),
      } : undefined,
      body_completeness: ai.body_completeness ? fields(ai.body_completeness, [
        "policy_version", "status", "structure", "analysis_text_matches", "reason",
        "retry_exhausted", "attempts", "next_retry_at",
      ]) : undefined,
      processing: ai.processing ? {
        ...fields(ai.processing, ["reason_code", "claim_held", "stale", "observed_at", "retry_at"]),
        reason_codes: strings(ai.processing.reason_codes),
      } : undefined,
    } : undefined,
  }
}

function makeSnapshot(owner, signature, { entries, total, offset, hasMore, aiRevision }, savedAt) {
  const snapshot = {
    owner, signature, savedAt, total, offset, hasMore: Boolean(hasMore),
    aiRevision: /^[0-9a-f]{64}$/.test(aiRevision) ? aiRevision : null,
    entries: [],
  }
  const encoder = new TextEncoder()
  let bytes = encoder.encode(JSON.stringify(snapshot)).length
  if (bytes > MAX_SNAPSHOT_BYTES) return null
  for (const entry of entries.slice(0, MAX_SNAPSHOT_ENTRIES)) {
    const card = snapshotEntry(entry)
    bytes += encoder.encode(JSON.stringify(card)).length + 1
    if (bytes > MAX_SNAPSHOT_BYTES) break
    snapshot.entries.push(card)
  }
  return snapshot
}

export function createArticleSnapshotStore(storage, now = Date.now) {
  const ownerEpochs = new Map()
  const epoch = owner => ownerEpochs.get(owner) ?? 0
  const isRecent = (snapshot, owner) => {
    if (snapshot?.owner !== owner || !Number.isFinite(snapshot.savedAt)) return false
    const age = now() - snapshot.savedAt
    return age >= 0 && age < SNAPSHOT_MAX_AGE
  }
  return {
    async read(owner, signature) {
      if (!owner) return null
      try {
        const startedAt = epoch(owner)
        const row = await storage.read(owner)
        if (startedAt !== epoch(owner)) return null
        return [row?.last, row?.default].find(snapshot =>
          isRecent(snapshot, owner) && snapshot.signature === signature) ?? null
      } catch {
        return null
      }
    },
    async save(owner, query, page) {
      if (!owner) return
      try {
        const startedAt = epoch(owner)
        const snapshot = makeSnapshot(owner, query.signature, page, now())
        if (!snapshot) return
        await storage.update(owner, row => {
          if (startedAt !== epoch(owner)) return row
          return query.isDefault
            ? { default: snapshot }
            : { default: isRecent(row?.default, owner) ? row.default : null, last: snapshot }
        })
      } catch {
        // The network remains the source of the live list if storage is unavailable.
      }
    },
    async removeOwner(owner) {
      if (!owner) return
      ownerEpochs.set(owner, epoch(owner) + 1)
      try {
        await storage.update(owner, () => null)
      } catch {
        // Storage access must never prevent session retirement.
      }
    },
  }
}

function openSnapshots() {
  return new Promise((resolve, reject) => {
    const request = globalThis.indexedDB.open("reader-article-snapshots", 1)
    let abandoned = false
    const abandon = () => {
      abandoned = true
      reject(new Error("Snapshot storage unavailable"))
    }
    const timeout = setTimeout(abandon, 750)
    request.onupgradeneeded = () => {
      try {
        request.result.createObjectStore("owners")
      } catch {
        request.transaction.abort()
      }
    }
    request.onblocked = () => {
      clearTimeout(timeout)
      abandon()
    }
    request.onerror = () => { clearTimeout(timeout); reject(request.error) }
    request.onsuccess = () => {
      clearTimeout(timeout)
      if (abandoned) request.result.close()
      else resolve(request.result)
    }
  })
}

async function transact(owner, update) {
  const db = await openSnapshots()
  try {
    return await new Promise((resolve, reject) => {
      const tx = db.transaction("owners", update ? "readwrite" : "readonly")
      const store = tx.objectStore("owners")
      const request = store.get(owner)
      tx.onabort = () => reject(tx.error)
      tx.oncomplete = () => resolve(request.result)
      if (update) {
        request.onsuccess = () => {
          try {
            const next = update(request.result)
            if (next) store.put(next, owner)
            else store.delete(owner)
          } catch {
            tx.abort()
          }
        }
      }
    })
  } finally {
    db.close()
  }
}

export const articleSnapshots = createArticleSnapshotStore({
  read: (owner) => transact(owner),
  update: (owner, update) => transact(owner, update),
})

export const isShowingArticleSnapshot = (content) => Boolean(content.articleListCache)

export function snapshotSwapAnchor(previousCache, liveReady, currentCache, anchor, entries, columns) {
  if (!previousCache || !liveReady || currentCache) return null
  const index = anchor ? entries.findIndex(entry => Number(entry.id) === anchor.entryId) : -1
  return index < 0 ? { index: 0, offset: 0 } : { index: Math.floor(index / columns), offset: -anchor.offset }
}
