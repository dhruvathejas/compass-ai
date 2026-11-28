// Account area: one script, one section per page (data-section on #acct).
import { api, ApiError } from "./api.js";
import { h, icon, say, ago, when, inlineConfirm, copyText } from "./ui.js";

const root = document.getElementById("acct");
const $ = (id) => document.getElementById(id);
const fail = (el) => (err) => say(el, err instanceof ApiError ? err.message : "Something went wrong.", true);

/* ------------------------------------------------------------------ profile */

async function profile() {
  const form = $("profile-form");
  const status = $("profile-status");
  document.querySelectorAll("[data-time]").forEach((el) => {
    el.textContent = new Date(Number(el.dataset.time) * 1000).toLocaleDateString(undefined, { dateStyle: "long" });
  });
  api("/api/account").then(({ connection, device }) => {
    $("info-host").textContent = `${connection.label} (${connection.host})`;
    $("info-device").textContent = device ? `${device.name} · ${device.type}` : "Not authorized yet";
  }).catch(() => {});

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    say(status, "Saving…");
    try {
      const user = await api("/api/account/profile", {
        method: "PATCH", body: { display_name: form.display_name.value, email: form.email.value },
      });
      form.display_name.value = user.display_name;
      form.email.value = user.email;
      say(status, "Saved.");
    } catch (err) { fail(status)(err); }
  });
}

/* ------------------------------------------------------------------ security */

const ORIGIN = { local: "This PC", lan: "Local network", tunnel: "Internet (tunnel)", internet: "Internet" };

async function security() {
  const form = $("password-form");
  const status = $("password-status");
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    if (form.new.value !== form.again.value) return say(status, "The two new passwords don't match.", true);
    say(status, "Changing…");
    try {
      const { message } = await api("/api/account/password", {
        method: "POST", body: { current: form.current.value, new: form.new.value },
      });
      form.reset();
      say(status, message);
      loadSessions();
    } catch (err) { fail(status)(err); }
  });

  const list = $("sessions-list");
  const sessionsStatus = $("sessions-status");
  async function loadSessions() {
    try {
      const rows = await api("/api/sessions");
      list.replaceChildren(...rows.map((s) => {
        const actions = h("div", { class: "row-actions" });
        const end = h("button", { class: "btn", type: "button", text: s.current ? "Sign out" : "End session" });
        end.addEventListener("click", async () => {
          try {
            const r = await api(`/api/sessions/${s.id}`, { method: "DELETE" });
            if (r.signed_out) location.assign("/login"); else loadSessions();
          } catch (err) { fail(sessionsStatus)(err); }
        });
        actions.append(end);
        return h("li", { class: "row" },
          h("div", { class: "row-main" },
            h("strong", { text: s.agent }),
            s.current ? h("span", { class: "pill good", text: "This browser" }) : null,
            h("div", { class: "meta", text: `${s.device} · ${ORIGIN[s.origin] || s.origin} · active ${ago(s.last)} · signed in ${when(s.created)}` })),
          actions);
      }));
    } catch (err) { list.replaceChildren(h("li", { class: "loading bad", text: err.message })); }
  }
  loadSessions();

  const revoke = $("revoke"), yes = $("revoke-yes"), no = $("revoke-no");
  const reset = () => { revoke.hidden = false; yes.hidden = no.hidden = true; };
  revoke.addEventListener("click", () => { revoke.hidden = true; yes.hidden = no.hidden = false; no.focus(); });
  no.addEventListener("click", () => { reset(); revoke.focus(); });
  yes.addEventListener("click", async () => {
    try { await api("/api/sessions/revoke", { method: "POST" }); location.assign("/login"); }
    catch (err) { fail(sessionsStatus)(err); reset(); }
  });
}

/* ------------------------------------------------------------------ devices */

function devices() {
  const list = $("devices-list");
  const status = $("devices-status");
  const banner = $("review-banner");
  let editing = false;

  const stateOf = (d) => (d.connected ? ["Connected now", "good"] : d.signed_in ? ["Signed in", ""] : ["Signed out", "dim"]);

  function row(d) {
    const actions = h("div", { class: "row-actions" });
    const [stateText, stateClass] = stateOf(d);
    const li = h("li", { class: "row device", "data-id": d.id },
      h("span", { class: "dev-icon" }, icon(d.type)),
      h("div", { class: "row-main" },
        h("div", { class: "title" },
          h("strong", { text: d.name }),
          d.current ? h("span", { class: "pill good", text: "This device" }) : null,
          !d.reviewed && !d.current ? h("span", { class: "pill warn", text: "New: please review" }) : null,
          h("span", { class: `pill ${stateClass}`, text: stateText })),
        h("div", { class: "meta", text: `${d.type[0].toUpperCase() + d.type.slice(1)} · ${d.agent || "Browser"} · registered ${when(d.created)} · last active ${ago(d.last_active)}` })),
      actions);

    const rename = h("button", { class: "btn", type: "button", text: "Rename", onclick: () => edit(d, li) });
    const remove = h("button", { class: "btn danger", type: "button", text: "Remove" });
    remove.addEventListener("click", () => inlineConfirm(actions, {
      message: d.current ? "Remove this device? You'll be signed out." : "Remove this device? It signs out now and must be authorized again.",
      yes: "Remove",
      onYes: async () => {
        try {
          const r = await api(`/api/devices/${d.id}`, { method: "DELETE" });
          if (r.signed_out) return location.assign("/login");
          say(status, `Removed ${d.name}.`);
          await load();
        } catch (err) { fail(status)(err); throw err; }
      },
    }));
    if (!d.reviewed && !d.current) {
      actions.append(h("button", {
        class: "btn primary", type: "button", text: "This is me",
        onclick: async () => {
          try { await api(`/api/devices/${d.id}`, { method: "PATCH", body: { reviewed: true } }); await load(); }
          catch (err) { fail(status)(err); }
        },
      }));
    }
    actions.append(rename, remove);
    return li;
  }

  function edit(d, li) {
    editing = true;
    const name = h("input", { type: "text", value: d.name, maxlength: "40", "aria-label": "Device name" });
    const type = h("select", { "aria-label": "Device type" },
      ...["desktop", "laptop", "tablet", "phone", "other"].map((t) => h("option", { value: t, text: t[0].toUpperCase() + t.slice(1), selected: t === d.type })));
    const save = h("button", { class: "btn primary", type: "button", text: "Save" });
    const cancel = h("button", { class: "btn", type: "button", text: "Cancel" });
    const done = () => { editing = false; load(); };
    cancel.addEventListener("click", done);
    save.addEventListener("click", async () => {
      try {
        await api(`/api/devices/${d.id}`, { method: "PATCH", body: { name: name.value, type: type.value } });
        say(status, "Saved.");
        done();
      } catch (err) { fail(status)(err); }
    });
    name.addEventListener("keydown", (e) => { if (e.key === "Enter") save.click(); if (e.key === "Escape") cancel.click(); });
    li.replaceChildren(h("span", { class: "dev-icon" }, icon(d.type)),
      h("div", { class: "row-main edit" }, name, type), h("div", { class: "row-actions" }, cancel, save));
    name.focus();
    name.select();
  }

  async function load() {
    if (editing) return;
    try {
      const rows = await api("/api/devices");
      list.replaceChildren(...rows.map(row));
      const fresh = rows.filter((d) => !d.reviewed && !d.current).length;
      banner.hidden = !fresh;
      if (fresh) banner.textContent = `${fresh} device${fresh === 1 ? " was" : "s were"} added since you last checked. If you don't recognize ${fresh === 1 ? "it" : "one"}, remove it and change your password.`;
    } catch (err) { list.replaceChildren(h("li", { class: "loading bad", text: err.message })); }
  }
  load();
  setInterval(load, 30000);
}

/* ------------------------------------------------------------------ privacy */

function privacy() {
  const form = $("delete-form");
  const status = $("delete-status");
  form.addEventListener("submit", (e) => {
    e.preventDefault();
    const actions = form.querySelector(".actions");
    if (!form.confirm_username.value || !form.delete_password.value) return say(status, "Fill in both fields first.", true);
    inlineConfirm(actions, {
      message: "Erase this account and everything in it, for good?",
      yes: "Yes, delete my account",
      onYes: async () => {
        try {
          await api("/api/account/delete", {
            method: "POST", body: { username: form.confirm_username.value, password: form.delete_password.value },
          });
          location.assign("/welcome");
        } catch (err) { fail(status)(err); throw err; }
      },
    });
  });
}

/* ------------------------------------------------------------------ people (owner) */

function people() {
  const status = $("people-status");
  const regStatus = $("reg-status");
  const invites = $("invites-list");
  const members = $("members-list");
  const box = $("new-invite");
  const limitsForm = $("limits-form");
  const limitsStatus = $("limits-status");

  async function loadLimits() {
    if (!limitsForm) return;
    try {
      const s = await api("/api/server");
      for (const [key, value] of Object.entries(s.limits)) limitsForm[key].value = value;
      $("limits-summary").textContent =
        `Today: ${s.today.messages} message${s.today.messages === 1 ? "" : "s"} from ${s.today.people} ${s.today.people === 1 ? "person" : "people"}. ${s.accounts} account${s.accounts === 1 ? "" : "s"}. ` +
        `Models offered: ${s.models.length ? s.models.join(", ") : "everything installed in Ollama"}. Up to ${s.max_concurrent} replies at once.`;
    } catch (err) { fail(limitsStatus)(err); }
  }
  limitsForm?.addEventListener("submit", async (e) => {
    e.preventDefault();
    const body = {};
    for (const key of ["daily_messages", "per_minute", "max_chats", "max_users"]) {
      const raw = limitsForm[key].value.trim();
      if (!/^\d+$/.test(raw)) return say(limitsStatus, "Use whole numbers only.", true);
      body[key] = Number(raw);
    }
    say(limitsStatus, "Saving…");
    try {
      await api("/api/server/limits", { method: "PUT", body });
      say(limitsStatus, "Saved. It applies to the next message.");
      loadLimits();
    } catch (err) { fail(limitsStatus)(err); }
  });

  async function load() {
    try {
      const data = await api("/api/people");
      document.querySelector(`#reg-group input[value="${data.registration}"]`).checked = true;
      invites.replaceChildren(...data.invites.map((i) => {
        const actions = h("div", { class: "row-actions" });
        actions.append(h("button", {
          class: "btn", type: "button", text: "Revoke",
          onclick: async () => { try { await api(`/api/people/invites/${i.id}`, { method: "DELETE" }); load(); } catch (e) { fail(status)(e); } },
        }));
        return h("li", { class: "row" }, h("div", { class: "row-main" },
          h("strong", { text: "Unused invite" }),
          h("div", { class: "meta", text: `Created ${ago(i.created)} · expires ${when(i.expires)}` })), actions);
      }));
      members.replaceChildren(...data.members.map((m) => {
        const actions = h("div", { class: "row-actions" });
        if (m.role !== "owner") {
          if (data.server) {
            actions.append(h("button", {
              class: "btn", type: "button", text: m.suspended ? "Reactivate" : "Suspend",
              onclick: async () => {
                try {
                  await api(`/api/people/members/${m.id}/suspended`, { method: "PUT", body: { suspended: !m.suspended } });
                  say(status, m.suspended ? `${m.username} can sign in again.` : `${m.username} was signed out and can't sign in.`);
                  await load();
                } catch (err) { fail(status)(err); }
              },
            }));
          }
          actions.append(h("button", {
            class: "btn danger", type: "button", text: "Remove",
            onclick: (e) => inlineConfirm(actions, {
              message: `Remove ${m.display_name} and erase all their data?`, yes: "Remove",
              onYes: async () => {
                try { await api(`/api/people/members/${m.id}`, { method: "DELETE" }); say(status, `Removed ${m.username}.`); await load(); }
                catch (err) { fail(status)(err); throw err; }
              },
            }),
          }));
        }
        return h("li", { class: "row" }, h("div", { class: "row-main" },
          h("div", { class: "title" }, h("strong", { text: m.display_name }), h("span", { class: "pill", text: `@${m.username}` }),
            m.role === "owner" ? h("span", { class: "pill good", text: "Owner" }) : null,
            m.suspended ? h("span", { class: "pill warn", text: "Suspended" }) : null),
          h("div", { class: "meta", text: `${m.devices} device${m.devices === 1 ? "" : "s"} · ${m.last_active ? `last active ${ago(m.last_active)}` : "never signed in"}${data.server ? ` · ${m.today} message${m.today === 1 ? "" : "s"} today` : ""} · joined ${when(m.created)}` })), actions);
      }));
    } catch (err) { fail(status)(err); }
  }

  $("reg-group").addEventListener("change", async (e) => {
    say(regStatus, "Saving…");
    try {
      await api("/api/people/registration", { method: "PUT", body: { mode: e.target.value } });
      say(regStatus, "Saved.");
    } catch (err) { fail(regStatus)(err); load(); }
  });

  $("make-invite").addEventListener("click", async () => {
    try {
      const inv = await api("/api/people/invites", { method: "POST" });
      const link = `${location.origin}/register?invite=${encodeURIComponent(inv.code)}`;
      const copyCode = h("button", { class: "btn", type: "button", text: "Copy code" });
      const copyLink = h("button", { class: "btn", type: "button", text: "Copy sign-up link" });
      copyCode.addEventListener("click", () => copyText(inv.code, copyCode));
      copyLink.addEventListener("click", () => copyText(link, copyLink));
      box.replaceChildren(h("p", { class: "hint", text: "Share this now. It won't be shown again." }),
        h("code", { class: "invite-code", text: inv.code }), h("div", { class: "actions" }, copyCode, copyLink));
      box.hidden = false;
      load();
    } catch (err) { fail(status)(err); }
  });
  load();
  loadLimits();
}

({ profile, security, devices, privacy, people })[root.dataset.section]?.();
