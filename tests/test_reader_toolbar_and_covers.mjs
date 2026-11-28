import assert from "node:assert/strict"
import { readFileSync } from "node:fs"
import test from "node:test"

import { initialStatusPopover, isStatusPopoverOpen, updateStatusPopover } from "../frontend-review/after/src/components/Ai/status-popover.js"
import { entryCardSummary } from "../frontend-review/after/src/components/Article/card-summary.js"
import {
  AI_COVER_PREFETCH_SLOTS, canPrefetchAiCovers, createThumbnailPreloader,
  firstAiCoverEntries, readerThumbnailProps,
} from "../frontend-review/after/src/components/Article/reader-image-variants.js"

const popover = (...actions) => isStatusPopoverOpen(actions.reduce(updateStatusPopover, initialStatusPopover))

test("desktop badge previews on hover and keyboard focus", () => {
  assert.equal(popover(), false)
  assert.equal(popover("enter"), true)
  assert.equal(popover("enter", "leave"), false)
  assert.equal(popover("focus"), true)
  assert.equal(popover("focus", "enter", "leave"), true)
  assert.equal(popover("focus", "blur"), false)
})

test("desktop badge toggles and Escape dismisses it until a new interaction", () => {
  assert.equal(popover("toggle", "leave"), true)
  assert.equal(popover("toggle", "toggle"), false)
  assert.equal(popover("enter", "toggle"), false)
  assert.equal(popover("focus", "dismiss"), false)
  assert.equal(popover("enter", "dismiss", "leave", "enter"), true)
  assert.equal(popover("focus", "dismiss", "blur", "focus"), true)
  const toolbar = readFileSync(new URL("../frontend-review/after/src/components/Ai/AiToolbar.jsx", import.meta.url), "utf8")
  assert.match(toolbar, /aria-label=\{"运行状态：" \+ label\} aria-controls=\{id\} aria-expanded=\{expanded\}/)
  assert.match(toolbar, /className="ai-status-popover" hidden=\{!expanded\}/)
})

test("normal verified cards keep the original summary priority", () => {
  const ai = {
    state: "done", summary: "AI summary", content_source: "original_url_site_rule", source_chars: 1200,
    body_completeness: { status: "verified" },
  }
  assert.equal(entryCardSummary({ ai, card: { summary: "Translated summary" } }, "Body preview"), "Translated summary")
  assert.equal(entryCardSummary({ ai }, "Body preview"), "AI summary")
  assert.equal(entryCardSummary({ ai: { ...ai, state: "pending" } }, "Body preview"), "Body preview")
  assert.equal(entryCardSummary({ card: { summary: "Translated summary" } }, ""), "Translated summary")
  assert.equal(entryCardSummary({}, "Native preview"), "Native preview")
})

test("a valid translated or AI summary survives teaser metadata for the body", () => {
  const ai = { state: "done", summary: "AI summary", body_completeness: { status: "incomplete" } }
  assert.equal(entryCardSummary({ ai, card: { summary: "Translation" } }, "A short teaser"), "Translation")
  assert.equal(entryCardSummary({ ai }, "A short teaser"), "AI summary")
})

test("blank bases, teasers and placeholder links use the trimmed AI summary in any state", () => {
  const ai = { state: "pending", summary: "  An AI summary.  " }
  assert.equal(entryCardSummary({ ai }, " "), "An AI summary.")
  assert.equal(entryCardSummary({ ai, card: { summary: "  " } }, "Normal preview"), "An AI summary.")
  assert.equal(entryCardSummary({ ai, card: { summary: "【查看原文】 →" } }, "Normal preview"), "An AI summary.")
  for (const placeholder of ["点击查看原文", "查看原文！ ↗", "Read more →"]) {
    const content = `<p><a href="https://example.test/"><b>${placeholder}</b></a></p>`
    assert.equal(entryCardSummary({ ai, content }, placeholder), "An AI summary.")
    assert.equal(entryCardSummary({ ai, content: "<p>A short introduction.</p>" + content }, "A short introduction." + placeholder), "An AI summary.")
  }
  assert.equal(entryCardSummary({ ai: { ...ai, body_completeness: { status: "incomplete" } } }, "An excerpt"), "An AI summary.")
  assert.equal(entryCardSummary({ ai: { ...ai, content_source: "original_url_site_rule", source_chars: 1200 } }, "An excerpt"), "An AI summary.")
})

test("a placeholder base with no usable AI summary yields no summary line", () => {
  assert.equal(entryCardSummary({ ai: { state: "done" } }, "Preview"), "")
  assert.equal(entryCardSummary({ card: { summary: "请点击查看原文了解详情" } }, "Preview"), "")
  assert.equal(entryCardSummary({ content: '<p>An excerpt <a href="/">查看原文</a></p>' }, "An excerpt 查看原文"), "")
  for (const summary of [null, undefined, 0, " \n ", "点击查看原文", "详情请点击查看原文。", "查看原文 ↗"]) {
    assert.equal(entryCardSummary({ ai: { summary } }, ""), "")
  }
  assert.equal(entryCardSummary({}, "可在订阅页面查看原文与相关资料。"), "可在订阅页面查看原文与相关资料。")
})

const origin = "https://reader.example.test"
const entries = Array.from({ length: 24 }, (_, index) => ({
  coverSource: `/mf/proxy/${"A".repeat(43)}=/${btoa(`https://example.test/cover-${index}.jpg`)}`,
}))

function createCoverFixture(canStart = () => true) {
  const images = []
  const timers = new Set()
  const queue = createThumbnailPreloader(() => {
    const image = { removeAttribute(name) { delete this[name] } }
    images.push(image)
    return image
  }, undefined, {
    slots: AI_COVER_PREFETCH_SLOTS, canStart,
    setTimer: task => { timers.add(task); return task },
    clearTimer: task => timers.delete(task),
  })
  return { images, timers, queue }
}

test("cover warmup skips data saver and 2G connections", () => {
  for (const connection of [{ saveData: true }, { effectiveType: "slow-2g" }, { effectiveType: "2g" }]) {
    const { images, queue } = createCoverFixture(() => canPrefetchAiCovers(connection))
    queue.enqueue(firstAiCoverEntries(entries), 340, origin, 2)
    assert.equal(images.length, 0)
    queue.dispose()
  }
  for (const connection of [undefined, {}, { saveData: false, effectiveType: "3g" }, { effectiveType: "4g" }]) {
    assert.equal(canPrefetchAiCovers(connection), true)
  }
})

test("at most two cover requests run at once and only the first 20 picks are offered", () => {
  const { images, queue } = createCoverFixture()
  queue.enqueue(firstAiCoverEntries(entries), 340, origin, 2)
  for (let index = 0; index < 20; index++) {
    const active = images.filter(image => image.onload)
    assert.equal(active.length, Math.min(2, 20 - index))
    const image = active[0]
    const thumbnail = readerThumbnailProps(entries[index], origin)
    assert.equal(image.src, thumbnail.src)
    assert.equal(image.srcset, thumbnail.srcSet)
    assert.equal(image.sizes, "340px")
    assert.equal(image.fetchPriority, "low")
    image.onload()
  }
  assert.equal(images.length, 20)
  queue.dispose()
})

test("a view or session change retires queued and active covers", () => {
  let current = true
  const { images, timers, queue } = createCoverFixture(() => current)
  queue.enqueue(firstAiCoverEntries(entries), 340, origin, 2)
  current = false
  images[0].onload()
  assert.equal(images.length, 2, "stale completions cannot move the queue forward")
  const lateLoad = images[1].onload
  queue.dispose()
  assert.equal(images[1].src, undefined)
  assert.equal(images[1].srcset, undefined)
  assert.equal(images[1].onload, null)
  assert.equal(timers.size, 0)
  current = true
  lateLoad()
  queue.enqueue(entries, 340, origin, 2)
  assert.equal(images.length, 2, "a retired queue cannot restart")
})
