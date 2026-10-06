// Display URLs only. Never mutate the HTML source used by the original gallery.
const SIGNED_PROXY = /^\/mf\/proxy\/[A-Za-z0-9_-]{43}=\/[A-Za-z0-9_=-]{1,8192}$/
// Keep resized natural width above the existing 768px big-image threshold.
const WIDTHS = [960, 1600]
const THUMBNAIL_PREFETCH_SLOTS = 2

export function readerImageProps(props, viewport = 768, ratio = 1, origin) {
  let path = props.src
  if (typeof path !== "string" || props.srcSet) return props
  if (/^https?:\/\//.test(path)) {
    let url
    try { url = new URL(path) } catch { return props }
    if (url.origin !== origin || url.username || url.password || url.search || url.hash) return props
    path = url.pathname
  }
  if (!SIGNED_PROXY.test(path)) return props
  const knownWidth = Number(props.width)
  if (knownWidth > 0 && knownWidth <= 480) return props
  const cssWidth = Math.min(768, Math.max(1, Number.isFinite(viewport) ? viewport - 32 : 768))
  const density = Math.min(3, Math.max(1, Number.isFinite(ratio) ? ratio : 1))
  const width = WIDTHS.find(value => value >= cssWidth * density) ?? 1600
  return {...props, src: `${props.src}?reader_width=${width}`}
}

// Reuse native signed covers; body and original preview URLs stay untouched.
function signedPath(value, origin) {
  if (typeof value !== "string") return null
  let path = value
  if (/^https?:\/\//.test(value)) {
    let url
    try { url = new URL(value) } catch { return null }
    if (url.origin !== origin || url.username || url.password || url.search || url.hash) return null
    path = url.pathname
  }
  return SIGNED_PROXY.test(path) ? path : null
}

function sameCover(path, original) {
  try {
    const encoded = path.split('/').at(-1).replace(/-/g, '+').replace(/_/g, '/')
    const target = new TextDecoder('utf-8', {fatal: true}).decode(
      Uint8Array.from(atob(encoded), char => char.charCodeAt(0)),
    )
    return new URL(target).href === new URL(original).href
  } catch { return false }
}

export function readerThumbnailProps(entry, origin) {
  const originalSrc = entry.coverSource
  const coverProxy = signedPath(entry.ai?.cover_proxy_url, origin)
  const image = entry.attachments?.images?.find(item => {
    const path = signedPath(item.url, origin)
    return path && sameCover(path, originalSrc)
  })
  const path = signedPath(originalSrc, origin) ||
    (coverProxy && sameCover(coverProxy, originalSrc) ? coverProxy : null) ||
    signedPath(image?.url, origin)
  if (!path) return {originalSrc}
  return {
    src: `${path}?reader_width=480`,
    srcSet: [480, 960, 1600].map(width => `${path}?reader_width=${width} ${width}w`).join(", "),
    sizes: "auto, (max-width: 768px) calc(100vw - 32px), 480px",
    originalSrc,
  }
}

// Queue already-loaded covers using the mounted card's width and shared slots.
export function createThumbnailPreloader(createImage, onSettled = () => {}) {
  const seen = new Set(), pending = new Set(), pendingKeys = new Set()
  const queuedKeys = new Set()
  let queue = []
  let closed = false
  const drain = () => {
    while (!closed && pending.size < THUMBNAIL_PREFETCH_SLOTS && queue.length) {
      const item = queue.shift()
      queuedKeys.delete(item.key)
      loader.warm([null, item.entry], 0, item.width, item.origin, item.ratio)
    }
  }
  const loader = {
    enqueue(entries, width, origin, ratio = 1) {
      if (closed || !Number.isFinite(width) || width <= 0) return
      const candidate = [480, 960, 1600].find(value => value >= width * ratio) ?? 1600
      for (const entry of entries) {
        const props = readerThumbnailProps(entry, origin)
        if (!props.srcSet) continue
        const key = props.srcSet + ':' + candidate
        if (seen.has(key) || queuedKeys.has(key)) continue
        queuedKeys.add(key)
        queue.push({entry, key, width, origin, ratio})
      }
      drain()
    },
    reset() {
      // Retire work not yet started; existing downloads keep the shared slots.
      queue = []
      queuedKeys.clear()
    },
    warm(entries, lastVisible, width, origin, ratio = 1) {
      if (closed || lastVisible < 0 || !Number.isFinite(width) || width <= 0) return
      const candidate = [480, 960, 1600].find(value => value >= width * ratio) ?? 1600
      for (const entry of entries.slice(lastVisible + 1, lastVisible + 7)) {
        if (pending.size >= THUMBNAIL_PREFETCH_SLOTS) break
        const props = readerThumbnailProps(entry, origin)
        if (!props.srcSet) continue
        const key = `${props.srcSet}:${candidate}`
        if (seen.has(key)) continue
        const image = createImage()
        seen.add(key)
        pending.add(image)
        pendingKeys.add(key)
        if (seen.size > 128) {
          const oldest = [...seen].find(value => !pendingKeys.has(value))
          if (oldest) seen.delete(oldest)
        }
        image.onload = image.onerror = () => {
          image.onload = image.onerror = null
          pending.delete(image)
          pendingKeys.delete(key)
          if (!closed) { onSettled(); drain() }
        }
        image.decoding = 'async'
        image.fetchPriority = 'low'
        image.sizes = `${width}px`
        image.srcset = props.srcSet
        image.src = props.src
      }
    },
    dispose() {
      closed = true
      queue = []
      queuedKeys.clear()
      for (const image of pending) image.onload = image.onerror = null
      pending.clear()
      pendingKeys.clear()
      seen.clear()
    },
  }
  return loader
}
