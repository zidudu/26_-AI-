(body) => {
  // Read-only geometry. Relative to the body, so iframe/top-page scrolling
  // does not change the coordinate conversion performed by Python.
  const origin = body.getBoundingClientRect().top;
  const intervals = [];
  let limited = false;
  const add = (rect, kind) => {
    if (rect.width > 0 && rect.height > 0) {
      if (intervals.length >= 6000) { limited = true; return; }
      intervals.push({top: rect.top - origin, bottom: rect.bottom - origin, kind});
    }
  };
  for (const element of body.querySelectorAll('img,video,iframe,canvas,svg')) {
    add(element.getBoundingClientRect(), 'media');
  }
  const walker = document.createTreeWalker(body, NodeFilter.SHOW_TEXT);
  let node;
  while ((node = walker.nextNode()) && !limited) {
    if (!node.textContent.trim() || node.parentElement.closest('script,style,noscript')) continue;
    const range = document.createRange();
    range.selectNodeContents(node);
    for (const rect of range.getClientRects()) add(rect, 'text');
  }
  return {intervals, limited};
}
