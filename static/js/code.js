// The Code button: same as typing `cd workspace` then `code .` on the host PC.
// The server refuses unless the request comes from the host PC itself, so the button explains instead of failing.
import { api, ApiError } from "./api.js";

export function initCodeButton() {
  const btn = document.getElementById("open-code");
  const note = document.getElementById("code-note");
  if (!btn) return;
  const local = btn.dataset.local === "yes";
  let busy = false;

  const say = (text, bad = false) => { note.textContent = text; note.classList.toggle("bad", bad); };

  btn.addEventListener("click", async () => {
    if (!local) return say("Open Compass AI on the computer that runs it to use the Code button.", true);
    if (busy) return;
    busy = true;
    btn.setAttribute("aria-busy", "true");
    say("Opening VS Code in your workspace…");
    try {
      const { path } = await api("/api/workspace/open-in-code", { method: "POST" });
      say(`VS Code is opening ${path}`);
    } catch (err) {
      say(err instanceof ApiError ? err.message : "Couldn't start VS Code.", true);
    } finally {
      busy = false;
      btn.removeAttribute("aria-busy");
    }
  });
}
