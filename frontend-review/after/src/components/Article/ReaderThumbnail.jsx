import { useState } from "react"
import { readerThumbnailProps } from "./reader-image-variants"

export default function ReaderThumbnail({ entry, onError, ...props }) {
  const { originalSrc, ...variant } = readerThumbnailProps(entry, window.location.origin)
  const [failedSource, setFailedSource] = useState(null)
  const fallback = failedSource === variant.src
  return (
    <img
      {...props}
      {...(fallback ? { src: originalSrc } : variant)}
      onError={event => {
        if (!fallback && variant.src !== originalSrc) setFailedSource(variant.src)
        else onError?.(event)
      }}
    />
  )
}
