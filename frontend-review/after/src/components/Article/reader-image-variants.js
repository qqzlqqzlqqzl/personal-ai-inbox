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
