import { BRAND } from "./api.js";
import { initChats } from "./chats.js";
import { initSettings } from "./settings.js";

const phone = window.matchMedia("(max-width: 760px)");

function isDark() {
  const t = document.documentElement.dataset.theme;
  return t ? t === "dark" : window.matchMedia("(prefers-color-scheme: dark)").matches;
}

// Splash: the logo glides into the header once per tab. The glide lands an ink lockup, so on a dark
// theme the splash simply fades out instead.
if (window.CompassSplash) {
  window.CompassSplash.play({
    assetBase: BRAND,
    target: isDark() ? null : phone.matches ? "#brand-slot-top" : "#brand-slot-side",
    once: "compass-splash-seen",
  });
}

function setDrawer(open) {
  document.body.classList.toggle("drawer-open", open);
}
document.getElementById("menu-btn").addEventListener("click", () => setDrawer(true));
document.getElementById("scrim").addEventListener("click", () => setDrawer(false));
document.addEventListener("keydown", (e) => { if (e.key === "Escape") setDrawer(false); });

initSettings();
initChats({ navigate: () => setDrawer(false) });
