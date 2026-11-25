// Chat list (sidebar) and the conversation view. State mirrors memory/chats.json on the server.
import { api, ApiError, BRAND } from "./api.js";

const chatBox = document.getElementById("chat-box");
const chatList = document.getElementById("chat-list");
const form = document.getElementById("chat-form");
const input = document.getElementById("user-input");
const sendBtn = form.querySelector(".send");

let chats = [];            // [{id, title, updated}] newest first
let currentId = null;      // null = a fresh chat; it is only created on the server when you send the first message
let confirmingId = null;   // sidebar row that is asking "Delete this chat?"
let busy = false;          // a reply is on its way
let onNavigate = () => {};

/* ---------------------------------------------------------------- sidebar */

function renderList() {
  chatList.replaceChildren();
  if (!chats.length) {
    const li = document.createElement("li");
    li.className = "side-empty";
    li.textContent = "No chats yet.";
    chatList.appendChild(li);
    return;
  }
  for (const c of chats) chatList.appendChild(c.id === confirmingId ? confirmRow(c) : chatRow(c));
}

function chatRow(c) {
  const li = document.createElement("li");
  li.className = "chat-item" + (c.id === currentId ? " active" : "");
  li.tabIndex = 0;
  li.dataset.id = c.id;
  if (c.id === currentId) li.setAttribute("aria-current", "true");

  const title = document.createElement("span");
  title.className = "t";
  title.textContent = c.title;
  title.title = c.title;

  const del = document.createElement("button");
  del.className = "del";
  del.type = "button";
  del.textContent = "×";
  del.setAttribute("aria-label", `Delete chat: ${c.title}`);
  del.addEventListener("click", (e) => {
    e.stopPropagation();
    confirmingId = c.id;
    renderList();
    chatList.querySelector(".confirming .btn:not(.danger)")?.focus();
  });

  li.append(title, del);
  li.addEventListener("click", () => openChat(c.id));
  li.addEventListener("keydown", (e) => {
    if (e.target === li && (e.key === "Enter" || e.key === " ")) {
      e.preventDefault();
      openChat(c.id);
    }
  });
  return li;
}

function confirmRow(c) {
  const li = document.createElement("li");
  li.className = "chat-item confirming";
  li.dataset.id = c.id;
  const q = document.createElement("span");
  q.className = "q";
  q.textContent = `Delete “${c.title}”? This can't be undone.`;
  const no = document.createElement("button");
  no.className = "btn";
  no.type = "button";
  no.textContent = "Cancel";
  const yes = document.createElement("button");
  yes.className = "btn danger solid";
  yes.type = "button";
  yes.textContent = "Delete";
  const cancel = () => { confirmingId = null; renderList(); chatList.querySelector(`[data-id="${c.id}"]`)?.focus(); };
  no.addEventListener("click", cancel);
  li.addEventListener("keydown", (e) => { if (e.key === "Escape") { e.stopPropagation(); cancel(); } });
  yes.addEventListener("click", () => removeChat(c));
  li.append(q, no, yes);
  return li;
}

async function refreshList() {
  chats = await api("/api/chats");
  renderList();
}

/* ---------------------------------------------------------------- conversation */

function showEmpty() {
  chatBox.replaceChildren();
  const wrap = document.createElement("div");
  wrap.className = "empty";
  const img = document.createElement("img");
  img.src = `${BRAND}logo/mark.svg`;
  img.alt = "";
  const p = document.createElement("p");
  p.textContent = "Ready when you are";
  const small = document.createElement("small");
  small.textContent = "Ask about code, maths or anything logical.";
  wrap.append(img, p, small);
  chatBox.appendChild(wrap);
}

function addBubble(text, sender) {
  chatBox.querySelector(".empty")?.remove();
  const row = document.createElement("div");
  row.className = `row ${sender}`;
  if (sender === "ai") {
    const av = document.createElement("img");
    av.className = "avatar";
    av.src = `${BRAND}logo/mark-small.svg`;
    av.alt = "";
    row.appendChild(av);
  }
  const bubble = document.createElement("div");
  bubble.className = "msg";
  bubble.textContent = text;
  row.appendChild(bubble);
  chatBox.appendChild(row);
  chatBox.scrollTop = chatBox.scrollHeight;
  return bubble;
}

function renderChat(chat) {
  chatBox.replaceChildren();
  if (!chat || !chat.messages.length) return showEmpty();
  for (const m of chat.messages) addBubble(m.content, m.role === "user" ? "user" : "ai");
}

/* ---------------------------------------------------------------- actions */

export async function openChat(id) {
  onNavigate();
  confirmingId = null;
  try {
    const chat = await api(`/api/chats/${id}`);
    currentId = id;
    renderChat(chat);
    renderList();
    input.focus();
  } catch (err) {
    if (err.status === 404) await refreshList();   // it vanished elsewhere: fall back to the list
    else showError(err);
  }
}

export function newChat() {
  onNavigate();
  currentId = null;
  confirmingId = null;
  showEmpty();
  renderList();
  input.focus();
}

async function removeChat(c) {
  confirmingId = null;
  try {
    await api(`/api/chats/${c.id}`, { method: "DELETE" });
  } catch (err) {
    if (err.status !== 404) return showError(err);
  }
  await refreshList();
  if (c.id === currentId) chats.length ? openChat(chats[0].id) : newChat();
}

function showError(err) {
  const bubble = addBubble(err.message || "Something went wrong.", "ai");
  bubble.classList.add("error");
}

function setBusy(value) {
  busy = value;
  sendBtn.disabled = value;
  sendBtn.setAttribute("aria-busy", String(value));
}

form.addEventListener("submit", async (e) => {
  e.preventDefault();
  const text = input.value.trim();
  if (!text || busy) return;
  setBusy(true);
  input.value = "";

  try {
    if (currentId === null) {
      const created = await api("/api/chats", { method: "POST" });
      currentId = created.id;
      const title = text.length > 40 ? `${text.slice(0, 40)}…` : text;
      chats.unshift({ id: created.id, title, updated: created.updated });
      renderList();
    }
  } catch (err) {
    input.value = text;                                // nothing was lost; let the user retry
    showError(err);
    return setBusy(false);
  }

  const id = currentId;
  addBubble(text, "user");
  const thinking = addBubble("Thinking…", "ai");
  thinking.classList.add("thinking");

  try {
    const data = await api(`/api/chats/${id}/message`, { method: "POST", body: { content: text } });
    if (currentId === id) { thinking.classList.remove("thinking"); thinking.textContent = data.reply; }
  } catch (err) {
    if (currentId === id) {
      thinking.classList.remove("thinking");
      thinking.classList.add("error");
      thinking.textContent = err instanceof ApiError ? err.message : "Something went wrong.";
    }
  } finally {
    setBusy(false);
  }
  await refreshList().catch(() => {});
});

export async function initChats({ navigate }) {
  onNavigate = navigate;
  showEmpty();
  try {
    await refreshList();
    if (chats.length) await openChat(chats[0].id);
  } catch {
    renderList();
  }
  document.getElementById("new-chat").addEventListener("click", newChat);
}
