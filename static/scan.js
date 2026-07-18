// Browser-side slice: turn on the camera, decode an EAN-13 barcode, and hand the
// resulting ISBN to the Python backend. This part HAS to live in the browser (only
// the browser can reach the camera). Keep it thin — all the book logic is in Python.
//
// BarcodeDetector is native in Chrome/Edge but missing/unreliable on Safari/iOS,
// so we import a polyfill that gives the same API everywhere. If this CDN import
// ever gives you trouble, a common alternative is `@zxing/browser`.
import { BarcodeDetector } from "https://esm.sh/barcode-detector@2/pure";

const video = document.getElementById("video");
const startBtn = document.getElementById("start");
const stopBtn = document.getElementById("stop");
const snapBtn = document.getElementById("snap");
const snapCanvas = document.getElementById("snap-canvas");
const manualIsbn = document.getElementById("manual-isbn");
const manualGo = document.getElementById("manual-go");
const titleSearch = document.getElementById("title-search");
const titleInput = document.getElementById("title-input");
const titleGo = document.getElementById("title-go");
const manualBook = document.getElementById("manual-book");
const manualBookToggle = document.getElementById("manual-book-toggle");
const manualBookForm = document.getElementById("manual-book-form");
const mb = {
  title: document.getElementById("mb-title"),
  author: document.getElementById("mb-author"),
  publisher: document.getElementById("mb-publisher"),
  year: document.getElementById("mb-year"),
  isbn: document.getElementById("mb-isbn"),
  copies: document.getElementById("mb-copies"),
};
const statusEl = document.getElementById("status");
const resultEl = document.getElementById("result");
const cameraUi = document.getElementById("camera-ui");
const collectionEl = document.getElementById("collection");
const collectionCountEl = document.getElementById("collection-count");
const collectionBodyEl = document.getElementById("collection-body");
const loansEl = document.getElementById("loans");
const loansBodyEl = document.getElementById("loans-body");

const detector = new BarcodeDetector({ formats: ["ean_13"] });

// UI strings (from the server, per BOOK_LANG). T(key) returns the string; T(key, {…})
// fills {placeholders}.
const I18N = JSON.parse(document.getElementById("i18n").textContent);
function T(key, params) {
  const s = I18N[key] ?? key;
  return params ? s.replace(/\{(\w+)\}/g, (_, k) => (k in params ? params[k] : `{${k}}`)) : s;
}
// Translated label for the current mode (for the scan prompt).
const modeLabel = (mode) => T("mode_" + mode);

// Flow states:
//   scanning=true,  reviewing=false  -> camera live, looking for a barcode
//   scanning=true,  reviewing=true   -> a book is on the card; detection PAUSED
//   scanning=false                   -> camera off
let stream = null;
let scanning = false;
let reviewing = false;
let busy = false;

// Require the same value on a few consecutive frames before trusting it.
const CONFIRM_FRAMES = 3;
let candidate = null;
let candidateCount = 0;

function setStatus(msg) {
  statusEl.textContent = msg;
}

// Which action the current scan should perform: "register" | "borrow" | "return" | "collection".
function currentMode() {
  return document.querySelector('input[name="mode"]:checked').value;
}

async function startCamera() {
  try {
    stream = await navigator.mediaDevices.getUserMedia({
      video: { facingMode: "environment" }, // rear camera on phones
      audio: false,
    });
    video.srcObject = stream;
    await video.play();
    scanning = true;
    resumeScanning();
    startBtn.disabled = true;
    stopBtn.disabled = false;
    snapBtn.disabled = false;
    requestAnimationFrame(scanLoop);
  } catch (err) {
    // Most common cause on a phone: not served over HTTPS (camera is blocked).
    setStatus(T("st_camera_error", { msg: err.message }));
  }
}

function stopCamera() {
  scanning = false;
  reviewing = false;
  stream?.getTracks().forEach((track) => track.stop());
  stream = null;
  startBtn.disabled = false;
  stopBtn.disabled = true;
  snapBtn.disabled = true;
  setStatus(T("st_stopped"));
}

// Clear the card and go (back) to actively scanning. `keepStatus` leaves the
// caller's message (e.g. "Borrowed ✅") instead of the scan prompt.
function resumeScanning(keepStatus = false) {
  resultEl.innerHTML = "";
  reviewing = false;
  candidate = null;
  candidateCount = 0;
  if (!keepStatus) setStatus(T("st_scan", { mode: modeLabel(currentMode()) }));
}

async function scanLoop() {
  if (!scanning) return;
  try {
    if (!busy && !reviewing) {
      const barcodes = await detector.detect(video);
      const raw = barcodes.length > 0 ? barcodes[0].rawValue : null;
      if (raw) {
        if (raw === candidate) {
          candidateCount += 1;
        } else {
          candidate = raw;
          candidateCount = 1;
        }
        if (candidateCount >= CONFIRM_FRAMES) {
          candidate = null;
          candidateCount = 0;
          busy = true;
          const ok = await handleScan(raw);
          busy = false;
          if (ok) reviewing = true; // pause for the user's decision
        }
      }
    }
  } catch (err) {
    setStatus(T("st_scan_error", { msg: err.message }));
    busy = false;
  }
  requestAnimationFrame(scanLoop);
}

// Route a confirmed scan by mode. Returns true if a card is shown (pause), false
// to keep scanning (so a miss/"not in library" is retryable).
async function handleScan(isbn) {
  const mode = currentMode();
  if (mode === "register") return lookupForRegister(isbn);
  return lookupForAction(isbn, mode); // borrow / return read from the DB
}

// GET helper that surfaces the JSON {error} message from a failed response.
async function getJson(url) {
  const resp = await fetch(url);
  if (!resp.ok) {
    const { error } = await resp.json().catch(() => ({}));
    throw new Error(error || T("st_request_failed", { status: resp.status }));
  }
  return resp.json();
}

// --- Register: fetch metadata from the online API, then offer to add ---
async function lookupForRegister(isbn) {
  setStatus(T("st_looking_up", { isbn }));
  resultEl.innerHTML = "";
  try {
    const book = await getJson(`/api/lookup/${isbn}`);
    renderRegisterCard(book);
    setStatus(T("st_found", { title: book.title }));
    return true;
  } catch (err) {
    setStatus(err.message);
    return false;
  }
}

// --- Borrow / return: look the book up in the DB (no network), then act ---
async function lookupForAction(isbn, mode) {
  setStatus(T("st_looking_up", { isbn }));
  resultEl.innerHTML = "";
  try {
    const { book, open_loans } = await getJson(`/api/book/${isbn}`);
    if (mode === "borrow") renderBorrowCard(book);
    else renderReturnCard(book, open_loans || []);
    return true;
  } catch (err) {
    // e.g. "This book isn't in the library yet." — stay scanning so it's retryable.
    setStatus(err.message);
    return false;
  }
}

// Escape user-supplied text (borrower labels) before putting it in innerHTML.
function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]),
  );
}

// Safe cover <img src>: https as-is; upgrade http→https (covers serve fine over https,
// which recovers Google's http thumbnails and avoids mixed content); block everything
// else (javascript:/data:). Returns "" when there's no usable URL.
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

// Shared book blurb (cover + title/author + availability when known).
function bookInfoHtml(book, showCounts) {
  const counts =
    showCounts && book.available != null
      ? `<br /><small>${book.available} of ${book.total_count} available</small>`
      : "";
  const safeCover = coverSrc(book.cover_url);
  // onload guard: drop Open Library's blank 1×1 placeholder like a failed load.
  return `
    ${safeCover ? `<img src="${esc(safeCover)}" alt="cover" onerror="this.remove()" onload="if(this.naturalWidth<2)this.remove()" />` : ""}
    <div>
      <strong>${esc(book.title || "(no title)")}</strong><br />
      ${esc(book.author || "")}<br />
      <small>ISBN ${esc(book.isbn || "")}${book.language ? " · " + esc(book.language) : ""}</small>${counts}
      <div id="actions"></div>
    </div>`;
}

function renderRegisterCard(book) {
  resultEl.innerHTML = bookInfoHtml(book, false);
  const actions = resultEl.querySelector("#actions");
  actions.innerHTML = `<button id="add">${esc(T("act_add"))}</button>
                       <button id="next">${esc(T("act_scan_next"))}</button>`;
  actions.querySelector("#add").addEventListener("click", () => registerBook(book));
  actions.querySelector("#next").addEventListener("click", () => resumeScanning());
}

function renderBorrowCard(book) {
  resultEl.innerHTML = bookInfoHtml(book, true);
  const actions = resultEl.querySelector("#actions");
  const none = book.available <= 0;
  actions.innerHTML = `
    <input id="borrower" aria-label="${esc(T("ph_name"))}" placeholder="${esc(T("ph_name"))}" autocomplete="off" />
    <button id="borrow" ${none ? "disabled" : ""}>${esc(T("act_borrow"))}</button>
    <button id="next">${esc(T("act_scan_next"))}</button>`;
  if (none) setStatus(T("st_no_copies", { title: book.title }));
  else setStatus(T("st_borrow_enter_name", { title: book.title }));
  actions.querySelector("#borrow").addEventListener("click", () => borrowBook(book));
  actions.querySelector("#next").addEventListener("click", () => resumeScanning());
}

// Return: list the open loans and let the user tap the one they're closing.
function renderReturnCard(book, openLoans) {
  resultEl.innerHTML = bookInfoHtml(book, true);
  const actions = resultEl.querySelector("#actions");

  if (openLoans.length === 0) {
    actions.innerHTML = `<button id="next">${esc(T("act_scan_next"))}</button>`;
    setStatus(T("st_no_copies_out", { title: book.title }));
  } else {
    const rows = openLoans
      .map(
        (loan) => `
        <div class="loan">
          <span>${esc(loan.borrower)} · ${esc((loan.borrowed_at || "").slice(0, 10))}</span>
          <button class="return-one" data-loan="${loan.loan_id}">${esc(T("act_return_this"))}</button>
        </div>`,
      )
      .join("");
    actions.innerHTML = `${rows}<button id="next">${esc(T("act_scan_next"))}</button>`;
    setStatus(T("st_who_returning", { title: book.title }));
    actions.querySelectorAll(".return-one").forEach((btn) =>
      btn.addEventListener("click", () => returnLoan(book, Number(btn.dataset.loan))),
    );
  }
  actions.querySelector("#next").addEventListener("click", () => resumeScanning());
}

// --- Collection: cover grid or list of all registered books ---
let collectionView = localStorage.getItem("collectionView") || "grid";
let cachedBooks = null;

const viewGridBtn = document.getElementById("view-grid-btn");
const viewListBtn = document.getElementById("view-list-btn");

function setCollectionView(view) {
  collectionView = view;
  localStorage.setItem("collectionView", view);
  viewGridBtn.classList.toggle("active", view === "grid");
  viewListBtn.classList.toggle("active", view === "list");
  if (columnsBtn) columnsBtn.hidden = view !== "list"; // columns only apply to the table
  if (view !== "list" && columnPanel) columnPanel.hidden = true;
  if (cachedBooks) renderCollection(cachedBooks);
}

viewGridBtn.addEventListener("click", () => setCollectionView("grid"));
viewListBtn.addEventListener("click", () => setCollectionView("list"));

// Sync toggle button state on load.
viewGridBtn.classList.toggle("active", collectionView === "grid");
viewListBtn.classList.toggle("active", collectionView === "list");

// --- Column selector (everyone) + admin mode ---
const columnsBtn = document.getElementById("columns-btn");
const columnPanel = document.getElementById("column-panel");
const adminBtn = document.getElementById("admin-btn");
const importBtn = document.getElementById("import-btn");
const importFile = document.getElementById("import-file");
const loansBtn = document.getElementById("loans-btn");

// Columns we never offer (internal id / non-textual).
const HIDDEN_COLUMNS = new Set(["book_id", "cover_url"]);
// Pretty labels; any column not listed falls back to its raw DB name.
const COLUMN_LABELS = {
  isbn: "ISBN", title: "Title", author: "Author", publisher: "Publisher",
  year: "Year", language: "Language", tags: "Tags", source: "Source",
  total_count: "Copies", available: "Available", added_at: "Added",
};
const DEFAULT_COLUMNS = ["title", "author", "year", "publisher", "available"];
// User-friendly column order (the API returns keys alphabetically). Any column not
// listed here — e.g. a future migration field — is appended after these.
const COLUMN_ORDER = [
  "title", "author", "year", "publisher", "language", "tags",
  "isbn", "source", "added_at", "total_count", "available",
];

let visibleColumns =
  JSON.parse(localStorage.getItem("collectionColumns") || "null") || DEFAULT_COLUMNS;

// Options come from the DB row keys (a future migration column appears automatically),
// minus the hidden internal ones, ordered the friendly way above.
function availableColumns(books) {
  if (!books || !books.length) return [];
  const present = Object.keys(books[0]).filter((k) => !HIDDEN_COLUMNS.has(k));
  const ordered = COLUMN_ORDER.filter((k) => present.includes(k));
  const extra = present.filter((k) => !COLUMN_ORDER.includes(k));
  return [...ordered, ...extra];
}
const colLabel = (key) => COLUMN_LABELS[key] || key;

// User's choice intersected with what the DB actually has (preserve order); fall
// back to defaults if that leaves nothing.
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
      if (cachedBooks) renderCollection(cachedBooks);
    }),
  );
}

if (columnsBtn) {
  columnsBtn.hidden = collectionView !== "list";
  columnsBtn.addEventListener("click", () => {
    columnPanel.hidden = !columnPanel.hidden;
    if (!columnPanel.hidden && cachedBooks) buildColumnPanel(cachedBooks);
  });
}

// Admin: in "open" mode (trusted local run) admin is always on, no password. Otherwise
// the password is held in sessionStorage for the tab and sent as a header on
// privileged requests, verified server-side.
const adminOpen = document.body.dataset.adminOpen === "true";
let adminPassword = sessionStorage.getItem("adminPassword") || "";
const isAdmin = () => adminOpen || !!adminPassword;
const adminHeaders = () => (adminPassword ? { "X-Admin-Password": adminPassword } : {});

function refreshAdminBtn() {
  if (adminBtn) {
    adminBtn.textContent = isAdmin() ? "🔓 Admin ✓" : "🔒 Admin";
    adminBtn.classList.toggle("active", isAdmin());
  }
  if (importBtn) importBtn.hidden = !isAdmin(); // CSV import is admin-only
  if (loansBtn) loansBtn.hidden = !isAdmin(); // loans view is admin-only
  syncModeUi(); // admin state gates the manual-entry form in register mode
}
refreshAdminBtn();

// Open mode needs no unlock button behaviour — admin is already active.
if (adminBtn && !adminOpen) {
  adminBtn.addEventListener("click", async () => {
    if (isAdmin()) {
      adminPassword = "";
      sessionStorage.removeItem("adminPassword");
      refreshAdminBtn();
      if (cachedBooks) renderCollection(cachedBooks);
      return;
    }
    const pw = window.prompt("Admin password:"); // collection view has no live camera
    if (!pw) return;
    try {
      const resp = await fetch("/api/admin/check", {
        method: "POST",
        headers: { "X-Requested-With": "XMLHttpRequest", "X-Admin-Password": pw },
      });
      if (!resp.ok) throw new Error("rejected");
      adminPassword = pw;
      sessionStorage.setItem("adminPassword", pw);
      refreshAdminBtn();
      if (cachedBooks) renderCollection(cachedBooks);
    } catch {
      window.alert("Admin password rejected.");
    }
  });
}

async function loadCollection() {
  collectionCountEl.textContent = "";
  collectionBodyEl.innerHTML = `<p>${T("st_loading")}</p>`;
  try {
    const { books } = await getJson("/api/books");
    cachedBooks = books;
    buildTagFilter(cachedBooks);
    applyCollectionFilter();
  } catch (err) {
    collectionBodyEl.innerHTML = `<p style="color:#c00">${esc(err.message)}</p>`;
  }
}

// --- Loans view (admin): who has what, with due-date highlighting ---
function showLoans() {
  if (scanning) stopCamera();
  cameraUi.hidden = true;
  collectionEl.hidden = true;
  loansEl.hidden = false;
  loadLoans();
}
if (loansBtn) loansBtn.addEventListener("click", showLoans);

async function loadLoans() {
  loansBodyEl.innerHTML = `<p>${T("st_loading")}</p>`;
  try {
    const resp = await fetch("/api/loans", { headers: { ...adminHeaders() } });
    const data = await resp.json().catch(() => ({}));
    if (!resp.ok) throw new Error(data.error || T("st_request_failed", { status: resp.status }));
    renderLoans(data.loans || []);
  } catch (err) {
    loansBodyEl.innerHTML = `<p style="color:#c00">${esc(err.message)}</p>`;
  }
}

function renderLoans(loans) {
  if (!loans.length) {
    loansBodyEl.innerHTML = `<p>${T("st_no_loans")}</p>`;
    return;
  }
  const rows = loans
    .map((l) => {
      const cls =
        l.status === "overdue" ? "loan-overdue" : l.status === "due_soon" ? "loan-soon" : "";
      const status =
        l.status === "overdue"
          ? T("loan_overdue_by", { n: -l.days_left })
          : l.status === "due_soon"
            ? T("loan_due_in", { n: l.days_left })
            : T("loan_ok");
      return `<tr class="${cls}">
          <td>${esc(l.title)}</td><td>${esc(l.borrower)}</td>
          <td>${esc(l.borrowed_at)}</td><td>${esc(l.due_at)}</td><td>${esc(status)}</td>
        </tr>`;
    })
    .join("");
  loansBodyEl.innerHTML = `<table class="loans-table">
      <thead><tr>
        <th>${T("loan_book")}</th><th>${T("loan_borrower")}</th>
        <th>${T("loan_borrowed")}</th><th>${T("loan_due")}</th><th>${T("loan_status")}</th>
      </tr></thead>
      <tbody>${rows}</tbody>
    </table>`;
}

// Client-side filter over the already-loaded books: free-text substring search
// (title / author / ISBN / tags) AND the exact-tag dropdown, combined.
const collectionSearch = document.getElementById("collection-search");
const tagFilter = document.getElementById("tag-filter");
let selectedTag = "";

function bookTags(b) {
  return (b.tags || "").split(",").map((s) => s.trim()).filter(Boolean);
}

// Rebuild the dropdown from the loaded books: every distinct tag with its count.
// Stays hidden while the library carries no tags at all.
function buildTagFilter(books) {
  const counts = new Map();
  for (const b of books) for (const t of bookTags(b)) counts.set(t, (counts.get(t) || 0) + 1);
  const tags = [...counts.keys()].sort((a, b) => a.localeCompare(b));
  tagFilter.hidden = tags.length === 0;
  if (selectedTag && !counts.has(selectedTag)) selectedTag = "";
  tagFilter.innerHTML =
    `<option value="">${esc(T("tag_all"))}</option>` +
    tags
      .map(
        (t) =>
          `<option value="${esc(t)}"${t === selectedTag ? " selected" : ""}>${esc(t)} (${counts.get(t)})</option>`,
      )
      .join("");
}
tagFilter.addEventListener("change", () => {
  selectedTag = tagFilter.value;
  applyCollectionFilter();
});

function applyCollectionFilter() {
  if (!cachedBooks) return;
  const q = (collectionSearch.value || "").trim().toLowerCase();
  let books = selectedTag
    ? cachedBooks.filter((b) => bookTags(b).includes(selectedTag))
    : cachedBooks;
  if (q)
    books = books.filter((b) =>
      [b.title, b.author, b.isbn, b.tags].some((v) => (v || "").toLowerCase().includes(q)),
    );
  renderCollection(books);
}
collectionSearch.addEventListener("input", applyCollectionFilter);

function renderCollection(books) {
  collectionCountEl.textContent =
    books.length === 0 ? "" : T("st_book_count", { n: books.length });
  if (books.length === 0) {
    collectionBodyEl.innerHTML = `<p>${T("st_no_books")}</p>`;
    return;
  }
  const ordered = sortBooks(books); // apply the sort to grid too (list re-sorts, harmless)
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
  collectionBodyEl.querySelectorAll(".book-card").forEach((card, i) => {
    const img = card.querySelector("img");
    if (img) watchCover(img, books[i].title);
    card.addEventListener("click", () => openCollectionAction(books[i], card, "grid"));
  });
}

// Click a column header to sort ascending; click again for descending.
let sortColumn = "added_at"; // default: most recently added first
let sortDir = -1; // 1 = ascending, -1 = descending

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

function renderList(books) {
  const cols = effectiveColumns(books);
  const head = cols
    .map((c) => {
      const arrow = sortColumn === c ? (sortDir === 1 ? " ▲" : " ▼") : "";
      return `<th class="sortable" data-col="${esc(c)}">${esc(colLabel(c))}${arrow}</th>`;
    })
    .join("");
  const sorted = sortBooks(books);
  const rows = sorted
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
      renderCollection(cachedBooks); // re-render with the new sort
    });
  });
  collectionBodyEl.querySelectorAll("tbody tr").forEach((row, i) => {
    row.addEventListener("click", (e) => {
      if (e.target.closest("button")) return;
      openCollectionAction(sorted[i], row, "list");
    });
  });
}

// One table cell. "available" renders as a coloured availability badge; everything
// else is its plain (escaped) DB value.
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

// --- Collection borrow/return action ---

function closeCollectionAction() {
  renderCollection(cachedBooks); // rebuilds cleanly; clears any active state
}

async function openCollectionAction(book, el, view) {
  if (el.classList.contains("col-active")) return;
  closeCollectionAction(); // close any previously open action (re-renders the list)

  // Re-find the freshly-rendered element by ISBN — robust to any sort order.
  const target = collectionBodyEl.querySelector(`[data-isbn="${book.isbn}"]`);
  if (!target) return;

  target.classList.add("col-active");

  let container;
  if (view === "list") {
    const span = effectiveColumns(cachedBooks).length;
    const actionTr = document.createElement("tr");
    actionTr.className = "col-action-row";
    actionTr.innerHTML = `<td colspan="${span}" class="col-action-cell"><em>${T("st_loading")}</em></td>`;
    target.insertAdjacentElement("afterend", actionTr);
    container = actionTr.querySelector(".col-action-cell");
  } else {
    target.innerHTML = `<div class="col-action-cell"><em>${T("st_loading")}</em></div>`;
    container = target.querySelector(".col-action-cell");
  }

  try {
    const { open_loans } = await getJson(`/api/book/${book.isbn}`);
    fillCollectionActionUI(book, open_loans, container);
  } catch (err) {
    container.innerHTML = `<p class="col-err">${esc(err.message)}</p>
      <button class="col-cancel-btn">Cancel</button>`;
    container.querySelector(".col-cancel-btn").addEventListener("click", closeCollectionAction);
  }
}

function fillCollectionActionUI(book, openLoans, container) {
  const avail = book.available ?? 0;

  container.innerHTML = `
    <span class="col-book-title">${esc(book.title || "")}</span>
    <div class="col-btns">
      ${avail > 0
        ? `<button class="col-borrow-btn">${esc(T("act_borrow"))}</button>`
        : `<span class="col-unavail">${esc(T("act_no_copies_avail"))}</span>`}
      ${openLoans.length > 0 ? `<button class="col-return-btn">${esc(T("act_return"))}</button>` : ""}
      ${isAdmin() ? `<button class="col-edit-btn">Edit</button><button class="col-delete-btn">Delete</button>` : ""}
      <button class="col-cancel-btn">${esc(T("act_cancel"))}</button>
    </div>
    <div class="col-form"></div>`;

  const formEl = container.querySelector(".col-form");

  container.querySelector(".col-cancel-btn").addEventListener("click", closeCollectionAction);

  // --- Admin: edit ---
  container.querySelector(".col-edit-btn")?.addEventListener("click", () => {
    const fields = [
      ["title", "Title"], ["author", "Author"], ["publisher", "Publisher"],
      ["year", "Year"], ["language", "Language"], ["tags", "Tags"],
      ["cover_url", "Cover URL"], ["total_count", "Copies"],
    ];
    formEl.innerHTML =
      fields
        .map(
          ([k, label]) =>
            `<label class="col-edit-row"><span>${label}</span>
               <input class="col-name-input" data-field="${k}" value="${esc(
              book[k] == null ? "" : String(book[k]),
            )}" /></label>`,
        )
        .join("") + `<button class="col-confirm-btn">Save</button>`;
    formEl.querySelector(".col-confirm-btn").addEventListener("click", async () => {
      const payload = {};
      formEl.querySelectorAll("input[data-field]").forEach((inp) => {
        payload[inp.dataset.field] = inp.value;
      });
      try {
        await patchJson(`/api/book/${book.isbn}`, payload);
        await loadCollection();
      } catch (err) {
        formEl.insertAdjacentHTML("beforeend", `<p class="col-err">${esc(err.message)}</p>`);
      }
    });
  });

  // --- Admin: delete ---
  container.querySelector(".col-delete-btn")?.addEventListener("click", async () => {
    if (!window.confirm(`Delete "${book.title}" and its loan history? This cannot be undone.`)) return;
    try {
      await deleteReq(`/api/book/${book.isbn}`);
      await loadCollection();
    } catch (err) {
      formEl.innerHTML = `<p class="col-err">${esc(err.message)}</p>`;
    }
  });

  container.querySelector(".col-borrow-btn")?.addEventListener("click", () => {
    formEl.innerHTML = `
      <input class="col-name-input" aria-label="${esc(T("ph_name"))}" placeholder="${esc(T("ph_name"))}" autocomplete="off" />
      <button class="col-confirm-btn">${esc(T("act_confirm"))}</button>`;
    const nameInput = formEl.querySelector(".col-name-input");
    nameInput.focus();
    const doBorrow = async () => {
      const borrower = nameInput.value.trim();
      if (!borrower) { nameInput.focus(); return; }
      try {
        await postJson(`/api/borrow/${book.isbn}`, { borrower });
        await loadCollection();
      } catch (err) {
        formEl.innerHTML = `<p class="col-err">${esc(err.message)}</p>`;
      }
    };
    formEl.querySelector(".col-confirm-btn").addEventListener("click", doBorrow);
    nameInput.addEventListener("keydown", (e) => { if (e.key === "Enter") doBorrow(); });
  });

  container.querySelector(".col-return-btn")?.addEventListener("click", () => {
    const loanHtml = openLoans
      .map((loan) => `
        <div class="col-loan-row">
          <span>${esc(loan.borrower)} · ${esc((loan.borrowed_at || "").slice(0, 10))}</span>
          <button class="col-return-one-btn" data-loan="${loan.loan_id}">${esc(T("act_return"))}</button>
        </div>`)
      .join("");
    formEl.innerHTML = loanHtml;
    formEl.querySelectorAll(".col-return-one-btn").forEach((btn) => {
      btn.addEventListener("click", async () => {
        try {
          await postJson(`/api/return/${book.isbn}`, { loan_id: Number(btn.dataset.loan) });
          await loadCollection();
        } catch (err) {
          formEl.innerHTML = `<p class="col-err">${esc(err.message)}</p>`;
        }
      });
    });
  });
}

// --- Mode switching ---
function syncModeUi() {
  const mode = currentMode();
  loansEl.hidden = true; // any mode selection leaves the admin loans view
  cameraUi.hidden = mode === "collection";
  collectionEl.hidden = mode !== "collection";
  titleSearch.hidden = mode !== "register"; // title search only makes sense for adding
  manualBook.hidden = !(mode === "register" && isAdmin()); // admin-only manual entry
  collapseManualForm(); // always start collapsed (toggle shown, fields hidden)
}
document.querySelectorAll('input[name="mode"]').forEach((radio) => {
  radio.addEventListener("change", () => {
    syncModeUi();
    if (currentMode() === "collection") {
      if (scanning) stopCamera();
      loadCollection();
    }
  });
});
syncModeUi(); // register is checked on load — show the title search

// --- Title search: find candidates by title, then register the chosen one ---
async function submitTitleSearch() {
  const q = titleInput.value.trim();
  if (!q || busy) return;
  busy = true;
  reviewing = true; // pause barcode detection while choosing
  setStatus(T("st_searching", { q }));
  resultEl.innerHTML = "";
  try {
    const { results } = await getJson(`/api/search?q=${encodeURIComponent(q)}`);
    renderSearchResults(q, results || []);
  } catch (err) {
    setStatus(err.message);
  }
  busy = false;
}

function renderSearchResults(q, results) {
  if (results.length === 0) {
    setStatus(T("st_no_results", { q }));
    reviewing = false;
    return;
  }
  setStatus(T("st_results", { n: results.length }));
  resultEl.innerHTML = `<div class="search-results">${results
    .map((b, i) => {
      const cSrc = coverSrc(b.cover_url);
      const cover = cSrc
        ? `<img src="${esc(cSrc)}" alt="" loading="lazy" onerror="this.remove()" />`
        : `<div class="no-cover"></div>`;
      const meta = [b.author, b.publisher, b.year].filter(Boolean).map(esc).join(" · ");
      return `<button class="search-result" data-i="${i}">
          ${cover}
          <span class="sr-info"><strong>${esc(b.title || "")}</strong>
          <small>${meta}<br />ISBN ${esc(b.isbn)}</small></span>
        </button>`;
    })
    .join("")}</div>
    <button class="sr-cancel">${esc(T("act_cancel"))}</button>`;
  resultEl.querySelectorAll(".search-result").forEach((el) => {
    el.addEventListener("click", () => {
      titleInput.value = "";
      lookupForRegister(results[Number(el.dataset.i)].isbn); // full fetch, then Add
    });
  });
  // Dismiss the results without choosing — clears the list and resumes scanning.
  resultEl.querySelector(".sr-cancel").addEventListener("click", () => resumeScanning());
}

titleGo.addEventListener("click", submitTitleSearch);
titleInput.addEventListener("keydown", (e) => { if (e.key === "Enter") submitTitleSearch(); });

// --- Manual entry (admin): add a book with no ISBN or one not found online ---
// Disclosure: you see either the "＋ Manual entry" toggle OR the open form, not both.
function showManualForm() {
  manualBookForm.hidden = false;
  manualBookToggle.hidden = true;
  mb.title.focus();
}
function collapseManualForm() {
  manualBookForm.hidden = true;
  manualBookToggle.hidden = false;
}
manualBookToggle.addEventListener("click", showManualForm);
document.getElementById("mb-hide").addEventListener("click", collapseManualForm);
document.getElementById("mb-save").addEventListener("click", submitManualBook);

async function submitManualBook() {
  const title = mb.title.value.trim();
  if (!title) {
    mb.title.focus();
    return;
  }
  const body = {
    title,
    author: mb.author.value.trim(),
    publisher: mb.publisher.value.trim(),
    year: mb.year.value.trim(),
    isbn: mb.isbn.value.trim(),
    total_count: parseInt(mb.copies.value, 10) || 1,
  };
  try {
    const data = await postJson("/api/register-manual", body, adminHeaders());
    Object.values(mb).forEach((el) => (el.value = ""));
    mb.copies.value = "1";
    collapseManualForm();
    const t = (data.book && data.book.title) || title;
    setStatus(T(data.status === "exists" ? "st_exists" : "st_manual_added", { title: t }));
    if (cachedBooks) loadCollection(); // keep the collection fresh for when you switch
  } catch (err) {
    setStatus(err.message);
  }
}

// POST helper: send JSON (optional), return parsed JSON or throw with the message.
async function postJson(url, body, extraHeaders) {
  const resp = await fetch(url, {
    method: "POST",
    headers: {
      "X-Requested-With": "XMLHttpRequest", // CSRF guard (server requires it)
      ...(body ? { "Content-Type": "application/json" } : {}),
      ...(extraHeaders || {}),
    },
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await resp.json().catch(() => ({}));
  if (!resp.ok) throw new Error(data.error || T("st_request_failed", { status: resp.status }));
  return data;
}

// Admin-only mutations — carry the admin password header.
async function patchJson(url, body) {
  const resp = await fetch(url, {
    method: "PATCH",
    headers: {
      "X-Requested-With": "XMLHttpRequest",
      "Content-Type": "application/json",
      ...adminHeaders(),
    },
    body: JSON.stringify(body),
  });
  const data = await resp.json().catch(() => ({}));
  if (!resp.ok) throw new Error(data.error || T("st_request_failed", { status: resp.status }));
  return data;
}

async function deleteReq(url) {
  const resp = await fetch(url, {
    method: "DELETE",
    headers: { "X-Requested-With": "XMLHttpRequest", ...adminHeaders() },
  });
  const data = await resp.json().catch(() => ({}));
  if (!resp.ok) throw new Error(data.error || T("st_request_failed", { status: resp.status }));
  return data;
}

// --- Register actions (two-step duplicate-copy confirm) ---
async function registerBook(book, confirm = false) {
  const url = `/api/register/${book.isbn}` + (confirm ? "?confirm=true" : "");
  setStatus(T("st_registering"));
  try {
    const data = await postJson(url, book); // send the book so the server needn't re-fetch
    if (data.status === "added") {
      setStatus(T("st_added", { title: data.book.title }));
      resumeScanning(true);
    } else if (data.status === "exists") {
      // Ask inline (NOT window.confirm): a blocking dialog freezes the camera
      // stream on mobile and it won't resume without restarting the camera.
      promptAddCopy(book, data.book);
    } else if (data.status === "copy_added") {
      setStatus(T("st_copy_added", { available: data.book.available, total: data.book.total_count }));
      resumeScanning(true);
    }
  } catch (err) {
    setStatus(err.message); // stay on the card so the user can retry
  }
}

// Duplicate scan: ask in the card whether to add another copy. Inline buttons
// instead of window.confirm so the live camera stream is never interrupted.
function promptAddCopy(book, existing) {
  const actions = resultEl.querySelector("#actions");
  if (!actions) return;
  setStatus(T("st_exists", { title: existing.title }));
  actions.innerHTML = `<button id="add-copy">${esc(T("act_yes_copy"))}</button>
                       <button id="no-copy">${esc(T("act_no"))}</button>`;
  actions.querySelector("#add-copy").addEventListener("click", () => registerBook(book, true));
  actions.querySelector("#no-copy").addEventListener("click", () => {
    setStatus(T("st_not_added"));
    resumeScanning(true);
  });
}

async function borrowBook(book) {
  const borrower = resultEl.querySelector("#borrower").value.trim();
  if (!borrower) {
    setStatus(T("st_enter_name"));
    return;
  }
  setStatus(T("st_recording_borrow"));
  try {
    const data = await postJson(`/api/borrow/${book.isbn}`, { borrower });
    setStatus(T("st_borrowed", { title: data.book.title, borrower, available: data.book.available, total: data.book.total_count }));
    resumeScanning(true);
  } catch (err) {
    setStatus(err.message);
  }
}

async function returnLoan(book, loanId) {
  setStatus(T("st_recording_return"));
  try {
    const data = await postJson(`/api/return/${book.isbn}`, { loan_id: loanId });
    setStatus(T("st_returned", { title: data.book.title, available: data.book.available, total: data.book.total_count }));
    resumeScanning(true);
  } catch (err) {
    setStatus(err.message);
  }
}

snapBtn.addEventListener("click", async () => {
  if (!scanning || reviewing || busy) return;

  // Freeze the current video frame onto the hidden canvas.
  snapCanvas.width = video.videoWidth;
  snapCanvas.height = video.videoHeight;
  snapCanvas.getContext("2d").drawImage(video, 0, 0);

  setStatus(T("st_decoding"));
  busy = true;
  try {
    const barcodes = await detector.detect(snapCanvas);
    if (barcodes.length === 0) {
      setStatus(T("st_no_barcode"));
      busy = false;
      return;
    }
    const ok = await handleScan(barcodes[0].rawValue);
    if (ok) reviewing = true;
  } catch (err) {
    setStatus(T("st_snap_error", { msg: err.message }));
  }
  busy = false;
});

async function submitManualIsbn() {
  const raw = manualIsbn.value.trim().replace(/[-\s]/g, "");
  if (!raw || reviewing || busy) return;
  manualIsbn.value = "";
  busy = true;
  const ok = await handleScan(raw);
  if (ok) reviewing = true;
  busy = false;
}

manualGo.addEventListener("click", submitManualIsbn);
manualIsbn.addEventListener("keydown", (e) => { if (e.key === "Enter") submitManualIsbn(); });

startBtn.addEventListener("click", startCamera);
stopBtn.addEventListener("click", stopCamera);
document.getElementById("reload-btn").addEventListener("click", loadCollection);
// Export downloads: the server sends Content-Disposition, so navigating triggers a
// download (and carries the page's Basic Auth) without leaving the page.
document.getElementById("export-csv").addEventListener("click", () => {
  window.location.href = "/api/export.csv";
});
document.getElementById("export-json").addEventListener("click", () => {
  window.location.href = "/api/export.json";
});

// CSV import (admin): a hidden file input drives the upload.
importBtn.addEventListener("click", () => importFile.click());
importFile.addEventListener("change", async () => {
  const file = importFile.files[0];
  if (!file) return;
  if (!window.confirm(T("cf_import"))) {
    importFile.value = "";
    return;
  }
  const body = new FormData();
  body.append("file", file);
  try {
    const resp = await fetch("/api/import.csv", {
      method: "POST",
      headers: { "X-Requested-With": "XMLHttpRequest", ...adminHeaders() },
      body,
    });
    const data = await resp.json().catch(() => ({}));
    if (!resp.ok) throw new Error(data.error || T("st_request_failed", { status: resp.status }));
    window.alert(T("st_imported", data));
    loadCollection();
  } catch (err) {
    window.alert(err.message);
  } finally {
    importFile.value = "";
  }
});
