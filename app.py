"""Compass AI entry point.

    python app.py                 start the server on http://127.0.0.1:5000
    python app.py set-password    set or change the sign-in password (signs everyone out)
    python app.py logout-all      sign out every browser and phone without changing the password

Environment (all optional): OLLAMA_HOST, OLLAMA_MODEL, COMPASS_HOME, COMPASS_WORKSPACE,
COMPASS_PORT, COMPASS_ALLOWED_HOSTS.
"""
from __future__ import annotations

import argparse
import getpass
import os
import sys

from compass import create_app
from compass.auth import MIN_PASSWORD
from compass.config import Config


def prompt_password(auth, show: bool = False) -> bool:
    print(f"Choose a password for Compass AI (at least {MIN_PASSWORD} characters).")
    if show:
        ask = input
        print("Your typing is VISIBLE. Close this window afterwards so it doesn't stay on screen.")
    else:
        ask = getpass.getpass
        print("Your typing is hidden (no dots or stars appear). Type it anyway and press Enter.")
        print("Prefer to see it? Run: python app.py set-password --show")
    for _ in range(3):
        first = ask("New password: ")
        if first != ask("Repeat it:   "):
            print("Those did not match. Try again.\n")
            continue
        try:
            auth.set_password(first)
        except ValueError as exc:
            print(f"{exc}\n")
            continue
        print("Password saved. Every browser has been signed out.")
        return True
    print("No password was set.")
    return False


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python app.py", description="Compass AI")
    parser.add_argument("command", nargs="?", choices=["serve", "set-password", "logout-all"], default="serve")
    parser.add_argument("--show", action="store_true", help="show the password as you type it (set-password)")
    args = parser.parse_args(argv)

    cfg = Config.from_env()
    app = create_app(cfg)
    auth = app.extensions["compass"].auth

    if args.command == "set-password":
        return 0 if prompt_password(auth, show=args.show) else 1
    if args.command == "logout-all":
        auth.bump_epoch()
        print("Signed out everywhere.")
        return 0

    if not auth.has_password():
        if sys.stdin.isatty():
            if not prompt_password(auth):
                return 1
        else:
            print("No password is set yet. Run: python app.py set-password", file=sys.stderr)

    port = int(os.environ.get("COMPASS_PORT", "5000"))
    print(f"Compass AI  http://127.0.0.1:{port}")
    print(f"  brain:   Ollama model '{app.extensions['compass'].settings.read(lambda d: d['model'])}' at {cfg.ollama_host}")
    print(f"  memory:  {cfg.home}")
    # 127.0.0.1 only: the Cloudflare tunnel reaches the app locally, nothing else on the network can.
    app.run(host="127.0.0.1", port=port, debug=False, threaded=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
