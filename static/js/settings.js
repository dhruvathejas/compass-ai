// Settings dialog: model, appearance, sign out everywhere. Every change saves immediately.
import { api } from "./api.js";

const dialog = document.getElementById("settings-dialog");
const select = document.getElementById("model-select");
const status = document.getElementById("model-status");
const themeGroup = document.getElementById("theme-group");
const revoke = document.getElementById("revoke");
const revokeYes = document.getElementById("revoke-yes");
const revokeNo = document.getElementById("revoke-no");

function say(message, bad = false) {
  status.textContent = message;
  status.classList.toggle("bad", bad);
}

function applyTheme(theme) {
  if (theme === "auto") delete document.documentElement.dataset.theme;
  else document.documentElement.dataset.theme = theme;
}

async function loadModels() {
  say("Looking for models…");
  select.disabled = true;
  try {
    const { models, error, current } = await api("/api/models");
    const names = models.includes(current) ? models : [current, ...models];
    select.replaceChildren(...names.map((n) => {
      const opt = document.createElement("option");
      opt.value = n;
      opt.textContent = models.includes(n) ? n : `${n} (not installed)`;
      return opt;
    }));
    select.value = current;
    select.disabled = false;
    if (error) say(error, true);
    else if (!models.includes(current)) say(`Ollama doesn't have '${current}'. Pick another or pull it with: ollama pull ${current}`, true);
    else say(`${models.length} model${models.length === 1 ? "" : "s"} available.`);
  } catch (err) {
    say(err.message, true);
  }
}

async function loadSettings() {
  const s = await api("/api/settings");
  themeGroup.querySelector(`input[value="${s.theme}"]`).checked = true;
}

export function initSettings() {
  document.getElementById("open-settings").addEventListener("click", async () => {
    resetRevoke();
    dialog.showModal();
    await Promise.all([loadSettings().catch((e) => say(e.message, true)), loadModels()]);
  });
  document.getElementById("close-settings").addEventListener("click", () => dialog.close());
  dialog.addEventListener("click", (e) => { if (e.target === dialog) dialog.close(); });   // click on the backdrop

  document.getElementById("refresh-models").addEventListener("click", loadModels);
  select.addEventListener("change", async () => {
    try {
      await api("/api/settings", { method: "PUT", body: { model: select.value } });
      say(`Now using ${select.value}.`);
    } catch (err) { say(err.message, true); }
  });

  themeGroup.addEventListener("change", async (e) => {
    const theme = e.target.value;
    const before = document.documentElement.dataset.theme || "auto";
    applyTheme(theme);
    try {
      await api("/api/settings", { method: "PUT", body: { theme } });
    } catch (err) {
      applyTheme(before);
      themeGroup.querySelector(`input[value="${before}"]`).checked = true;
      say(err.message, true);
    }
  });

  function resetRevoke() { revoke.hidden = false; revokeYes.hidden = revokeNo.hidden = true; }
  revoke.addEventListener("click", () => { revoke.hidden = true; revokeYes.hidden = revokeNo.hidden = false; revokeNo.focus(); });
  revokeNo.addEventListener("click", () => { resetRevoke(); revoke.focus(); });
  revokeYes.addEventListener("click", async () => {
    try {
      await api("/api/sessions/revoke", { method: "POST" });
      location.assign("/login");
    } catch (err) { say(err.message, true); resetRevoke(); }
  });
}
