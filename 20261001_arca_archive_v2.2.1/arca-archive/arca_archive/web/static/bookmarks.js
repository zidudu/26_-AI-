// 북마크는 DB 응답이 성공했을 때만 표시를 바꿉니다. 같은 항목의 버튼을 함께 갱신합니다.
(function () {
  "use strict";
  var states = new Map(), pending = new Set(), noticeTimer, refreshController;
  function key(button) { return button.dataset.bookmarkKind + ":" + button.dataset.bookmarkId; }
  function buttons() { return Array.from(document.querySelectorAll("button[data-bookmark-kind]")); }
  function draw(button) {
    if (!button.dataset.bookmarkId) return;
    var k = key(button), saved = states.get(k) === true, label = button.dataset.bookmarkLabel || "미디어";
    button.setAttribute("aria-pressed", String(saved));
    button.setAttribute("aria-label", label + " 북마크 " + (saved ? "해제" : "추가"));
    button.title = button.getAttribute("aria-label");
    button.disabled = pending.has(k);
    button.textContent = (saved ? "★ " : "☆ ") + label + " 북마크";
  }
  function register() {
    buttons().forEach(function (button) {
      if (button.dataset.bookmarkId && !pending.has(key(button))) {
        states.set(key(button), button.getAttribute("aria-pressed") === "true");
      }
    });
    buttons().forEach(draw);
  }
  function notify(message, error) {
    var notice = document.getElementById("bookmark-notice");
    if (!notice) return;
    clearTimeout(noticeTimer);
    notice.textContent = message;
    notice.classList.toggle("error", !!error);
    notice.hidden = false;
    noticeTimer = setTimeout(function () { notice.hidden = true; }, error ? 8000 : 4000);
  }
  async function refreshResults() {
    var results = document.getElementById("bookmark-results");
    if (!results) return;
    if (refreshController) refreshController.abort();
    var controller = new AbortController();
    refreshController = controller;
    var timer = setTimeout(function () { controller.abort(); }, 15000);
    try {
      var response = await fetch(location.pathname + location.search, {cache: "no-store", signal: controller.signal});
      if (!response.ok) throw new Error("목록 갱신 실패");
      var html = new DOMParser().parseFromString(await response.text(), "text/html");
      var next = html.getElementById("bookmark-results");
      if (!next) throw new Error("목록 갱신 실패");
      if (refreshController !== controller) return;
      var restoreFocus = results.contains(document.activeElement);
      results.replaceWith(next);
      register();
      document.dispatchEvent(new CustomEvent("bookmarkresultsupdated"));
      if (restoreFocus && !document.querySelector("#viewer.open")) {
        var focus = next.querySelector("button, a") || document.querySelector(".bookmark-filters button");
        if (focus) focus.focus({preventScroll: true});
      }
    } catch (error) {
      if (refreshController === controller) notify("북마크는 반영됐습니다. 목록 갱신에 실패해 새로고침이 필요합니다.", true);
    } finally { clearTimeout(timer); }
  }
  register();
  window.ArchiveBookmarks = {
    bind: function (button, kind, id, label) {
      button.dataset.bookmarkKind = kind;
      button.dataset.bookmarkId = id || "";
      button.dataset.bookmarkLabel = label;
      button.hidden = !id;
      draw(button);
    }
  };
  document.addEventListener("click", async function (event) {
    var button = event.target.closest("button[data-bookmark-kind]");
    if (!button || !button.dataset.bookmarkId) return;
    event.preventDefault();
    var k = key(button);
    if (pending.has(k)) return;
    var kind = button.dataset.bookmarkKind, id = button.dataset.bookmarkId, saved = !states.get(k);
    pending.add(k);
    buttons().forEach(draw);
    var controller = new AbortController();
    var timer = setTimeout(function () { controller.abort(); }, 15000);
    try {
      var response = await fetch("/api/bookmarks/" + kind + "/" + id, {
        method: "PUT", headers: {"Content-Type": "application/json"}, body: JSON.stringify({saved: saved}),
        signal: controller.signal
      });
      if (!response.ok) throw new Error(response.status === 404 ? "항목을 찾을 수 없습니다. 새로고침 후 확인해 주세요." : "북마크 변경에 실패했습니다. 다시 시도해 주세요.");
      var data = await response.json();
      if (data.saved !== saved) throw new Error("북마크 결과를 확인하지 못했습니다. 새로고침 후 확인해 주세요.");
      states.set(k, data.saved);
      notify((kind === "article" ? "글" : "미디어") + " 북마크를 " + (saved ? "추가했습니다." : "해제했습니다."));
      buttons().forEach(draw);
      refreshResults();
    } catch (error) {
      notify(error.name === "AbortError" ? "응답이 늦어 결과를 확인하지 못했습니다. 새로고침 후 확인해 주세요." : (error.message || "북마크 변경에 실패했습니다."), true);
    } finally {
      clearTimeout(timer);
      pending.delete(k);
      buttons().forEach(draw);
    }
  });
})();
