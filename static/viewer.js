// Read-only collection view for the public viewer (viewer.html). A trimmed port of
// scan.js's collection mode: grid/list toggle, title/author filter, sortable list
// columns, column picker — no camera, no borrow/return, no admin. Data comes from
// /api/books, which the server refreshes from the published catalog.json in R2.

const I18N = JSON.parse(document.getElementById("i18n").textContent);
function T(key, params) {
  const s = I18N[key] ?? key;
  return params ? s.replace(/\{(\w+)\}/g, (_, k) => (k in params ? params[k] : `{${k}}`)) : s;
}

const collectionCountEl = document.getElementById("collection-count");
const collectionBodyEl = document.getElementById("collection-body");
const collectionSearch = document.getElementById("collection-search");
const lastSyncedEl = document.getElementById("last-synced");
const reloadBtn = document.getElementById("reload-btn");
const viewGridBtn = document.getElementById("view-grid-btn");
const viewListBtn = document.getElementById("view-list-btn");
const columnsBtn = document.getElementById("columns-btn");
const columnPanel = document.getElementById("column-panel");

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]),
  );
}

// Safe cover <img src>: https as-is; upgrade http→https; block everything else.
function coverSrc(url) {
  url = url || "";
  if (/^https:\/\//.test(url)) return url;
  if (/^http:\/\//.test(url)) return "https://" + url.slice(7);
  return "";
}

// Open Library serves 200 + a transparent 1×1 GIF when it has no cover (old DB
// rows still carry such URLs), so a "successful" load can be blank. Swap blank
// AND failed covers for the styled no-cover tile.
function watchCover(img, title) {
  const check = () => {
    if (img.naturalWidth <= 1) {
      const tile = document.createElement("div");
      tile.className = "no-cover";
      tile.textContent = title || "";
      img.replaceWith(tile);
    }
  };
  if (img.complete) check(); // cached images may load before listeners attach
  else {
    img.addEventListener("load", check);
    img.addEventListener("error", check);
  }
}

// --- Grid / list toggle (persisted per browser) ---
let cachedBooks = null;
let collectionView = localStorage.getItem("collectionView") || "grid";

function setCollectionView(view) {
  collectionView = view;
  localStorage.setItem("collectionView", view);
  viewGridBtn.classList.toggle("active", view === "grid");
  viewListBtn.classList.toggle("active", view === "list");
  columnsBtn.hidden = view !== "list"; // columns only apply to the table
  if (view !== "list") columnPanel.hidden = true;
  if (cachedBooks) applyCollectionFilter();
}
viewGridBtn.addEventListener("click", () => setCollectionView("grid"));
viewListBtn.addEventListener("click", () => setCollectionView("list"));
viewGridBtn.classList.toggle("active", collectionView === "grid");
viewListBtn.classList.toggle("active", collectionView === "list");
columnsBtn.hidden = collectionView !== "list";

// --- Column selector (persisted per browser) ---
const HIDDEN_COLUMNS = new Set(["book_id", "cover_url"]);
const COLUMN_LABELS = {
  isbn: "ISBN", title: "Title", author: "Author", publisher: "Publisher",
  year: "Year", language: "Language", tags: "Tags", source: "Source",
  total_count: "Copies", available: "Available", added_at: "Added",
};
const DEFAULT_COLUMNS = ["title", "author", "year", "publisher", "available"];
const COLUMN_ORDER = [
  "title", "author", "year", "publisher", "language", "tags",
  "isbn", "source", "added_at", "total_count", "available",
];

let visibleColumns =
  JSON.parse(localStorage.getItem("collectionColumns") || "null") || DEFAULT_COLUMNS;

function availableColumns(books) {
  if (!books || !books.length) return [];
  const present = Object.keys(books[0]).filter((k) => !HIDDEN_COLUMNS.has(k));
  const ordered = COLUMN_ORDER.filter((k) => present.includes(k));
  const extra = present.filter((k) => !COLUMN_ORDER.includes(k));
  return [...ordered, ...extra];
}
const colLabel = (key) => COLUMN_LABELS[key] || key;

function effectiveColumns(books) {
  const have = new Set(availableColumns(books));
  const cols = visibleColumns.filter((c) => have.has(c));
  return cols.length ? cols : DEFAULT_COLUMNS.filter((c) => have.has(c));
}

function buildColumnPanel(books) {
  const active = new Set(effectiveColumns(books));
  columnPanel.innerHTML = availableColumns(books)
    .map(
      (c) =>
        `<label class="column-opt"><input type="checkbox" value="${esc(c)}" ${
          active.has(c) ? "checked" : ""
        }/> ${esc(colLabel(c))}</label>`,
    )
    .join("");
  columnPanel.querySelectorAll("input[type=checkbox]").forEach((cb) =>
    cb.addEventListener("change", () => {
      const chosen = [...columnPanel.querySelectorAll("input:checked")].map((i) => i.value);
      visibleColumns = chosen.length ? chosen : DEFAULT_COLUMNS.slice();
      localStorage.setItem("collectionColumns", JSON.stringify(visibleColumns));
      if (cachedBooks) applyCollectionFilter();
    }),
  );
}

columnsBtn.addEventListener("click", () => {
  columnPanel.hidden = !columnPanel.hidden;
  if (!columnPanel.hidden && cachedBooks) buildColumnPanel(cachedBooks);
});

// --- Filter ---
function applyCollectionFilter() {
  if (!cachedBooks) return;
  const q = (collectionSearch.value || "").trim().toLowerCase();
  const books = q
    ? cachedBooks.filter((b) =>
        [b.title, b.author, b.isbn, b.tags].some((v) => (v || "").toLowerCase().includes(q)),
      )
    : cachedBooks;
  renderCollection(books);
}
collectionSearch.addEventListener("input", applyCollectionFilter);

// --- Sort (click a list column header; ascending, click again for descending) ---
let sortColumn = "added_at"; // default: most recently added first
let sortDir = -1;

function sortBooks(books) {
  if (!sortColumn) return books;
  return books.slice().sort((a, b) => {
    const x = a[sortColumn];
    const y = b[sortColumn];
    const nx = Number(x);
    const ny = Number(y);
    const numeric = x != null && y != null && x !== "" && y !== "" && !isNaN(nx) && !isNaN(ny);
    if (numeric) return (nx - ny) * sortDir;
    return String(x ?? "").localeCompare(String(y ?? ""), undefined, { numeric: true }) * sortDir;
  });
}

// --- Rendering ---
function renderCollection(books) {
  collectionCountEl.textContent =
    books.length === 0 ? "" : T("st_book_count", { n: books.length });
  if (books.length === 0) {
    collectionBodyEl.innerHTML = `<p>${T("st_no_books")}</p>`;
    return;
  }
  const ordered = sortBooks(books);
  if (collectionView === "list") renderList(ordered);
  else renderGrid(ordered);
}

function renderGrid(books) {
  const cards = books
    .map((book) => {
      const src = coverSrc(book.cover_url);
      const cover = src
        ? `<img src="${esc(src)}" alt="cover" loading="lazy" />`
        : `<div class="no-cover">${esc(book.title || "")}</div>`;
      const avail = book.available ?? 0;
      const total = book.total_count ?? 1;
      const badgeClass = avail > 0 ? "badge-ok" : "badge-out";
      const badgeLabel = avail > 0 ? `${avail}/${total}` : "Out";
      return `
        <div class="book-card" data-isbn="${esc(book.isbn)}">
          ${cover}
          <div class="info">
            <strong>${esc(book.title || "(no title)")}</strong>
            <span class="author">${esc(book.author || "")}</span>
            <span class="badge ${badgeClass}">${badgeLabel}</span>
          </div>
        </div>`;
    })
    .join("");
  collectionBodyEl.innerHTML = `<div class="book-grid">${cards}</div>`;
  // Read-only: cards are not clickable (no borrow/return here).
  collectionBodyEl.querySelectorAll(".book-card").forEach((card, i) => {
    const img = card.querySelector("img");
    if (img) watchCover(img, books[i].title);
  });
}

function renderList(books) {
  const cols = effectiveColumns(books);
  const head = cols
    .map((c) => {
      const arrow = sortColumn === c ? (sortDir === 1 ? " ▲" : " ▼") : "";
      return `<th class="sortable" data-col="${esc(c)}">${esc(colLabel(c))}${arrow}</th>`;
    })
    .join("");
  const rows = sortBooks(books)
    .map(
      (book) =>
        `<tr data-isbn="${esc(book.isbn)}">${cols
          .map((c) => `<td>${cellHtml(book, c)}</td>`)
          .join("")}</tr>`,
    )
    .join("");
  collectionBodyEl.innerHTML = `
    <table class="book-list">
      <thead><tr>${head}</tr></thead>
      <tbody>${rows}</tbody>
    </table>`;
  collectionBodyEl.querySelectorAll("thead th.sortable").forEach((th) => {
    th.addEventListener("click", () => {
      const c = th.dataset.col;
      if (sortColumn === c) sortDir = -sortDir;
      else {
        sortColumn = c;
        sortDir = 1;
      }
      applyCollectionFilter(); // re-render with the new sort, keeping the filter
    });
  });
}

// One table cell. "available" renders as a coloured availability badge.
function cellHtml(book, col) {
  if (col === "available") {
    const avail = book.available ?? 0;
    const total = book.total_count ?? 1;
    const cls = avail > 0 ? "badge-ok" : "badge-out";
    return `<span class="badge ${cls}">${avail > 0 ? `${avail}/${total}` : "Out"}</span>`;
  }
  const v = book[col];
  return esc(v == null ? "" : String(v));
}

// --- Load ---
async function loadCollection() {
  collectionCountEl.textContent = "";
  collectionBodyEl.innerHTML = `<p>${T("st_loading")}</p>`;
  try {
    const resp = await fetch("/api/books");
    if (!resp.ok) {
      const { error } = await resp.json().catch(() => ({}));
      throw new Error(error || `HTTP ${resp.status}`);
    }
    const data = await resp.json();
    cachedBooks = data.books || [];
    if (data.generated_at) {
      const t = new Date(data.generated_at).toLocaleString();
      // "stale" = the server couldn't re-check R2 just now; the copy is the last good one.
      lastSyncedEl.textContent = T("st_synced", { t }) + (data.stale ? " ⚠" : "");
    } else {
      lastSyncedEl.textContent = "";
    }
    applyCollectionFilter();
  } catch (err) {
    collectionBodyEl.innerHTML = `<p style="color:#c00">${esc(err.message)}</p>`;
  }
}
reloadBtn.addEventListener("click", loadCollection);

loadCollection();
