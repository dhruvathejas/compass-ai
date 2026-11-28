// Tiny DOM helpers shared by the account, connect and install pages. Text always goes in as text, never as HTML.

export function h(tag, props = {}, ...children) {
  const el = document.createElement(tag);
  for (const [key, value] of Object.entries(props || {})) {
    if (value === false || value == null) continue;
    if (key === "class") el.className = value;
    else if (key === "text") el.textContent = value;
    else if (key.startsWith("on")) el.addEventListener(key.slice(2), value);
    else if (value === true) el.setAttribute(key, "");
    else el.setAttribute(key, value);
  }
  el.append(...children.flat().filter((c) => c != null && c !== false));
  return el;
}

const SVG_NS = "http://www.w3.org/2000/svg";
const ICONS = {
  phone: "M8 3h8a1 1 0 011 1v16a1 1 0 01-1 1H8a1 1 0 01-1-1V4a1 1 0 011-1zM11 18h2",
  tablet: "M6 3h12a1 1 0 011 1v16a1 1 0 01-1 1H6a1 1 0 01-1-1V4a1 1 0 011-1zM11 18h2",
  laptop: "M5 6h14v9H5zM3 19h18",
  desktop: "M4 4h16v11H4zM9 20h6M12 15v5",
  other: "M12 3l9 5v8l-9 5-9-5V8z",
};

export function icon(name, size = 22) {
  const svg = document.createElementNS(SVG_NS, "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("width", size);
  svg.setAttribute("height", size);
  svg.setAttribute("aria-hidden", "true");
  const path = document.createElementNS(SVG_NS, "path");
  path.setAttribute("d", ICONS[name] || ICONS.other);
  for (const [k, v] of Object.entries({ fill: "none", stroke: "currentColor", "stroke-width": 1.8,
    "stroke-linecap": "round", "stroke-linejoin": "round" })) path.setAttribute(k, v);
  svg.append(path);
  return svg;
}

export function say(el, message, bad = false) {
  el.textContent = message;
  el.classList.toggle("bad", bad);
}

export function ago(ts) {
  const s = Math.max(0, Date.now() / 1000 - ts);
  if (s < 45) return "just now";
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  if (s < 86400) return `${Math.round(s / 3600)} h ago`;
  if (s < 7 * 86400) return `${Math.round(s / 86400)} days ago`;
  return new Date(ts * 1000).toLocaleDateString();
}

export const when = (ts) => new Date(ts * 1000).toLocaleString();

/** Replace a row's action buttons with "message  [Cancel] [Yes]". If `onYes` throws, the buttons come back (it should show its own error first). */
export function inlineConfirm(actions, { message, yes, onYes }) {
  const before = [...actions.childNodes];
  const restore = () => { actions.replaceChildren(...before); before.find((n) => n.focus)?.focus(); };
  const ok = h("button", { class: "btn danger solid", type: "button", text: yes });
  const no = h("button", { class: "btn", type: "button", text: "Cancel", onclick: restore });
  ok.addEventListener("click", async () => {
    ok.disabled = no.disabled = true;
    try { await onYes(); } catch { restore(); }      // onYes reports its own error; the buttons come back
  });
  actions.replaceChildren(h("span", { class: "confirm-msg", text: message }), no, ok);
  no.focus();
}

export async function copyText(text, button) {
  const original = button.textContent;
  try {
    await navigator.clipboard.writeText(text);
    button.textContent = "Copied";
  } catch {
    button.textContent = "Select and copy";           // clipboard blocked (plain http): the text is selectable
  }
  setTimeout(() => { button.textContent = original; }, 1600);
}
