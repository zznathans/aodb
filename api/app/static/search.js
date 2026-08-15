// Progressive enhancement for the /items and /nanos browse pages:
// intercepts search-form submits and pagination link clicks, re-fetches
// from the JSON API directly (same-origin, /api/items or /api/nanos), and
// re-renders just the results table instead of a full page reload. The
// page already works with none of this (plain server-rendered GET forms
// and links) - this only makes it snappier when JS is available.
(function () {
  "use strict";

  const form = document.getElementById("search-form");
  const container = document.getElementById("results-container");
  if (!form || !container) return;

  const kind = form.dataset.kind; // "items" or "nanos"
  const apiPath = "/api/" + kind;
  // Detail links (renderRow) always point at the plain, unscoped browse
  // path - only the results list/pagination stays under the current path,
  // which may be a category/profession-scoped URL
  // (/items/categories/armor, /nanos/professions/doctor).
  const basePath = "/" + kind;
  const listPath = window.location.pathname;
  const pageSize = parseInt(form.dataset.pageSize, 10) || 50;

  function fieldNames() {
    return ["q"];
  }

  // profession/category/subcategory are fixed by the current URL path
  // rather than a form field (see app/web.py's /professions/{slug},
  // /categories/{slug}, and /categories/{slug}/types/{slug} routes) -
  // carried as data attributes and merged into the JSON API fetch, but
  // deliberately kept out of the visible pagination/pushState URLs below,
  // which stay scoped by path instead.
  function pathScopeParams() {
    const params = new URLSearchParams();
    if (form.dataset.profession) params.set("profession", form.dataset.profession);
    if (form.dataset.category) params.set("category", form.dataset.category);
    if (form.dataset.subcategory) params.set("subcategory", form.dataset.subcategory);
    return params;
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

  // Mirrors app/web.py's _merge_ql_variants() - collapses consecutive
  // same name+description rows (already adjacent since results are
  // name-sorted) into one row with a ql range, so the JS-rendered path
  // matches the server-rendered one.
  function mergeQlVariants(rows) {
    const merged = [];
    for (const row of rows) {
      const last = merged[merged.length - 1];
      if (last && last.name === row.name && last.description === row.description) {
        if (row.ql < last.qlMin) {
          last.qlMin = row.ql;
          last.id = row.id;
        }
        last.qlMax = Math.max(last.qlMax, row.ql);
      } else {
        merged.push({ id: row.id, name: row.name, description: row.description, qlMin: row.ql, qlMax: row.ql });
      }
    }
    for (const row of merged) {
      row.qlDisplay = row.qlMin === row.qlMax ? String(row.qlMin) : row.qlMin + "–" + row.qlMax;
    }
    return merged;
  }

  function renderRow(row) {
    if (kind === "nanos") {
      return (
        "<tr><td><a href='" + basePath + "/" + row.id + "'>" + escapeHtml(row.name) + "</a></td>" +
        "<td>" + row.ql + "</td>" +
        "<td>" + escapeHtml(row.school) + "</td>" +
        "<td>" + (row.nanocost ?? "") + "</td>" +
        "<td>" + (row.ncu ?? "") + "</td></tr>"
      );
    }
    return (
      "<tr><td class='col-name'><a href='" + basePath + "/" + row.id + "'>" + escapeHtml(row.name) + "</a></td>" +
      "<td>" + row.qlDisplay + "</td>" +
      "<td class='col-description'>" + escapeHtml(row.description) + "</td></tr>"
    );
  }

  function headerRow() {
    return kind === "nanos"
      ? "<tr><th data-sort-type='text'>Name</th><th class='col-ql' data-sort-type='number'>QL</th>" +
          "<th data-sort-type='text'>School</th><th data-sort-type='number'>Nanocost</th>" +
          "<th data-sort-type='number'>NCU</th></tr>"
      : "<tr><th class='col-name' data-sort-type='text'>Name</th><th class='col-ql' data-sort-type='number'>QL</th>" +
          "<th class='col-description' data-sort-type='text'>Description</th></tr>";
  }

  function paginationLink(params, page, label) {
    const p = new URLSearchParams(params);
    p.set("page", page);
    return "<a href='" + listPath + "?" + p.toString() + "' data-page='" + page + "'>" + label + "</a>";
  }

  async function loadResults(params, page, pushState) {
    const fetchParams = new URLSearchParams(params);
    for (const [name, value] of pathScopeParams()) fetchParams.set(name, value);
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
      const displayRows = kind === "items" ? mergeQlVariants(rows) : rows;
      html += "<table class='results'><thead>" + headerRow() + "</thead><tbody>";
      for (const row of displayRows) html += renderRow(row);
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
      history.pushState({ params: params.toString(), page }, "", listPath + "?" + p.toString());
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
