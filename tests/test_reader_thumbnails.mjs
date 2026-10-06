import assert from "node:assert/strict"
import test from "node:test"
import {readerThumbnailProps, readerImageProps, createThumbnailPreloader} from "../frontend-review/after/src/components/Article/reader-image-variants.js"

const origin = "https://reader.example.test"
const signed = "/mf/proxy/" + "A".repeat(43) + "=/aHR0cHM6Ly9leGFtcGxlLm9yZy9jb3Zlci5qcGc="
const original = "https://example.org/cover.jpg"

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

test("AI cover proxy must represent the selected cover, never a different attachment", () => {
  const entry = {coverSource:original, ai:{cover_proxy_url:signed}}
  assert.equal(readerThumbnailProps(entry,origin).src,signed+"?reader_width=480")
  const other = {...entry,coverSource:"https://example.org/different.jpg",attachments:{images:[{url:signed}]}}
  assert.deepEqual(readerThumbnailProps(other,origin),{src:other.coverSource,originalSrc:other.coverSource})
})

test("cover lookahead has six slots, shared variants and no repeated requests", () => {
  const images=[];let settled=0
  const loader=createThumbnailPreloader(()=>{const image={};images.push(image);return image},()=>settled++)
  const entries=Array.from({length:20},(_,id)=>{
    const url=`https://example.org/cover-${id}.jpg`
    const proxy="/mf/proxy/"+"A".repeat(43)+"=/"+btoa(url)
    return {id,coverSource:url,ai:{cover_proxy_url:proxy}}
  })
  loader.warm(entries,2,320,origin,2)
  assert.equal(images.length,6)
  assert.equal(images[0].src,readerThumbnailProps(entries[3],origin).src)
  assert.equal(images[0].srcset,readerThumbnailProps(entries[3],origin).srcSet)
  assert.equal(images[0].sizes,"320px")
  loader.warm(entries,12,320,origin,2)
  assert.equal(images.length,6,"in-flight work is bounded even after a fast scroll")
  for (const image of images) image.onerror()
  assert.equal(settled,6)
  loader.warm(entries,2,320,origin,2)
  assert.equal(images.length,6,"failed prefetch does not loop or retry originals")
  loader.warm(entries,12,320,origin,2)
  assert.equal(images.length,12)
  loader.dispose()
  loader.warm(entries,0,480,origin,1)
  assert.equal(images.length,12,"retired component cannot issue more work")
  assert.ok(images.every(image=>image.onerror===null))
  const invalid=createThumbnailPreloader(()=>{throw new Error("invalid prefetch")})
  invalid.warm([{coverSource:original},{coverSource:"javascript:alert(1)"}],0,320,origin)
  invalid.warm(entries,-1,320,origin)
  invalid.warm(entries,0,0,origin)
  invalid.dispose()
})

test("complete Today list warms only its chosen raw covers, at most two in flight", () => {
  const today={more:false,entries:Array.from({length:13},(_,id)=>({id,coverSource:`https://example.org/today-${id}.jpg`}))}
  const images=[]
  const loader=createThumbnailPreloader(()=>{const image={};images.push(image);return image})
  loader.warm(today.entries,2,320,origin,2)
  assert.deepEqual(images.map(image=>image.src),today.entries.slice(3,5).map(entry=>entry.coverSource))
  assert.ok(images.every(image=>image.fetchPriority==='low' && image.srcset===undefined))
  loader.warm(today.entries,8,320,origin,2)
  assert.equal(images.length,2,"raw work cannot overwhelm the other four image slots")
  images[0].onerror();images[1].onload()
  loader.warm(today.entries,2,640,origin,1)
  assert.deepEqual(images.slice(2).map(image=>image.src),today.entries.slice(5,7).map(entry=>entry.coverSource))
  assert.equal(images.length,4,"same raw URL is not retried after failure or width changes")
  loader.dispose()
})

test("queued covers continue without scrolling, retain six/raw-two limits and deduplicate", () => {
  const images=[]
  const loader=createThumbnailPreloader(()=>{const image={};images.push(image);return image})
  const entries=Array.from({length:30},(_,id)=>{
    const url='https://example.org/queued-'+id+'.jpg'
    return {id,coverSource:url,...(id%3?{ai:{cover_proxy_url:'/mf/proxy/'+'A'.repeat(43)+'=/'+btoa(url)}}:{})}
  })
  loader.enqueue(entries,320,origin,2)
  assert.equal(images.length,6)
  loader.enqueue(entries,320,origin,2)
  for(let completed=0;completed<30;completed++){
    const active=images.filter(image=>image.onload)
    assert.ok(active.length<=6)
    assert.ok(active.filter(image=>!image.srcset).length<=2)
    assert.ok(active.length,'queued covers must continue when a previous image settles')
    active[0].onload()
  }
  assert.equal(images.length,30)
  assert.equal(new Set(images.map(image=>image.src)).size,30)
  loader.enqueue(entries,320,origin,2)
  assert.equal(images.length,30)
  loader.dispose()
})

test("filter retirement discards queued covers while respecting existing download slots", () => {
  const images=[]
  const loader=createThumbnailPreloader(()=>{const image={};images.push(image);return image})
  const old=Array.from({length:13},(_,id)=>({coverSource:'https://example.org/old-'+id+'.jpg'}))
  loader.enqueue(old,320,origin)
  assert.equal(images.length,2)
  loader.reset()
  loader.enqueue([{coverSource:'https://example.org/new.jpg'}],320,origin)
  assert.equal(images.length,2)
  images[0].onerror()
  assert.equal(images.length,3)
  assert.equal(images[2].src,'https://example.org/new.jpg')
  images[1].onload();images[2].onload()
  assert.equal(images.length,3,'old unsent covers must not restart')
  loader.dispose()
})

test("external, credentialed, malformed, queried and fragment URLs remain untouched", () => {
  for (const value of [original,"//reader.example.test"+signed,"https://outside.example.test"+signed,
    "https://user:pass@reader.example.test"+signed,signed+"?reader_width=960",signed+"#fragment",
    "/mf/proxy/bad/encoded","javascript:alert(1)"]) {
    assert.deepEqual(readerThumbnailProps({coverSource:original,attachments:{images:[{url:value}]}},origin),
      {src:original,originalSrc:original})
  }
})
