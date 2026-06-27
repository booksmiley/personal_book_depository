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
const statusEl = document.getElementById("status");
const resultEl = document.getElementById("result");
const cameraUi = document.getElementById("camera-ui");
const collectionEl = document.getElementById("collection");
const collectionCountEl = document.getElementById("collection-count");
const collectionBodyEl = document.getElementById("collection-body");

const detector = new BarcodeDetector({ formats: ["ean_13"] });

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
    setStatus(`Camera error: ${err.message}`);
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
  setStatus("Stopped.");
}

// Clear the card and go (back) to actively scanning. `keepStatus` leaves the
// caller's message (e.g. "Borrowed ✅") instead of the scan prompt.
function resumeScanning(keepStatus = false) {
  resultEl.innerHTML = "";
  reviewing = false;
  candidate = null;
  candidateCount = 0;
  if (!keepStatus) setStatus(`Point the camera at a barcode… (${currentMode()})`);
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
    setStatus(`Scan error: ${err.message}`);
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
    throw new Error(error || `Request failed (${resp.status}).`);
  }
  return resp.json();
}

// --- Register: fetch metadata from the online API, then offer to add ---
async function lookupForRegister(isbn) {
  setStatus(`Looking up ${isbn}…`);
  resultEl.innerHTML = "";
  try {
    const book = await getJson(`/api/lookup/${isbn}`);
    renderRegisterCard(book);
    setStatus(`Found "${book.title}" — add it, or scan the next book.`);
    return true;
  } catch (err) {
    setStatus(err.message);
    return false;
  }
}

// --- Borrow / return: look the book up in the DB (no network), then act ---
async function lookupForAction(isbn, mode) {
  setStatus(`Looking up ${isbn}…`);
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

// Shared book blurb (cover + title/author + availability when known).
function bookInfoHtml(book, showCounts) {
  const counts =
    showCounts && book.available != null
      ? `<br /><small>${book.available} of ${book.total_count} available</small>`
      : "";
  // Only allow https:// cover URLs — blocks javascript: and data: schemes.
  const safeCover = /^https:\/\//.test(book.cover_url || "") ? book.cover_url : "";
  return `
    ${safeCover ? `<img src="${esc(safeCover)}" alt="cover" />` : ""}
    <div>
      <strong>${esc(book.title || "(no title)")}</strong><br />
      ${esc(book.author || "")}<br />
      <small>ISBN ${esc(book.isbn || "")}</small>${counts}
      <div id="actions"></div>
    </div>`;
}

function renderRegisterCard(book) {
  resultEl.innerHTML = bookInfoHtml(book, false);
  const actions = resultEl.querySelector("#actions");
  actions.innerHTML = `<button id="add">Add to library</button>
                       <button id="next">Scan next book</button>`;
  actions.querySelector("#add").addEventListener("click", () => registerBook(book));
  actions.querySelector("#next").addEventListener("click", () => resumeScanning());
}

function renderBorrowCard(book) {
  resultEl.innerHTML = bookInfoHtml(book, true);
  const actions = resultEl.querySelector("#actions");
  const none = book.available <= 0;
  actions.innerHTML = `
    <input id="borrower" placeholder="Your name" autocomplete="off" />
    <button id="borrow" ${none ? "disabled" : ""}>Borrow</button>
    <button id="next">Scan next book</button>`;
  if (none) setStatus(`"${book.title}" has no copies available right now.`);
  else setStatus(`"${book.title}" — enter your name, then tap Borrow.`);
  actions.querySelector("#borrow").addEventListener("click", () => borrowBook(book));
  actions.querySelector("#next").addEventListener("click", () => resumeScanning());
}

// Return: list the open loans and let the user tap the one they're closing.
function renderReturnCard(book, openLoans) {
  resultEl.innerHTML = bookInfoHtml(book, true);
  const actions = resultEl.querySelector("#actions");

  if (openLoans.length === 0) {
    actions.innerHTML = `<button id="next">Scan next book</button>`;
    setStatus(`No copies of "${book.title}" are currently out.`);
  } else {
    const rows = openLoans
      .map(
        (loan) => `
        <div class="loan">
          <span>${esc(loan.borrower)} · ${esc((loan.borrowed_at || "").slice(0, 10))}</span>
          <button class="return-one" data-loan="${loan.loan_id}">Return this</button>
        </div>`,
      )
      .join("");
    actions.innerHTML = `${rows}<button id="next">Scan next book</button>`;
    setStatus(`Who's returning "${book.title}"? Tap their loan.`);
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
  if (cachedBooks) renderCollection(cachedBooks);
}

viewGridBtn.addEventListener("click", () => setCollectionView("grid"));
viewListBtn.addEventListener("click", () => setCollectionView("list"));

// Sync toggle button state on load.
viewGridBtn.classList.toggle("active", collectionView === "grid");
viewListBtn.classList.toggle("active", collectionView === "list");

async function loadCollection() {
  collectionCountEl.textContent = "";
  collectionBodyEl.innerHTML = "<p>Loading…</p>";
  try {
    const { books } = await getJson("/api/books");
    cachedBooks = books;
    renderCollection(books);
  } catch (err) {
    collectionBodyEl.innerHTML = `<p style="color:#c00">${esc(err.message)}</p>`;
  }
}

function renderCollection(books) {
  collectionCountEl.textContent =
    books.length === 0 ? "" : `${books.length} book${books.length === 1 ? "" : "s"}`;
  if (books.length === 0) {
    collectionBodyEl.innerHTML = "<p>No books registered yet.</p>";
    return;
  }
  if (collectionView === "list") renderList(books);
  else renderGrid(books);
}

function renderGrid(books) {
  const cards = books
    .map((book) => {
      const cover = book.cover_url
        ? `<img src="${esc(book.cover_url)}" alt="cover" loading="lazy" />`
        : `<div class="no-cover">${esc(book.title || "")}</div>`;
      const avail = book.available ?? 0;
      const total = book.total_count ?? 1;
      const badgeClass = avail > 0 ? "badge-ok" : "badge-out";
      const badgeLabel = avail > 0 ? `${avail}/${total}` : "Out";
      return `
        <div class="book-card">
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
    card.addEventListener("click", () => openCollectionAction(books[i], card, "grid"));
  });
}

function renderList(books) {
  const rows = books
    .map((book) => {
      const avail = book.available ?? 0;
      const total = book.total_count ?? 1;
      const badgeClass = avail > 0 ? "badge-ok" : "badge-out";
      const badgeLabel = avail > 0 ? `${avail}/${total}` : "Out";
      return `
        <tr>
          <td>${esc(book.title || "(no title)")}</td>
          <td>${esc(book.author || "")}</td>
          <td>${esc(book.year || "")}</td>
          <td>${esc(book.publisher || "")}</td>
          <td><span class="badge ${badgeClass}">${badgeLabel}</span></td>
        </tr>`;
    })
    .join("");
  collectionBodyEl.innerHTML = `
    <table class="book-list">
      <thead><tr>
        <th>Title</th><th>Author</th><th>Year</th><th>Publisher</th><th>Status</th>
      </tr></thead>
      <tbody>${rows}</tbody>
    </table>`;
  collectionBodyEl.querySelectorAll("tbody tr").forEach((row, i) => {
    row.addEventListener("click", (e) => {
      if (e.target.closest("button")) return;
      openCollectionAction(books[i], row, "list");
    });
  });
}

// --- Collection borrow/return action ---

function closeCollectionAction() {
  renderCollection(cachedBooks); // rebuilds cleanly; clears any active state
}

async function openCollectionAction(book, el, view) {
  if (el.classList.contains("col-active")) return;
  closeCollectionAction(); // close any previously open action first

  // Re-query the element after re-render triggered by closeCollectionAction.
  // For grid: find the card whose ISBN matches. For list: find the matching row.
  let target;
  if (view === "grid") {
    const allCards = collectionBodyEl.querySelectorAll(".book-card");
    const idx = cachedBooks.findIndex((b) => b.isbn === book.isbn);
    target = allCards[idx];
  } else {
    const allRows = collectionBodyEl.querySelectorAll("tbody tr");
    const idx = cachedBooks.findIndex((b) => b.isbn === book.isbn);
    target = allRows[idx];
  }
  if (!target) return;

  target.classList.add("col-active");

  let container;
  if (view === "list") {
    const actionTr = document.createElement("tr");
    actionTr.className = "col-action-row";
    actionTr.innerHTML = `<td colspan="5" class="col-action-cell"><em>Loading…</em></td>`;
    target.insertAdjacentElement("afterend", actionTr);
    container = actionTr.querySelector(".col-action-cell");
  } else {
    target.innerHTML = `<div class="col-action-cell"><em>Loading…</em></div>`;
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
        ? `<button class="col-borrow-btn">Borrow</button>`
        : `<span class="col-unavail">No copies available</span>`}
      ${openLoans.length > 0 ? `<button class="col-return-btn">Return</button>` : ""}
      <button class="col-cancel-btn">Cancel</button>
    </div>
    <div class="col-form"></div>`;

  const formEl = container.querySelector(".col-form");

  container.querySelector(".col-cancel-btn").addEventListener("click", closeCollectionAction);

  container.querySelector(".col-borrow-btn")?.addEventListener("click", () => {
    formEl.innerHTML = `
      <input class="col-name-input" placeholder="Your name" autocomplete="off" />
      <button class="col-confirm-btn">Confirm</button>`;
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
          <button class="col-return-one-btn" data-loan="${loan.loan_id}">Return</button>
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
document.querySelectorAll('input[name="mode"]').forEach((radio) => {
  radio.addEventListener("change", () => {
    const isCollection = currentMode() === "collection";
    cameraUi.hidden = isCollection;
    collectionEl.hidden = !isCollection;
    if (isCollection) {
      if (scanning) stopCamera();
      loadCollection();
    }
  });
});

// POST helper: send JSON (optional), return parsed JSON or throw with the message.
async function postJson(url, body) {
  const resp = await fetch(url, {
    method: "POST",
    headers: body ? { "Content-Type": "application/json" } : {},
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await resp.json().catch(() => ({}));
  if (!resp.ok) throw new Error(data.error || `Request failed (${resp.status}).`);
  return data;
}

// --- Register actions (two-step duplicate-copy confirm) ---
async function registerBook(book, confirm = false) {
  const url = `/api/register/${book.isbn}` + (confirm ? "?confirm=true" : "");
  setStatus("Registering…");
  try {
    const data = await postJson(url, book); // send the book so the server needn't re-fetch
    if (data.status === "added") {
      setStatus(`Added ✅ "${data.book.title}" — scanning for the next book.`);
      resumeScanning(true);
    } else if (data.status === "exists") {
      if (window.confirm(`"${data.book.title}" is already registered — add another copy?`)) {
        return registerBook(book, true);
      }
      setStatus("Okay, not added — scanning for the next book.");
      resumeScanning(true);
    } else if (data.status === "copy_added") {
      setStatus(`Copy added — ${data.book.available} of ${data.book.total_count} available. Scanning…`);
      resumeScanning(true);
    }
  } catch (err) {
    setStatus(err.message); // stay on the card so the user can retry
  }
}

async function borrowBook(book) {
  const borrower = resultEl.querySelector("#borrower").value.trim();
  if (!borrower) {
    setStatus("Please enter your name.");
    return;
  }
  setStatus("Recording borrow…");
  try {
    const data = await postJson(`/api/borrow/${book.isbn}`, { borrower });
    setStatus(`Borrowed ✅ "${data.book.title}" to ${borrower} — ${data.book.available} of ${data.book.total_count} left. Scanning…`);
    resumeScanning(true);
  } catch (err) {
    setStatus(err.message);
  }
}

async function returnLoan(book, loanId) {
  setStatus("Recording return…");
  try {
    const data = await postJson(`/api/return/${book.isbn}`, { loan_id: loanId });
    setStatus(`Returned ✅ "${data.book.title}" — ${data.book.available} of ${data.book.total_count} available. Scanning…`);
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

  setStatus("Decoding snapshot…");
  busy = true;
  try {
    const barcodes = await detector.detect(snapCanvas);
    if (barcodes.length === 0) {
      setStatus("No barcode found — hold steady and try again.");
      busy = false;
      return;
    }
    const ok = await handleScan(barcodes[0].rawValue);
    if (ok) reviewing = true;
  } catch (err) {
    setStatus(`Snap error: ${err.message}`);
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
