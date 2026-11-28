// Free-tier meter (server mode only): how many messages are left today. Hidden on a personal host and for the owner.
import { api } from "./api.js";

const box = document.getElementById("usage");
const text = document.getElementById("usage-text");
const fill = document.getElementById("usage-fill");

function time(ts) {
  return new Date(ts * 1000).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
}

export async function refreshUsage() {
  if (!box) return;
  try {
    const u = await api("/api/usage");
    if (!u.limited || !u.daily_limit) { box.hidden = true; return; }
    const left = Math.max(0, u.daily_limit - u.used);
    text.textContent = left
      ? `${left} of ${u.daily_limit} free messages left today`
      : `Daily limit reached. Resets at ${time(u.resets)}.`;
    fill.style.width = `${Math.min(100, (u.used / u.daily_limit) * 100)}%`;
    box.classList.toggle("low", left <= Math.ceil(u.daily_limit * 0.1));
    box.hidden = false;
  } catch { /* the meter is a convenience; never block the chat on it */ }
}

export function initUsage() {
  if (!box) return;
  refreshUsage();
  window.addEventListener("compass:sent", refreshUsage);     // chats.js fires this after every message
}
