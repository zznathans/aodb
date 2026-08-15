// Click-to-sort for any <table class="results"> - sorts whatever rows are
// currently rendered client-side (a page of up to 50 at a time), no backend
// involved. Delegated on `document` rather than bound per-table, since
// app/static/search.js re-renders results tables via innerHTML on every
// search/pagination action - a direct listener on the old table would be
// thrown away with it.
(function () {
  "use strict";

  function cellValue(row, index, type) {
    const text = (row.children[index].textContent || "").trim();
    if (type === "number") {
      // parseFloat reads only the leading numeric run and ignores
      // whatever follows, so a merged-item QL range like "1-300" (see
      // app/web.py's _merge_ql_variants) sorts by its low end - deliberate,
      // not just tolerated.
      const n = parseFloat(text);
      return isNaN(n) ? -Infinity : n;
    }
    return text.toLowerCase();
  }

  function sortRows(table, index, type, dir) {
    const tbody = table.tBodies[0];
    if (!tbody) return;
    const rows = Array.prototype.slice.call(tbody.rows);
    const sign = dir === "asc" ? 1 : -1;
    rows.sort(function (a, b) {
      const av = cellValue(a, index, type);
      const bv = cellValue(b, index, type);
      if (av < bv) return -sign;
      if (av > bv) return sign;
      return 0;
    });
    rows.forEach(function (row) {
      tbody.appendChild(row);
    });
  }

  document.addEventListener("click", function (event) {
    const th = event.target.closest("table.results thead th");
    if (!th) return;

    const table = th.closest("table");
    const headerRow = th.parentElement;
    const index = Array.prototype.indexOf.call(headerRow.children, th);
    const type = th.dataset.sortType || "text";
    const dir = th.dataset.sortDir === "asc" ? "desc" : "asc";

    Array.prototype.forEach.call(headerRow.children, function (h) {
      delete h.dataset.sortDir;
      h.classList.remove("sort-asc", "sort-desc");
    });
    th.dataset.sortDir = dir;
    th.classList.add(dir === "asc" ? "sort-asc" : "sort-desc");

    sortRows(table, index, type, dir);
  });
})();
