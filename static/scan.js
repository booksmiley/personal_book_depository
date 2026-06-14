// Browser-side slice: turn on the camera, decode an EAN-13 barcode, and hand the
// resulting ISBN to the Python backend. This part HAS to live in the browser (only
// the browser can reach the camera). Keep it thin — all the book logic is in Python.
//
// BarcodeDetector is native in Chrome/Edge but missing/unreliable on Safari/iOS,
// so we import a polyfill that gives the same API everywhere. If this CDN import
// ever gives you trouble, a common alternative is `@zxing/browser`.
import { BarcodeDetector } from "https://esm.sh/@sec-ant/barcode-detector@2/pure";

const video = document.getElementById("video");
const startBtn = document.getElementById("start");
const stopBtn = document.getElementById("stop");
const statusEl = document.getElementById("status");
const resultEl = document.getElementById("result");

const detector = new BarcodeDetector({ formats: ["ean_13"] });

let stream = null;
let scanning = false;
let lastIsbn = null; // crude guard so one barcode doesn't fire dozens of lookups

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
    startBtn.disabled = true;
    stopBtn.disabled = false;
    setStatus("Point the camera at a barcode…");
    requestAnimationFrame(scanLoop);
  } catch (err) {
    // Most common cause on a phone: not served over HTTPS (camera is blocked).
    setStatus(`Camera error: ${err.message}`);
  }
}

function stopCamera() {
  scanning = false;
  stream?.getTracks().forEach((track) => track.stop());
  stream = null;
  startBtn.disabled = false;
  stopBtn.disabled = true;
  setStatus("Stopped.");
}

async function scanLoop() {
  if (!scanning) return;
  try {
    const barcodes = await detector.detect(video);
    if (barcodes.length > 0) {
      const raw = barcodes[0].rawValue;
      // TODO (your exercise): smarter debouncing. Right now we only skip an
      // *identical* consecutive scan. Consider: require the same value on 2-3
      // frames in a row before trusting it, and a short cooldown after a lookup.
      if (raw !== lastIsbn) {
        lastIsbn = raw;
        await lookupIsbn(raw);
      }
    }
  } catch (err) {
    setStatus(`Scan error: ${err.message}`);
  }
  requestAnimationFrame(scanLoop);
}

async function lookupIsbn(isbn) {
  setStatus(`Looking up ${isbn}…`);
  resultEl.innerHTML = "";
  try {
    const resp = await fetch(`/api/lookup/${isbn}`);
    if (!resp.ok) {
      const { error } = await resp.json().catch(() => ({}));
      setStatus(error || `Lookup failed (${resp.status}).`);
      return;
    }
    const book = await resp.json();
    renderBook(book);
    setStatus("Found it.");
  } catch (err) {
    setStatus(`Network error: ${err.message}`);
  }
}

function renderBook(book) {
  resultEl.innerHTML = `
    ${book.cover_url ? `<img src="${book.cover_url}" alt="cover" />` : ""}
    <div>
      <strong>${book.title || "(no title)"}</strong><br />
      ${book.author || ""}<br />
      <small>${book.publisher || ""} ${book.year || ""}</small><br />
      <small>ISBN ${book.isbn} · via ${book.source || "?"}</small>
    </div>`;
  // NEXT STEP: this is where the "register" flow begins — add a "Add to library"
  // button that POSTs to a Python route which writes the book into the DB.
}

startBtn.addEventListener("click", startCamera);
stopBtn.addEventListener("click", stopCamera);
