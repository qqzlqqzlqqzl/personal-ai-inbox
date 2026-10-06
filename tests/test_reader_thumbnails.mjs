import assert from "node:assert/strict"
import test from "node:test"
import {readerThumbnailProps, readerImageProps} from "../frontend-review/after/src/components/Article/reader-image-variants.js"

const origin = "https://reader.example.test"
const signed = "/mf/proxy/" + "A".repeat(43) + "=/aHR0cHM6Ly9leGFtcGxlLm9yZy9jb3Zlci5qcGc="
const original = "https://images.example.test/cover.jpg"

test("native enclosure provides responsive thumbnails without mutating article or preview", () => {
  const entry = {coverSource:original, attachments:{images:[{url:signed}]}}
  const before = JSON.stringify(entry)
  const result = readerThumbnailProps(entry, origin)
  assert.equal(result.src, signed+"?reader_width=480")
  assert.equal(result.originalSrc, original)
  assert.equal(result.srcSet, [480,960,1600].map(w=>signed+"?reader_width="+w+" "+w+"w").join(", "))
  assert.match(result.sizes, /^auto, /)
  assert.equal(JSON.stringify(entry),before)
  assert.equal(readerImageProps({src:signed},390,1,origin).src,signed+"?reader_width=960")
  assert.equal(readerImageProps({src:signed},390,3,origin).src,signed+"?reader_width=1600")
})

test("existing signed cover wins and same-origin absolute URL becomes a display path", () => {
  const other = signed.replace("A".repeat(43),"B".repeat(43))
  const result = readerThumbnailProps({coverSource:origin+signed,attachments:{images:[{url:other}]}},origin)
  assert.equal(result.src,signed+"?reader_width=480")
  assert.equal(result.originalSrc,origin+signed)
})

test("external, credentialed, malformed, queried and fragment URLs remain untouched", () => {
  for (const value of [original,"//reader.example.test"+signed,"https://outside.example.test"+signed,
    "https://user:pass@reader.example.test"+signed,signed+"?reader_width=960",signed+"#fragment",
    "/mf/proxy/bad/encoded","javascript:alert(1)"]) {
    assert.deepEqual(readerThumbnailProps({coverSource:original,attachments:{images:[{url:value}]}},origin),
      {src:original,originalSrc:original})
  }
})
