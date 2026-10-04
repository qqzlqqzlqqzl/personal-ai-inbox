// ofetch 1.5.1 skips its timeout when a signal is supplied. Own both the
// deadline and cancellation, including settlement if a transport ignores abort.
export const NOTE_REQUEST_TIMEOUT = 15000

export function requestWithNoteDeadline(controller, request) {
  const { signal } = controller
  const abortReason = () => signal.reason ?? new DOMException("Note request cancelled", "AbortError")
  if (signal.aborted) return Promise.reject(abortReason())
  let onAbort
  const cancelled = new Promise((_, reject) => {
    onAbort = () => reject(abortReason())
    signal.addEventListener("abort", onAbort, { once: true })
  })
  const deadline = setTimeout(() => {
    controller.abort(new DOMException("Note request exceeded 15 seconds", "TimeoutError"))
  }, NOTE_REQUEST_TIMEOUT)
  const operation = Promise.resolve().then(() => {
    if (signal.aborted) throw abortReason()
    return request(signal)
  })
  return Promise.race([operation, cancelled]).finally(() => {
    clearTimeout(deadline)
    signal.removeEventListener("abort", onAbort)
  })
}
