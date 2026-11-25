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
python app.py set-password   # once: choose your sign-in password (10+ characters)
python app.py                # then start Compass AI on http://127.0.0.1:5000
```

Compass AI talks to a local [Ollama](https://ollama.com) server over HTTP (default `http://127.0.0.1:11434`) and never installs or starts it. Pick the model in **Settings**. Environment variables: `OLLAMA_HOST`, `OLLAMA_MODEL`, `COMPASS_PORT`, `COMPASS_HOME` (where chats and settings live, default `memory/`).

Tests: `python -m unittest discover -s tests -t .` (uses a fake Ollama, so no model is needed).

## Security

Compass AI is built to sit behind a Cloudflare tunnel (`cloudflared tunnel --url localhost:5000`) without being open to the world.

- **Password required** for everything. It is set from the computer running the app (`python app.py set-password`), never through the web, so nobody can claim an unconfigured app. Until it is set the app shows a "Setup needed" page. It is stored as a salted scrypt hash.
- **Sessions** last 12 hours idle / 7 days at most, use HttpOnly + SameSite=Strict cookies (Secure over https) and CSRF tokens. **Settings > Sign out everywhere** (or `python app.py logout-all`) revokes every browser at once; changing the password does too.
- **Brute force**: repeated wrong passwords lock that address out with growing delays. Anyone at this PC is never locked out by internet traffic.
- **Hardening**: strict Content-Security-Policy (no inline scripts), the server listens on 127.0.0.1 only, unknown Host names are refused, responses are `no-store`.
- **Your data** (`memory/`, `workspace/`) is git-ignored. Anyone with the tunnel link still needs your password; keep both private.
