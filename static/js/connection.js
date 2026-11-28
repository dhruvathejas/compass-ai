// Connection status: if the host can't be reached (host asleep, Wi-Fi dropped, tunnel restarted) show a banner,
// keep asking the host's public /api/hello until it answers, then go back to normal. Nothing is lost meanwhile.
const banner = document.getElementById("conn-banner");
const label = document.getElementById("conn-label");

let offline = false;
let timer = null;
let attempts = 0;

async function ping() {
  try {
    const res = await fetch("/api/hello", { cache: "no-store", credentials: "omit" });
    return res.ok;
  } catch {
    return false;
  }
}

function show(text, withRetry) {
  banner.replaceChildren(document.createTextNode(text));
  if (withRetry) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "btn";
    btn.textContent = "Retry now";
    btn.addEventListener("click", check);
    banner.append(btn);
  }
  banner.hidden = false;
}

async function check() {
  clearTimeout(timer);
  attempts += 1;
  if (await ping()) return recover();
  show(`Can't reach the host${attempts > 1 ? ` (tried ${attempts} times)` : ""}. Your messages are saved; trying again…`, true);
  timer = setTimeout(check, Math.min(15000, 2000 * attempts));
}

function recover() {
  clearTimeout(timer);
  offline = false;
  attempts = 0;
  banner.hidden = true;
  if (label?.dataset.was) label.textContent = label.dataset.was;
}

function goOffline() {
  if (offline) return;
  offline = true;
  if (label) { label.dataset.was = label.textContent; label.textContent = "Reconnecting…"; }
  check();
}

export function initConnection() {
  window.addEventListener("compass:offline", goOffline);
  window.addEventListener("compass:reachable", () => { if (offline) recover(); });
  window.addEventListener("offline", goOffline);
  window.addEventListener("online", () => { if (offline) check(); });
}
