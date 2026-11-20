/* 皮肤库列表交互（对照 /skinlib/search JSON 契约） */

(function () {
  "use strict";

  const grid = document.getElementById("texture-grid");
  const empty = document.getElementById("grid-empty");
  const pagerInfo = document.getElementById("pager-info");
  const prevBtn = document.getElementById("pager-prev");
  const nextBtn = document.getElementById("pager-next");

  const params = new URLSearchParams(window.location.search);
  let currentPage = parseInt(params.get("page") || "1", 10);

  function currentQueryString() {
    return new URLSearchParams({
      keyword: document.getElementById("search-keyword").value.trim(),
      category: document.getElementById("search-category").value,
      sort: document.getElementById("search-sort").value,
    });
  }

  async function load(page) {
    const qs = currentQueryString();
    qs.set("page", String(page));
    const payload = await window.bsFetch("/skinlib/search?" + qs.toString());
    if (payload.code !== 0) {
      window.bsToast(payload.message || "加载失败", "error");
      return;
    }
    render(payload.data);
    // 状态入 URL（replaceState 不留历史记录）：刷新/分享后保持当前页与筛选
    window.history.replaceState(null, "", "?" + qs.toString());
  }

  function render(data) {
    currentPage = data.current_page;
    grid.innerHTML = "";
    empty.style.display = data.data.length === 0 ? "block" : "none";

    data.data.forEach(function (item) {
      const card = document.createElement("div");
      card.className = "bs-card";
      // 公开纹理走伪静态指纹 URL（/preview/{tid}-{hash8}-h{height}.png）：
      // 换皮后 hash 变即换 URL，CDN 可按 immutable 一年缓存，回源零渲染；
      // 私密纹理回落鉴权端点（CDN 不缓存非公开内容）
      const imgUrl = item.public
        ? "/preview/" + item.tid + "-" + item.hash.slice(0, 8) + "-h150.png"
        : "/preview/" + item.tid + "?height=150";
      card.innerHTML =
        '<div style="padding:12px;cursor:pointer" data-tid="' + item.tid + '">' +
        '<img src="' + imgUrl + '" alt="' + escapeHtml(item.name) + '"' +
        '     style="width:100%;border-radius:6px;background:' +
        "var(--bs-primary-light)" + '">' +
        '<div style="margin-top:8px;font-weight:600">' + escapeHtml(item.name) + "</div>" +
        '<div style="font-size:12px;color:var(--bs-text-secondary);display:flex;' +
        'justify-content:space-between;align-items:center;margin-top:2px">' +
        "<span>" + (item.type === "cape" ? "披风" : "皮肤") +
        (item.public
          ? ' <span style="color:var(--bs-success)">公开</span>'
          : ' <span style="color:var(--bs-warning-emphasis)">私密</span>') +
        "</span>" +
        "<span>♥ " + item.likes + "</span>" +
        "</div>" +
        '<div style="font-size:12px;color:var(--bs-text-secondary);margin-top:2px;' +
        'overflow:hidden;text-overflow:ellipsis;white-space:nowrap">' +
        "上传者：" + escapeHtml(item.uploader || "—") +
        "</div></div>";
      card.querySelector("[data-tid]").addEventListener("click", function () {
        window.location.href = "/skinlib/show?tid=" + item.tid;
      });
      grid.appendChild(card);
    });

    pagerInfo.textContent = "第 " + data.current_page + " / " + data.last_page + " 页";
    prevBtn.style.visibility = data.current_page <= 1 ? "hidden" : "visible";
    nextBtn.style.visibility =
      data.current_page >= data.last_page ? "hidden" : "visible";
  }

  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  document.getElementById("search-btn").addEventListener("click", function () {
    load(1);
  });
  document.getElementById("search-keyword").addEventListener("keydown", function (e) {
    if (e.key === "Enter") load(1);
  });
  prevBtn.addEventListener("click", function () {
    if (currentPage > 1) load(currentPage - 1);
  });
  nextBtn.addEventListener("click", function () {
    load(currentPage + 1);
  });

  load(currentPage);
})();
