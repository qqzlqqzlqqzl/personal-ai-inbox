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
  assert.deepEqual(readerThumbnailProps(other,origin),{originalSrc:other.coverSource})
})

test("cover lookahead leaves browser slots for pages, shares variants and never repeats failed URLs", () => {
  const images=[];let settled=0
  const loader=createThumbnailPreloader(()=>{const image={};images.push(image);return image},()=>settled++)
  const entries=Array.from({length:20},(_,id)=>{
    const url=`https://example.org/cover-${id}.jpg`
    const proxy="/mf/proxy/"+"A".repeat(43)+"=/"+btoa(url)
    return {id,coverSource:url,ai:{cover_proxy_url:proxy}}
  })
  loader.warm(entries,2,320,origin,2)
  assert.equal(images.length,2)
  assert.equal(images[0].src,readerThumbnailProps(entries[3],origin).src)
  assert.equal(images[0].srcset,readerThumbnailProps(entries[3],origin).srcSet)
  assert.equal(images[0].sizes,"320px")
  loader.warm(entries,12,320,origin,2)
  assert.equal(images.length,2,"background work keeps two slots even after a fast scroll")
  for (const image of images) image.onerror()
  assert.equal(settled,2)
  loader.warm(entries,2,320,origin,2)
  assert.equal(images.length,4,"lookahead continues with new proxy URLs after failure")
  assert.equal(new Set(images.map(image=>image.src)).size,4,"failed prefetch never retries a URL")
  loader.warm(entries,12,320,origin,2)
  assert.equal(images.length,4)
  images[2].onload();images[3].onload()
  loader.warm(entries,12,320,origin,2)
  assert.equal(images.length,6)
  loader.dispose()
  loader.warm(entries,0,480,origin,1)
  assert.equal(images.length,6,"retired component cannot issue more work")
  assert.ok(images.every(image=>image.onerror===null))
  const invalid=createThumbnailPreloader(()=>{throw new Error("invalid prefetch")})
  invalid.warm([{coverSource:original},{coverSource:"javascript:alert(1)"}],0,320,origin)
  invalid.warm(entries,-1,320,origin)
  invalid.warm(entries,0,0,origin)
  invalid.dispose()
})

test("unproxied covers keep source information without display or background origin requests", () => {
  const today={more:false,entries:Array.from({length:13},(_,id)=>({id,coverSource:`https://example.org/today-${id}.jpg`}))}
  const images=[]
  const loader=createThumbnailPreloader(()=>{const image={};images.push(image);return image})
  loader.warm(today.entries,2,320,origin,2)
  assert.equal(images.length,0)
  loader.warm(today.entries,8,320,origin,2)
  loader.enqueue(today.entries,320,origin,2)
  assert.equal(images.length,0,"unbound originals never enter the background queue")
  loader.warm(today.entries,2,640,origin,1)
  assert.equal(images.length,0)
  for (const entry of today.entries) assert.deepEqual(readerThumbnailProps(entry,origin),{originalSrc:entry.coverSource})
  loader.dispose()
})

test("queued proxy covers continue without scrolling, retain two slots and never warm originals", () => {
  const images=[]
  const loader=createThumbnailPreloader(()=>{const image={};images.push(image);return image})
  const entries=Array.from({length:30},(_,id)=>{
    const url='https://example.org/queued-'+id+'.jpg'
    return {id,coverSource:url,...(id%3?{ai:{cover_proxy_url:'/mf/proxy/'+'A'.repeat(43)+'=/'+btoa(url)}}:{})}
  })
  loader.enqueue(entries,320,origin,2)
  assert.equal(images.length,2)
  loader.enqueue(entries,320,origin,2)
  for(let completed=0;completed<20;completed++){
    const active=images.filter(image=>image.onload)
    assert.ok(active.length<=2)
    assert.ok(active.every(image=>image.src.startsWith('/mf/proxy/') && image.srcset))
    assert.ok(active.length,'queued covers must continue when a previous image settles')
    if (completed%2) active[0].onload()
    else active[0].onerror()
  }
  assert.equal(images.length,20)
  assert.equal(new Set(images.map(image=>image.src)).size,20)
  loader.enqueue(entries,320,origin,2)
  assert.equal(images.length,20,"proxy errors never retry raw URLs or failed variants")
  loader.dispose()
})

test("filter retirement discards queued covers while respecting existing download slots", () => {
  const images=[]
  const loader=createThumbnailPreloader(()=>{const image={};images.push(image);return image})
  const cover=name=>{
    const coverSource='https://example.org/'+name+'.jpg'
    return {coverSource,ai:{cover_proxy_url:'/mf/proxy/'+'A'.repeat(43)+'=/'+btoa(coverSource)}}
  }
  const old=Array.from({length:13},(_,id)=>cover('old-'+id))
  loader.enqueue(old,320,origin)
  assert.equal(images.length,2)
  loader.reset()
  const next=cover('new')
  loader.enqueue([next],320,origin)
  assert.equal(images.length,2)
  images[0].onerror()
  assert.equal(images.length,3)
  assert.equal(images[2].src,readerThumbnailProps(next,origin).src)
  for (const image of images.slice(1)) image.onload()
  assert.equal(images.length,3,'old unsent covers must not restart')
  loader.dispose()
})

test("unbound external, credentialed, malformed, queried and fragment covers preserve originals without requesting them", () => {
  for (const value of [original,"//reader.example.test"+signed,"https://outside.example.test"+signed,
    "https://user:pass@reader.example.test"+signed,signed+"?reader_width=960",signed+"#fragment",
    "/mf/proxy/bad/encoded","javascript:alert(1)"]) {
    assert.deepEqual(readerThumbnailProps({coverSource:original,attachments:{images:[{url:value}]}},origin),
      {originalSrc:original})
  }
})
