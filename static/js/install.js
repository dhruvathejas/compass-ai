// Install page: pre-select the visitor's operating system and make the command boxes copyable.
import { copyText } from "./ui.js";

const ua = navigator.userAgent || "";
const platform = (navigator.userAgentData && navigator.userAgentData.platform) || navigator.platform || "";
const mobile = /Android|iPhone|iPad|iPod/i.test(ua);

let os = null;
if (/Win/i.test(platform) || /Windows/i.test(ua)) os = "windows";
else if (/Mac/i.test(platform) && !mobile) os = "mac";
else if (/Linux|X11/i.test(platform) && !mobile) os = "linux";

if (os) document.getElementById(`os-${os}`).checked = true;

if (mobile) {
  const note = document.getElementById("os-note");
  note.textContent = "This looks like a phone or tablet. Compass AI is installed on a computer. To use it from this device, connect to the computer that runs it instead.";
  note.hidden = false;
}

document.querySelectorAll("[data-copy]").forEach((btn) => {
  btn.addEventListener("click", () => copyText(btn.dataset.copy, btn));
});
