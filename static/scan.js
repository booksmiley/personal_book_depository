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

// Three clear states drive the flow:
//   scanning=true,  reviewing=false  -> camera live, actively looking for a barcode
//   scanning=true,  reviewing=true   -> a book was found; detection PAUSED on the card
//                                       until the user picks an action
//   scanning=false                   -> camera off
let stream = null;
let scanning = false;
let reviewing = false; // a found book is on screen; pause detection until user acts
let busy = false; // a network request is in flight

// Require the same value on a few consecutive frames before trusting it, so a
// single noisy misread doesn't fire a lookup.
const CONFIRM_FRAMES = 3;
let candidate = null;
let candidateCount = 0;

function setStatus(msg) {
  statusEl.textContent = msg;
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

// Clear the card and go (back) to actively scanning. `keepStatus` lets a caller
// leave its own message (e.g. "Added ✅") in place instead of the scan prompt.
function resumeScanning(keepStatus = false) {
  resultEl.innerHTML = "";
  reviewing = false;
  candidate = null;
  candidateCount = 0;
  if (!keepStatus) setStatus("Point the camera at a barcode…");
}

async function scanLoop() {
  if (!scanning) return;
  try {
    // Only look while we're actively scanning — not while a book is under review
    // and not while a request is in flight.
    if (!busy && !reviewing) {
      const barcodes = await detector.detect(video);
      const raw = barcodes.length > 0 ? barcodes[0].rawValue : null;
      if (raw) {
        // Count consecutive identical reads before trusting the value.
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
          const ok = await lookupIsbn(raw);
          busy = false;
          // On success the card is shown and we pause for the user's decision.
          // On failure we stay in scanning mode so the same book can be retried.
          if (ok) reviewing = true;
        }
      }
    }
  } catch (err) {
    setStatus(`Scan error: ${err.message}`);
    busy = false;
  }
  requestAnimationFrame(scanLoop);
}

// Returns true on success, false on any failure (so the loop can keep scanning).
async function lookupIsbn(isbn) {
  setStatus(`Looking up ${isbn}…`);
  resultEl.innerHTML = "";
  try {
    const resp = await fetch(`/api/lookup/${isbn}`);
    if (!resp.ok) {
      const { error } = await resp.json().catch(() => ({}));
      setStatus(error || `Lookup failed (${resp.status}).`);
      return false;
    }
    const book = await resp.json();
    renderBook(book);
    setStatus(`Found "${book.title}" — add it, or scan the next book.`);
    return true;
  } catch (err) {
    setStatus(`Network error: ${err.message}`);
    return false;
  }
}

function renderBook(book) {
  resultEl.innerHTML = `
    ${book.cover_url ? `<img src="${book.cover_url}" alt="cover" />` : ""}
    <div>
      <strong>${book.title || "(no title)"}</strong><br />
      ${book.author || ""}<br />
      <small>${book.publisher || ""} ${book.year || ""}</small><br />
      <small>ISBN ${book.isbn} · via ${book.source || "?"}</small><br />
      <button id="add">Add to library</button>
      <button id="next">Scan next book</button>
    </div>`;
  // Wire the buttons each time we render (the elements are brand new).
  // "Add" passes the whole book so register needn't re-fetch it from the slow API.
  document.getElementById("add").addEventListener("click", () => registerBook(book));
  document.getElementById("next").addEventListener("click", () => resumeScanning());
}

// POST the scanned book to the register route, handling the two-step duplicate flow.
// `book` is the object we already fetched at scan time — we send it so the server
// can store it directly instead of hitting Open Library a second time.
async function registerBook(book, confirm = false) {
  const url = `/api/register/${book.isbn}` + (confirm ? "?confirm=true" : "");
  setStatus("Registering…");
  try {
    const resp = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(book),
    });
    if (!resp.ok) {
      const { error } = await resp.json().catch(() => ({}));
      setStatus(error || `Register failed (${resp.status}).`);
      return; // stay on the card so the user can retry or scan next
    }
    const data = await resp.json(); // { status, book }

    if (data.status === "added") {
      setStatus(`Added ✅ "${data.book.title}" — scanning for the next book.`);
      resumeScanning(true);
    } else if (data.status === "exists") {
      // Already in the library — ask before adding another physical copy.
      if (window.confirm(`"${data.book.title}" is already registered — add another copy?`)) {
        return registerBook(book, true); // re-POST with ?confirm=true
      }
      setStatus("Okay, not added — scanning for the next book.");
      resumeScanning(true);
    } else if (data.status === "copy_added") {
      setStatus(`Copy added — ${data.book.available} of ${data.book.total_count} available. Scanning…`);
      resumeScanning(true);
    }
  } catch (err) {
    setStatus(`Network error: ${err.message}`);
  }
}

startBtn.addEventListener("click", startCamera);
stopBtn.addEventListener("click", stopCamera);
