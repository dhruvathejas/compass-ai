// "I have it running on my main PC": check that an address really is a Compass AI host, then hand over to its sign-in.
//
// The check is done by THIS browser asking the host's public /api/hello (the server here never fetches addresses),
// so nothing can be used to make a Compass server probe someone else's network.
import { h } from "./ui.js";

const KEY = "compass-host";
const form = document.getElementById("connect-form");
const input = document.getElementById("address");
const button = document.getElementById("connect-btn");
const result = document.getElementById("result");
const saved = document.getElementById("saved");

const LOOPBACK = new Set(["localhost", "127.0.0.1", "[::1]", "::1"]);
const isLoopback = (host) => LOOPBACK.has(host.toLowerCase());

/** Turn what a person typed into an origin like http://192.168.1.20:5000, or explain what's wrong. */
export function normalise(raw) {
  let text = raw.trim();
  if (!text) return { error: "Enter the address of your main PC first." };
  if (!/^[a-z][a-z0-9+.-]*:\/\//i.test(text)) {
    const host = text.split(/[/?#]/)[0].replace(/:\d+$/, "");
    const plain = /^\d{1,3}(\.\d{1,3}){3}$/.test(host) || host.endsWith(".local") || !host.includes(".") || isLoopback(host);
    text = `${plain ? "http" : "https"}://${text}`;
  }
  let url;
  try { url = new URL(text); } catch { return { error: "That doesn't look like an address. Try something like 192.168.1.20:5000." }; }
  if (!["http:", "https:"].includes(url.protocol)) return { error: "Use an http:// or https:// address." };
  if (url.username || url.password) return { error: "Leave passwords out of the address. You sign in on the next step." };
  if (isLoopback(url.hostname) && !isLoopback(location.hostname)) {
    return { error: `“${url.hostname}” always means the device you're holding, not your main PC. Use the main PC's network address (like 192.168.1.20:5000) or its tunnel link.` };
  }
  return { origin: url.origin };
}

async function probe(origin) {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), 7000);
  try {
    const res = await fetch(`${origin}/api/hello`, {
      mode: "cors", credentials: "omit", cache: "no-store", referrerPolicy: "no-referrer", signal: ctrl.signal,
    });
    if (!res.ok) return { ok: false, kind: "http", status: res.status };
    const data = await res.json().catch(() => null);
    if (!data || data.app !== "compass-ai") return { ok: false, kind: "other" };
    return { ok: true, data };
  } catch (err) {
    return { ok: false, kind: err.name === "AbortError" ? "timeout" : "network" };
  } finally {
    clearTimeout(timer);
  }
}

function explain(origin, r) {
  if (r.kind === "timeout") return ["No answer from that address.", "The main PC may be asleep or off, Compass AI may not be running on it, or this address is wrong. Windows Firewall can also block it."];
  if (r.kind === "http") return [`That address answered with an error (HTTP ${r.status}).`, "Check the port number. Compass AI uses :5000 unless it was started with COMPASS_PORT."];
  if (r.kind === "other") return ["That address is a website, but it isn't Compass AI.", "Check the address and port on the main PC's screen."];
  if (location.protocol === "https:" && origin.startsWith("http:")) {
    return ["Your browser blocked this connection.", "This page is on https, which isn't allowed to call a plain http address. Open this page over http too, or use the host's https tunnel link."];
  }
  return ["Couldn't reach that address.", "Is Compass AI running on the main PC (python app.py --lan for the local network)? Are both devices on the same Wi-Fi, or are you using the tunnel link? Open “It says it can't reach the main PC” below for the full checklist."];
}

function show(kind, title, body, ...extra) {
  result.className = `result ${kind}`;
  result.replaceChildren(h("strong", { text: title }), body ? h("p", { text: body }) : null, ...extra);
  result.hidden = false;
}

function remember(origin) { try { localStorage.setItem(KEY, origin); } catch { /* private mode */ } }
function recall() { try { return localStorage.getItem(KEY); } catch { return null; } }

async function connect(raw) {
  const parsed = normalise(raw);
  if (parsed.error) return show("bad", "Check the address", parsed.error);
  const { origin } = parsed;
  button.disabled = true;
  show("busy", "Looking for Compass AI…", `Asking ${origin}`);

  const r = origin === location.origin ? { ok: true, data: await fetch("/api/hello").then((x) => x.json()).catch(() => ({})) } : await probe(origin);
  button.disabled = false;
  if (!r.ok) {
    const [title, body] = explain(origin, r);
    return show("bad", title, body, h("button", { class: "btn", type: "button", text: "Try again", onclick: () => connect(raw) }));
  }

  remember(origin);
  const setup = r.data && r.data.setup_needed;
  show("good", "Found Compass AI", setup
    ? `${origin} has no accounts yet. Open it on that PC and create the owner account first.`
    : `${origin} is a Compass AI host. Sign in there, and you'll be asked to authorize this device the first time.`,
  h("div", { class: "actions" },
    h("a", { class: "btn primary", href: `${origin}/login`, text: "Continue to sign in →" }),
    setup ? null : h("a", { class: "btn", href: `${origin}/register`, text: "Create an account" })));
  result.querySelector("a")?.focus();
}

form.addEventListener("submit", (e) => { e.preventDefault(); connect(input.value); });

// A host used before: offer a one-tap reconnect, with its live status.
const last = recall();
if (last) {
  saved.hidden = false;
  document.getElementById("saved-addr").textContent = last;
  const state = document.getElementById("saved-state");
  state.textContent = "checking…";
  probe(last).then((r) => {
    state.textContent = r.ok ? "online" : "not reachable";
    state.className = `pill ${r.ok ? "good" : "warn"}`;
  });
  document.getElementById("saved-go").addEventListener("click", () => { input.value = last; connect(last); });
  document.getElementById("saved-forget").addEventListener("click", () => {
    try { localStorage.removeItem(KEY); } catch { /* ignore */ }
    saved.hidden = true;
  });
}
