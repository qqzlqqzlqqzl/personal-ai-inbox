// Display URLs only. Never mutate the HTML source used by the original gallery.
const SIGNED_PROXY = /^\/mf\/proxy\/[A-Za-z0-9_-]{43}=\/[A-Za-z0-9_=-]{1,8192}$/
// Keep resized natural width above the existing 768px big-image threshold.
const WIDTHS = [960, 1600]

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
  if (!path) return {src: originalSrc, originalSrc}
  return {
    src: `${path}?reader_width=480`,
    srcSet: [480, 960, 1600].map(width => `${path}?reader_width=${width} ${width}w`).join(", "),
    sizes: "auto, (max-width: 768px) calc(100vw - 32px), 480px",
    originalSrc,
  }
}

// Warm only the next few already-loaded covers, using the mounted card's width.
export function createThumbnailPreloader(createImage, onSettled = () => {}) {
  const seen = new Set(), pending = new Set(), pendingKeys = new Set()
  let closed = false, rawPending = 0
  return {
    warm(entries, lastVisible, width, origin, ratio = 1) {
      if (closed || lastVisible < 0 || !Number.isFinite(width) || width <= 0) return
      const candidate = [480, 960, 1600].find(value => value >= width * ratio) ?? 1600
      for (const entry of entries.slice(lastVisible + 1, lastVisible + 7)) {
        if (pending.size >= 6) break
        const props = readerThumbnailProps(entry, origin)
        const raw = !props.srcSet
        if (raw) {
          if (rawPending >= 2 || typeof props.src !== 'string' || !props.src) continue
          let url
          try { url = new URL(props.src, origin) } catch { continue }
          if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password) continue
        }
        const key = raw ? props.src : `${props.srcSet}:${candidate}`
        if (seen.has(key)) continue
        const image = createImage()
        seen.add(key)
        pending.add(image)
        if (raw) rawPending++
        pendingKeys.add(key)
        if (seen.size > 128) {
          const oldest = [...seen].find(value => !pendingKeys.has(value))
          if (oldest) seen.delete(oldest)
        }
        image.onload = image.onerror = () => {
          image.onload = image.onerror = null
          pending.delete(image)
          pendingKeys.delete(key)
          if (raw) rawPending--
          if (!closed) onSettled()
        }
        image.decoding = 'async'
        image.fetchPriority = 'low'
        if (!raw) {
          image.sizes = `${width}px`
          image.srcset = props.srcSet
        }
        image.src = props.src
      }
    },
    dispose() {
      closed = true
      for (const image of pending) image.onload = image.onerror = null
      pending.clear()
      pendingKeys.clear()
      seen.clear()
    },
  }
}
