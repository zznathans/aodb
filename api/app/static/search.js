// Progressive enhancement for the /browse/items and /browse/nanos pages:
// intercepts search-form submits and pagination link clicks, re-fetches
// from the JSON API directly (same-origin, /items or /nanos), and
// re-renders just the results table instead of a full page reload. The
// page already works with none of this (plain server-rendered GET forms
// and links) - this only makes it snappier when JS is available.
(function () {
  "use strict";

  const form = document.getElementById("search-form");
  const container = document.getElementById("results-container");
  if (!form || !container) return;

  const kind = form.dataset.kind; // "items" or "nanos"
  const apiPath = "/" + kind;
  const browsePath = "/browse/" + kind;
  const pageSize = parseInt(form.dataset.pageSize, 10) || 50;

  function fieldNames() {
    return kind === "nanos" ? ["q", "ql", "school", "profession"] : ["q", "ql"];
  }

  function paramsFromForm() {
    const params = new URLSearchParams();
    for (const name of fieldNames()) {
      const el = form.elements.namedItem(name);
      const value = el ? el.value.trim() : "";
      if (value) params.set(name, value);
    }
    return params;
  }

  function paramsFromUrl(url) {
    const params = new URLSearchParams(url.search);
    const out = new URLSearchParams();
    for (const name of fieldNames()) {
      const value = params.get(name);
      if (value) out.set(name, value);
    }
    return out;
  }

  function escapeHtml(s) {
    const div = document.createElement("div");
    div.textContent = s == null ? "" : String(s);
    return div.innerHTML;
  }

  function renderRow(row) {
    if (kind === "nanos") {
      return (
        "<tr><td><a href='" + browsePath + "/" + row.id + "'>" + escapeHtml(row.name) + "</a></td>" +
        "<td>" + row.ql + "</td>" +
        "<td>" + escapeHtml(row.school) + "</td>" +
        "<td>" + (row.nanocost ?? "") + "</td>" +
        "<td>" + (row.ncu ?? "") + "</td></tr>"
      );
    }
    return (
      "<tr><td><a href='" + browsePath + "/" + row.id + "'>" + escapeHtml(row.name) + "</a></td>" +
      "<td>" + row.ql + "</td>" +
      "<td>" + escapeHtml(row.description) + "</td></tr>"
    );
  }

  function headerRow() {
    return kind === "nanos"
      ? "<tr><th>Name</th><th>QL</th><th>School</th><th>Nanocost</th><th>NCU</th></tr>"
      : "<tr><th>Name</th><th>QL</th><th>Description</th></tr>";
  }

  function paginationLink(params, page, label) {
    const p = new URLSearchParams(params);
    p.set("page", page);
    return "<a href='" + browsePath + "?" + p.toString() + "' data-page='" + page + "'>" + label + "</a>";
  }

  async function loadResults(params, page, pushState) {
    const fetchParams = new URLSearchParams(params);
    fetchParams.set("limit", pageSize);
    fetchParams.set("offset", (page - 1) * pageSize);

    let resp;
    try {
      resp = await fetch(apiPath + "?" + fetchParams.toString());
    } catch (err) {
      return; // network hiccup - leave whatever's currently rendered alone
    }
    if (!resp.ok) return;

    const rows = await resp.json();
    const total = parseInt(resp.headers.get("X-Total-Count") || "0", 10);
    const hasNext = (page - 1) * pageSize + pageSize < total;

    let html = "<p class='result-count'>";
    if (total) {
      const start = (page - 1) * pageSize + 1;
      const end = Math.min((page - 1) * pageSize + pageSize, total);
      html += "Showing " + start + "–" + end + " of " + total;
    } else {
      html += "No " + kind + " found.";
    }
    html += "</p>";

    if (rows.length) {
      html += "<table class='results'><thead>" + headerRow() + "</thead><tbody>";
      for (const row of rows) html += renderRow(row);
      html += "</tbody></table>";
    }

    html += "<nav class='pagination'>";
    if (page > 1) html += paginationLink(params, page - 1, "« Prev");
    html += "<span>Page " + page + "</span>";
    if (hasNext) html += paginationLink(params, page + 1, "Next »");
    html += "</nav>";

    container.innerHTML = html;

    if (pushState) {
      const p = new URLSearchParams(params);
      p.set("page", page);
      history.pushState({ params: params.toString(), page }, "", browsePath + "?" + p.toString());
    }
  }

  form.addEventListener("submit", function (event) {
    event.preventDefault();
    loadResults(paramsFromForm(), 1, true);
  });

  container.addEventListener("click", function (event) {
    const link = event.target.closest("a[data-page]");
    if (!link) return;
    event.preventDefault();
    loadResults(paramsFromForm(), parseInt(link.dataset.page, 10), true);
  });

  window.addEventListener("popstate", function () {
    const url = new URL(window.location.href);
    const page = parseInt(url.searchParams.get("page"), 10) || 1;
    loadResults(paramsFromUrl(url), page, false);
  });
})();
