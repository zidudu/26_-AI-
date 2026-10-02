// 실행 상태 폴링: 배너·중지 버튼을 서버 상태에 맞춰 갱신합니다.
(function () {
  var box = document.getElementById("live-status");
  if (!box) return;
  var stopBtn = document.getElementById("stop-btn");

  function pad(n) { return (n < 10 ? "0" : "") + n; }
  function elapsed(sec) {
    if (sec == null) return "";
    var m = Math.floor(sec / 60), s = sec % 60;
    return m > 0 ? m + "분 " + pad(s) + "초" : s + "초";
  }
  function kst(iso) {
    if (!iso) return "";
    var d = new Date(iso);
    if (isNaN(d)) return iso;
    return pad(d.getMonth() + 1) + "-" + pad(d.getDate()) + " " + pad(d.getHours()) + ":" + pad(d.getMinutes());
  }
  function set(cls, head, detail) {
    box.className = "status " + cls;
    box.innerHTML = "";
    var h = document.createElement("div"); h.className = "status-head"; h.textContent = head;
    var d = document.createElement("div"); d.className = "status-detail"; d.textContent = detail || "";
    box.appendChild(h); box.appendChild(d);
  }

  function refresh() {
    fetch("/api/status").then(function (r) { return r.json(); }).then(function (s) {
      var run = s.run || {}, st = run.stats || {};
      var detail = "";
      if (s.running && s.current) {
        detail = "run #" + (run.id || "?") + " · 단계 " + (run.stage || "-") + " · 경과 " + elapsed(s.elapsed_seconds) +
          " · 목록 " + (st.pages_scanned || 0) + "p · 신규 " + (st.discovered_new || 0) + " · 수집 " + (st.collected || 0) +
          "/" + (st.collect_targets || 0) + " · 갱신 " + (st.updated || 0) + " · 실패 " + (st.failed || 0) +
          " · 미디어 " + (st.media_downloaded || 0) + "/" + (st.media_targets || 0) +
          " · 기존 미수집 복구 " + (st.media_existing_downloaded || 0) + " · 새 파일 " + (st.media_new_downloaded || 0);
        if (s.stopping) {
          set("stopping", "■ 중지 중 — " + s.current.channel_slug + " (진행 중인 요청이 끝나면 멈춥니다)", detail);
        } else {
          set("running", "● 실행 중 — " + s.current.channel_slug, detail);
        }
      } else {
        var parts = [];
        if (s.queue && s.queue.length) parts.push("대기열 " + s.queue.map(function (q) { return q.channel_slug; }).join(", "));
        if (s.next_due) parts.push("다음 예정 " + kst(s.next_due.at) + " " + s.next_due.channel_slug + (s.next_due.trigger === "backlog" ? " (대기 처리)" : " (글 수집)"));
        else if (!s.scheduler_enabled) parts.push("스케줄러 꺼짐");
        if (s.last_run) parts.push("마지막 실행 #" + s.last_run.id + " " + s.last_run.channel_slug + " " + s.last_run.status +
          (s.last_run.code && s.last_run.code !== "OK" ? " (" + s.last_run.code + ")" : "") +
          " · 수집 " + s.last_run.collected + " · 미디어 " + s.last_run.media_downloaded);
        if (s.last_error) parts.push("오류: " + s.last_error);
        set("idle", "○ 대기 중", parts.join(" · "));
      }
      if (stopBtn) {
        stopBtn.disabled = !s.running || s.stopping;
        stopBtn.textContent = s.stopping ? "중지 중…" : "실행 중지";
      }
      var wait = s.running ? (s.stopping ? 1000 : 3000) : (s.queue && s.queue.length ? 3000 : 15000);
      setTimeout(refresh, wait);
    }).catch(function () { setTimeout(refresh, 10000); });
  }
  refresh();
})();


// 화면 안 미디어 뷰어: 새 창 대신 오버레이로 크게 보기. 좌우 화살표 이전/다음, Esc 또는 바깥 클릭으로 닫기.
(function () {
  function links() { return Array.from(document.querySelectorAll("a[data-viewer]")); }
  var viewer = document.createElement("div");
  viewer.id = "viewer";
  viewer.setAttribute("role", "dialog");
  viewer.setAttribute("aria-modal", "true");
  viewer.setAttribute("aria-label", "미디어 크게 보기");
  viewer.innerHTML =
    '<div class="viewer-bar"><span class="viewer-title"></span><span class="viewer-index"></span><span class="spacer"></span>' +
    '<button type="button" class="bookmark-button viewer-media-bookmark"></button><button type="button" class="bookmark-button viewer-article-bookmark"></button>' +
    '<a class="viewer-article" href="#">글 보기</a><a class="viewer-open" href="#" target="_blank" rel="noopener">새 창</a>' +
    '<button type="button" class="viewer-close">닫기 (Esc)</button></div>' +
    '<div class="viewer-stage"><button type="button" class="viewer-nav prev" aria-label="이전 미디어">&lsaquo;</button><div class="viewer-content"></div><button type="button" class="viewer-nav next" aria-label="다음 미디어">&rsaquo;</button></div>';
  document.body.appendChild(viewer);
  var content = viewer.querySelector(".viewer-content");
  var title = viewer.querySelector(".viewer-title");
  var index = viewer.querySelector(".viewer-index");
  var openLink = viewer.querySelector(".viewer-open");
  var articleLink = viewer.querySelector(".viewer-article");
  var current = -1;
  var activeLink, previousOverflow, outside = [];

  function show(i) {
    var items = links();
    if (i < 0 || i >= items.length) return;
    var opening = !viewer.classList.contains("open");
    current = i;
    var a = items[i];
    activeLink = a;
    var kind = a.getAttribute("data-kind");
    content.innerHTML = "";
    var el;
    if (kind === "video" || kind === "gif") {
      el = document.createElement("video");
      el.src = a.href; el.controls = true; el.autoplay = true; el.loop = (kind === "gif"); el.muted = (kind === "gif");
    } else {
      el = document.createElement("img");
      el.src = a.href;
      el.alt = a.getAttribute("data-title") || "미디어";
    }
    content.appendChild(el);
    title.textContent = a.getAttribute("data-title") || "";
    index.textContent = "(" + (i + 1) + "/" + items.length + ")";
    openLink.href = a.href;
    var art = a.getAttribute("data-article");
    articleLink.style.display = art ? "" : "none";
    if (art) articleLink.href = art;
    if (window.ArchiveBookmarks) {
      window.ArchiveBookmarks.bind(viewer.querySelector(".viewer-media-bookmark"), "media", a.dataset.mediaId, kind === "image" ? "이미지" : "미디어");
      window.ArchiveBookmarks.bind(viewer.querySelector(".viewer-article-bookmark"), "article", a.dataset.articleId, "글");
    }
    viewer.querySelector(".prev").disabled = i === 0;
    viewer.querySelector(".next").disabled = i === items.length - 1;
    if (opening) {
      previousOverflow = document.body.style.overflow;
      outside = Array.from(document.querySelectorAll("body > header, body > main")).map(function (el) {
        var previous = {el: el, inert: el.inert};
        el.inert = true;
        return previous;
      });
    }
    viewer.classList.add("open");
    document.body.style.overflow = "hidden";
    if (opening) viewer.querySelector(".viewer-close").focus();
  }
  function close() {
    viewer.classList.remove("open");
    content.innerHTML = "";
    document.body.style.overflow = previousOverflow || "";
    outside.forEach(function (item) { item.el.inert = item.inert; });
    outside = [];
    current = -1;
    if (activeLink && activeLink.isConnected) activeLink.focus({preventScroll: true});
  }
  document.addEventListener("click", function (e) {
    var a = e.target.closest("a[data-viewer]");
    if (a) { e.preventDefault(); show(links().indexOf(a)); }
  });
  document.addEventListener("bookmarkresultsupdated", function () {
    if (!viewer.classList.contains("open")) return;
    var items = links();
    var previousCard = activeLink.closest("[data-saved-id]");
    var same = items.findIndex(function (a) {
      var card = a.closest("[data-saved-id]");
      return a.dataset.mediaId === activeLink.dataset.mediaId && (!previousCard ||
        (card && card.dataset.savedKind === previousCard.dataset.savedKind && card.dataset.savedId === previousCard.dataset.savedId));
    });
    if (items.length) show(same >= 0 ? same : Math.min(current, items.length - 1));
    else {
      close();
      var next = document.querySelector("#bookmark-results button, #bookmark-results a, .bookmark-filters button");
      if (next) next.focus({preventScroll: true});
    }
  });
  viewer.querySelector(".viewer-close").addEventListener("click", close);
  viewer.querySelector(".prev").addEventListener("click", function (e) { e.stopPropagation(); show(current - 1); });
  viewer.querySelector(".next").addEventListener("click", function (e) { e.stopPropagation(); show(current + 1); });
  viewer.querySelector(".viewer-stage").addEventListener("click", function (e) {
    if (e.target === this || e.target === content) close();
  });
  document.addEventListener("keydown", function (e) {
    if (!viewer.classList.contains("open")) return;
    if (e.key === "Escape") { e.preventDefault(); close(); }
    else if (e.key === "ArrowLeft" && e.target.tagName !== "VIDEO") { e.preventDefault(); show(current - 1); }
    else if (e.key === "ArrowRight" && e.target.tagName !== "VIDEO") { e.preventDefault(); show(current + 1); }
    else if (e.key === "Tab") {
      var focusable = Array.from(viewer.querySelectorAll("button, a[href], video[controls]")).filter(function (el) { return !el.disabled && !el.hidden && el.getClientRects().length; });
      var first = focusable[0], last = focusable[focusable.length - 1];
      if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
      else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
    }
  });
})();
