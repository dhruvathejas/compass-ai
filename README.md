The experience is built around the existing working setup rather than unnecessary installation or replacement.

The architecture should remain reasonably adaptable so that future AI providers or runtimes can be considered without redesigning the entire product.

The workspace, terminal, file management and interface are separate capabilities that work together.

Security, reliability and maintainability are foundational requirements, not afterthoughts.
12. Imagine What You Could Do

With Compass AI, imagine starting with a simple idea:

"I want to build something."

You explore the possibilities.

You choose a project.

You turn it into a mission.

You create the files.

You work on the code.

You run your project.

Something breaks.

You diagnose the issue.

You explore another route.

You learn something new.

And eventually, you have something real.

Not because an AI took over your work.

Because you had an environment that helped you move forward.

13. The Compass Promise
You bring the destination.
Compass helps you navigate.

We believe technology should increase human capability, not replace human choice.

We believe obstacles should be understood, not ignored.

We believe learning should be part of progress.

We believe powerful tools should remain understandable.

And we believe the person using the technology should always have the final say.

COMPASS AI
Your goal. Your route. Your decision.
Intelligence in your direction.

Compass AI isn't trying to become your boss, your replacement, or the owner of your projects.
---

## Run it

```sh
python -m pip install -r requirements.txt
python app.py        # starts Compass AI on http://127.0.0.1:5000 (this PC only)
```

Open <http://127.0.0.1:5000> on the same computer. The first time, the welcome page offers three paths (install, sign in, connect to a main PC). The first account you create becomes the **owner** of that Compass AI. Then authorize the browser as a device and you're in.

Compass AI talks to a local [Ollama](https://ollama.com) server over HTTP (default `http://127.0.0.1:11434`) and never installs or starts it. Each person picks their model in **Settings**. Environment variables: `OLLAMA_HOST`, `OLLAMA_MODEL`, `COMPASS_PORT`, `COMPASS_HOME` (private data, default `memory/`), `COMPASS_WORKSPACE` (projects, default `workspace/`), `COMPASS_ALLOWED_HOSTS`, `COMPASS_REPO_URL`.

Tests: `python -m unittest discover -s tests -t .` (uses a fake Ollama, so no model is needed).

Want to share it with other people over the internet? See **Run it as a server** below.

Command line (rarely needed): `python app.py set-password [--user NAME] [--show]` creates the owner if there are no accounts, otherwise resets someone's password. `python app.py logout-all` signs everyone out of every browser.

## Accounts, devices and main-PC connections

- **Accounts.** Each person has their own username and password (salted scrypt hash), settings, chats, devices and workspace folder (`memory/users/<id>/`, `workspace/<id>/`). Every request is authorized on the server from the signed-in session; a person can't name, guess or reach another person's data. People can change their password, download their data, or delete their account (typing their username and password) from **Account**.
- **Who can sign up.** The first account is created on the host PC only. After that sign-ups are **invite only** by default: the owner makes single-use codes (7 days) under **Account > People**, or switches registration to open or closed.
- **Devices.** After signing in, a browser must be authorized once. Its secret credential is kept in an HttpOnly cookie and stored only as a hash, so a device ID or a copy of the data files never signs anyone in, and a password is always needed too. Devices can be renamed, reviewed ("This is me") and removed under **Account > Devices**; removing one ends its sessions immediately.
- **Main PC and other devices.** Compass AI is a web app. The *main PC* is the computer running `python app.py`. A phone or second computer simply opens the main PC's address; the welcome page's *"I have it running on my main PC"* path checks the address (from the visitor's own browser, never from the server), then sends them to sign in and authorize the device. To reach the main PC:
  - **same Wi-Fi:** start it with `python app.py --lan`; it prints the address to use (plain http: trusted networks only);
  - **anywhere:** use a tunnel, for example `cloudflared tunnel --url localhost:5000`, and share the `https://` link.
  Using `localhost` on a phone never works: it means the phone itself.
- **Code button.** In the sidebar, **Code** runs `code .` inside your own workspace folder (like `cd workspace` then `code .`). It starts a program on the host, so it works only when you open Compass AI on the host PC itself, never over the network or the tunnel. It needs VS Code's `code` command on your PATH.

## Run it as a server (free for everyone, like ChatGPT's free tier)

Server mode turns Compass AI from "my own PC" into a shared host: anyone who reaches the address can create an account and chat for free, within fair-use limits, and the owner keeps control. Use it on a machine (or home PC) that runs Ollama and is reachable through a domain or tunnel.

```sh
python -m pip install -r requirements-server.txt      # Flask + waitress, a real production web server
python app.py set-password --server                   # once: creates the owner account (never from a web page)
python app.py --server                                # listens on 127.0.0.1:5000, put HTTPS in front of it
```

Put a reverse proxy or tunnel with **HTTPS** in front (Caddy, nginx, or `cloudflared tunnel --url localhost:5000`), then tell Compass AI about it:

| Variable | What it does |
| --- | --- |
| `COMPASS_PUBLIC_URL` | Your public address, e.g. `https://chat.example.com`. Marks cookies Secure and limits the host names answered. |
| `COMPASS_PROXY_HEADER` | The header your proxy sets to the visitor's real address (`CF-Connecting-Ip` for Cloudflare, `X-Forwarded-For` for nginx/Caddy). Only set it when the proxy is the *only* way in, otherwise visitors could forge it. Without it every visitor looks like the proxy and shares one rate limit. |
| `COMPASS_MODELS` | Comma-separated models people may choose (default: everything installed in Ollama). |
| `COMPASS_FREE_DAILY`, `COMPASS_FREE_PER_MINUTE`, `COMPASS_FREE_CHATS`, `COMPASS_MAX_USERS` | Starting free-tier limits (50 messages/day, 6/minute, 100 chats, 1000 accounts; `0` = no limit). After first run the owner changes them in the app. |
| `COMPASS_MAX_CONCURRENT` | Replies generated at the same time (default 2): match it to what your GPU/CPU can serve. |
| `COMPASS_HOST`, `COMPASS_PORT`, `COMPASS_THREADS` | Where to listen (default `127.0.0.1:5000`, 16 threads). |

What changes in server mode:

- **Open sign-up** by default (the owner can switch to invite-only or closed under **Account > People**). The owner account is created on the server's command line only, so nobody can grab it first.
- **Free tier.** Each member gets a daily and a per-minute message allowance, a cap on saved chats, and a meter in the sidebar ("37 of 50 free messages left today", resets at midnight UTC). The owner is never limited, and a message that got no answer (model down, server busy) is not counted. The owner edits the limits live, sees today's usage per person, and can **suspend** someone (signed out at once, data kept) or remove them under **Account > People**.
- **Capacity.** Only a few replies are generated at once and a person can have one in progress; when the server is full people are told it's busy instead of hanging. Long chats send only their latest part to the model.
- **Nothing runs on the server for visitors.** There is no "this PC": the Code button is gone, nobody gets host-only powers, and the welcome/install/connect pages are replaced by plain sign-in and sign-up.
- **Frictionless devices.** Signing in registers the browser as a device automatically (it still shows up under **Devices** to rename or remove).
- **Extra abuse protection.** Guessing one account's password from many addresses locks that account for a while, sign-ups are limited per address and server-wide, and the account count is capped.

Honest limits: this is a small self-hosted service, not a cloud platform. Everything lives in JSON files on one machine, so it suits tens to a few hundred active people, not thousands. There is no email verification or password-reset email (the owner resets passwords with `python app.py set-password --server --user NAME`), and whoever runs the server can read its files, which the **Privacy** page tells members plainly.

## Security

Compass AI is built to sit behind a Cloudflare tunnel without being open to the world.

- **Sign-in required** for everything except the welcome, install, connect and sign-in pages and a read-only `/api/hello` host check (it reveals only that this is Compass AI). Passwords are never stored; sessions last 12 hours idle / 7 days at most, live on the server (so they can be ended individually) and use HttpOnly, SameSite=Strict cookies (Secure over https) plus CSRF tokens.
- **Nobody can claim an unconfigured host remotely:** the first (owner) account can only be created from the host PC itself.
- **Brute force:** repeated wrong passwords lock that address out with growing delays; sign-up and invite-guessing are rate limited. Anyone at the host PC is never locked out by outside traffic.
- **Where a request comes from matters:** the PC itself, the local network (`--lan`), or the tunnel (detected from proxy headers, since the tunnel always arrives from 127.0.0.1). Anything that starts programs on the host is limited to the PC itself.
- **Hardening:** strict Content-Security-Policy (no inline scripts; only the Connect page may contact other addresses), the server listens on 127.0.0.1 unless you pass `--lan`, unknown Host names are refused, responses are `no-store`.
- **Your data** (`memory/`, `workspace/`) is git-ignored. Anyone with the tunnel link still needs an account; keep invite codes and passwords private.
- Honest limits: the owner of the host PC controls its files. Email addresses are stored as contact info only (not verified, never used to send mail), so password resets are done by the owner with `python app.py set-password --user NAME`.
