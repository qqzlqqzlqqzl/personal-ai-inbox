// Diagnostic only: rAF and DOM observations do not prove a completed paint.
// Keep scope/count/ids byte-for-byte equivalent to the original gate inputs.
(() => {
  const limit = 2400;
  window.queryFrames = [];
  window.queryObservation = { dropped: 0, sequence: 0, raf: 0 };
  const rect = value => ({ left: value.left, top: value.top,
    right: value.right, bottom: value.bottom, width: value.width, height: value.height });
  const geometry = element => {
    if (!element) return null;
    const bounds = element.getBoundingClientRect();
    let left = Math.max(0, bounds.left), top = Math.max(0, bounds.top);
    let right = Math.min(innerWidth, bounds.right), bottom = Math.min(innerHeight, bounds.bottom);
    let opacity = 1, styleVisible = true;
    const clips = [];
    for (let node = element; node instanceof Element; node = node.parentElement) {
      const style = getComputedStyle(node);
      opacity *= Number(style.opacity || 1);
      styleVisible &&= style.display !== 'none' && !['hidden', 'collapse'].includes(style.visibility);
      if (node === element) continue;
      const clipX = /^(auto|scroll|hidden|clip)$/.test(style.overflowX);
      const clipY = /^(auto|scroll|hidden|clip)$/.test(style.overflowY);
      if (!clipX && !clipY) continue;
      const box = node.getBoundingClientRect();
      const scaleX = node.offsetWidth ? box.width / node.offsetWidth : 1;
      const scaleY = node.offsetHeight ? box.height / node.offsetHeight : 1;
      const x = box.left + node.clientLeft * scaleX, y = box.top + node.clientTop * scaleY;
      const edgeX = x + node.clientWidth * scaleX, edgeY = y + node.clientHeight * scaleY;
      clips.push({ tag: node.tagName, class: String(node.className), clipX, clipY,
        left: x, top: y, right: edgeX, bottom: edgeY });
      if (clipX) { left = Math.max(left, x); right = Math.min(right, edgeX); }
      if (clipY) { top = Math.max(top, y); bottom = Math.min(bottom, edgeY); }
    }
    const intersection = { left, top, right, bottom,
      width: Math.max(0, right - left), height: Math.max(0, bottom - top) };
    const hasArea = intersection.width > 0 && intersection.height > 0;
    const hit = hasArea ? document.elementFromPoint?.((left + right) / 2, (top + bottom) / 2) : null;
    return { connected: element.isConnected, bounds: rect(bounds), intersection,
      opacity, styleVisible, hasArea, centerHitInside: hit ? element.contains(hit) : null, clips };
  };
  const scopeForLabel = label => label === '全部' ? 'all' : label === '今天' ? 'today' : null;
  window.queryFrame = (kind = 'manual', rafTime = null, mutationCount = null) => {
    const selected = [...document.querySelectorAll('.arco-menu-selected .custom-menu-item')].find(e => e.getClientRects().length);
    const rows = [...document.querySelectorAll('.entry-list [data-entry-id]')].filter(e => e.getClientRects().length);
    const count = selected?.querySelector('.item-count');
    const label = selected?.firstElementChild?.textContent.trim() ?? null;
    const headers = [...document.querySelectorAll('.page-info')].filter(e => e.getClientRects().length);
    const item = { scope: location.pathname.split('/')[2], count: count?.textContent.trim() ?? '', ids: rows.map(e => e.dataset.entryId),
      sequence: ++window.queryObservation.sequence, kind, time: performance.now(), rafTime, mutationCount,
      location: { pathname: location.pathname, search: location.search, hash: location.hash },
      dom: { selected_label: label, selected_scope: scopeForLabel(label),
        selected_href: selected?.closest('a')?.getAttribute('href') ?? null,
        selected_aria: selected?.closest('[aria-selected]')?.getAttribute('aria-selected') ?? null,
        count_rendered_text: count?.innerText?.trim() ?? null,
        selected_geometry: geometry(selected), count_geometry: geometry(count),
        headers: headers.map(e => ({ text: e.textContent.trim(), rendered_text: e.innerText?.trim() ?? null, geometry: geometry(e) })),
        rows: rows.map(e => ({ id: e.dataset.entryId, geometry: geometry(e) })) },
      viewport: { width: innerWidth, height: innerHeight, visibility: document.visibilityState },
      geometry_model: 'axis-aligned client-box clipping; center hit only; no full occlusion or paint proof' };
    if (window.queryFrames.length < limit) window.queryFrames.push(item);
    else window.queryObservation.dropped += 1;
    return item;
  };
  let observer = null, stopped = false, started = false;
  const start = () => {
    if (started || stopped) return;
    started = true;
    observer = new MutationObserver(records => {
      window.queryFrame('mutation', null, records.length);
      requestAnimationFrame(time => { if (!stopped) window.queryFrame('mutation-raf', time); });
    });
    observer.observe(document.body, { subtree: true, childList: true, attributes: true, characterData: true });
    const next = time => { if (stopped) return; window.queryObservation.raf += 1; window.queryFrame('raf', time); requestAnimationFrame(next); };
    requestAnimationFrame(next);
  };
  window.stopQueryObservation = () => { stopped = true; observer?.disconnect(); document.removeEventListener('DOMContentLoaded', start); };
  document.addEventListener('DOMContentLoaded', start, { once: true });
})();
