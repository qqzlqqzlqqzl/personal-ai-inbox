import { useEffect, useState } from "react"
import { readerThumbnailProps } from "./reader-image-variants"

export default function ReaderThumbnail({ entry, onError, ...props }) {
  const thumbnail = readerThumbnailProps(entry, window.location.origin)
  const variant = { src: thumbnail.src, srcSet: thumbnail.srcSet, sizes: thumbnail.sizes }
  const [failedSource, setFailedSource] = useState(null)
  useEffect(() => {
    if (!variant.src) onError?.()
  }, [variant.src, onError])
  if (!variant.src || failedSource === variant.src) return null
  return (
    <img
      {...props}
      {...variant}
      onError={event => {
        setFailedSource(variant.src)
        onError?.(event)
      }}
    />
  )
}
