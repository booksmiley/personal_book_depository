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
const statusEl = document.getElementById("status");
const resultEl = document.getElementById("result");

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

// Which action the current scan should perform: "register" | "borrow" | "return".
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
// to keep scanning (so a miss/“not in library” is retryable).
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
  return `
    ${book.cover_url ? `<img src="${book.cover_url}" alt="cover" />` : ""}
    <div>
      <strong>${book.title || "(no title)"}</strong><br />
      ${book.author || ""}<br />
      <small>ISBN ${book.isbn}</small>${counts}
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

startBtn.addEventListener("click", startCamera);
stopBtn.addEventListener("click", stopCamera);
