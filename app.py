"""Compass AI entry point.

    python app.py                    start the server on http://127.0.0.1:5000 (this PC only)
    python app.py --lan              also let devices on your local network connect (plain http, trusted Wi-Fi only)
    python app.py --server           run as a shared server: anyone can sign up and use it for free, within limits
    python app.py set-password       first run: create the owner account here; later: reset someone's password
    python app.py logout-all         sign every account out of every browser

Environment (all optional): OLLAMA_HOST, OLLAMA_MODEL, COMPASS_HOME, COMPASS_WORKSPACE, COMPASS_PORT,
COMPASS_ALLOWED_HOSTS, COMPASS_LAN=1, COMPASS_REPO_URL.
Server mode (--server or COMPASS_MODE=server) also reads: COMPASS_HOST, COMPASS_PUBLIC_URL, COMPASS_PROXY_HEADER,
COMPASS_MODELS, COMPASS_FREE_DAILY, COMPASS_FREE_PER_MINUTE, COMPASS_FREE_CHATS, COMPASS_MAX_USERS,
COMPASS_MAX_CONCURRENT, COMPASS_THREADS. See the README, "Run it as a server".

Normally you don't need the command line at all: open http://127.0.0.1:5000 on this PC and create the owner
account in the browser.
"""
from __future__ import annotations

import argparse
import getpass
import os
import socket
import sys
from dataclasses import replace

from compass import create_app
from compass.accounts import MIN_PASSWORD, AccountError
from compass.config import Config


def lan_addresses() -> list[str]:
    """This PC's non-loopback IPv4 addresses, for telling other devices where to connect."""
    found = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            found.add(info[4][0])
    except OSError:
        pass
    try:                                     # the address used to reach the outside world (sends nothing)
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("192.0.2.1", 9))
            found.add(s.getsockname()[0])
    except OSError:
        pass
    return sorted(a for a in found if not a.startswith("127.") and not a.startswith("169.254."))


def ask_password(show: bool) -> str | None:
    if show:
        print("Your typing is VISIBLE. Close this window afterwards so it doesn't stay on screen.")
        ask = input
    else:
        print("Your typing is hidden (no dots or stars appear). Type it anyway and press Enter.")
        print("Prefer to see it? Add --show.")
        ask = getpass.getpass
    for _ in range(3):
        first = ask(f"New password (at least {MIN_PASSWORD} characters): ")
        if first == ask("Repeat it: "):
            return first
        print("Those did not match. Try again.\n")
    return None


def set_password(app, username: str | None, show: bool) -> int:
    svc = app.extensions["compass"]
    accounts = svc.accounts
    try:
        if not accounts.has_users():
            print("No accounts yet. Creating the owner account.")
            username = username or input("Username: ").strip()
            display = input("Your name (Enter to reuse the username): ").strip()
            password = ask_password(show)
            if password is None:
                print("No account was created.")
                return 1
            user = accounts.register(username, display, "", password, is_local=True)
            svc.userdata.adopt_legacy(user["id"])
            print(f"Owner account '{user['username']}' created. Open the app and sign in.")
            return 0
        username = username or next(u["username"] for u in accounts.list_users() if u["role"] == "owner")
        password = ask_password(show)
        if password is None:
            print("Password not changed.")
            return 1
        accounts.reset_password(username, password)
        print(f"Password changed for '{username}'. They have been signed out everywhere.")
        return 0
    except AccountError as exc:
        print(f"{exc}", file=sys.stderr)
        return 1


def serve_shared(app, cfg, svc, host: str, port: int) -> int:
    """Server mode: a production WSGI server (waitress) when installed, and a briefing for the person running it."""
    limits = svc.policy.settings.get()

    def show(n: int) -> str:
        return str(n) if n else "no limit"

    print("Compass AI  SERVER MODE")
    print(f"  listening: http://{host}:{port}" + (f"   public address: {cfg.public_url}" if cfg.public_url else ""))
    print(f"  accounts:  {svc.accounts.count_users()}   sign-up: {svc.accounts.registration_mode()}")
    print(f"  free tier: {show(limits['daily_messages'])} messages/day, {show(limits['per_minute'])}/minute, "
          f"{show(limits['max_chats'])} chats, up to {show(limits['max_users'])} accounts, "
          f"{cfg.max_concurrent} replies at once")
    print(f"  brain:     Ollama at {cfg.ollama_host}; models: {', '.join(cfg.models) or 'everything installed'}")
    print(f"  memory:    {cfg.home}")
    if not svc.accounts.has_users():
        print()
        print("  No owner yet: stop here and run  python app.py set-password --server  to create the owner account.")
        print("  Until then every visitor sees a 'setup needed' page.")
    if host not in ("127.0.0.1", "localhost", "::1"):
        print("  NOTE: listening on a public address without HTTPS. Put a reverse proxy or tunnel with HTTPS in front.")
    elif not cfg.public_url.startswith("https://"):
        print("  Tip: set COMPASS_PUBLIC_URL=https://your.domain once HTTPS is in front, so cookies are marked Secure.")
    if not cfg.proxy_header:
        print("  Tip: behind a proxy set COMPASS_PROXY_HEADER (X-Forwarded-For, CF-Connecting-Ip...) so limits see "
              "each visitor's address instead of the proxy's.")
    try:
        import waitress
    except ImportError:
        print()
        print("  WARNING: 'waitress' isn't installed, so Flask's development server is being used. That is fine for")
        print("  a trial but not for the public internet:  python -m pip install -r requirements-server.txt")
        app.run(host=host, port=port, debug=False, threaded=True)
        return 0
    print("  serving with waitress")
    waitress.serve(app, host=host, port=port, threads=int(os.environ.get("COMPASS_THREADS", "16")),
                   connection_limit=500, channel_timeout=120, max_request_body_size=cfg.max_request_bytes,
                   ident="Compass AI", expose_tracebacks=False)
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python app.py", description="Compass AI")
    parser.add_argument("command", nargs="?", choices=["serve", "set-password", "logout-all"], default="serve")
    parser.add_argument("--user", help="whose password to reset (set-password); defaults to the owner")
    parser.add_argument("--show", action="store_true", help="show the password as you type it (set-password)")
    parser.add_argument("--lan", action="store_true",
                        help="listen on the local network too, so phones and other PCs can connect")
    parser.add_argument("--server", action="store_true",
                        help="shared public server: open sign-up, free-tier limits, no host-only features")
    parser.add_argument("--host", help="address to listen on (server mode default: 127.0.0.1, behind a proxy)")
    parser.add_argument("--port", type=int, help="port to listen on (default 5000)")
    args = parser.parse_args(argv)

    if args.server:
        os.environ["COMPASS_MODE"] = "server"           # so every server-mode default in Config applies
    cfg = Config.from_env()
    lan = (args.lan or os.environ.get("COMPASS_LAN") == "1") and not cfg.server
    addresses = lan_addresses() if lan else []
    if lan:
        names = {socket.gethostname().lower(), socket.gethostname().lower() + ".local", *addresses}
        cfg = replace(cfg, allowed_hosts=cfg.allowed_hosts | frozenset(names))
    app = create_app(cfg)
    svc = app.extensions["compass"]

    if args.command == "set-password":
        return set_password(app, args.user, args.show)
    if args.command == "logout-all":
        print(f"Signed out {svc.accounts.end_everyone()} session(s).")
        return 0

    port = args.port or int(os.environ.get("COMPASS_PORT", "5000"))
    if cfg.server:
        return serve_shared(app, cfg, svc, args.host or os.environ.get("COMPASS_HOST", "127.0.0.1"), port)
    users = len(svc.accounts.list_users())
    print(f"Compass AI  http://127.0.0.1:{port}")
    if users:
        print(f"  accounts: {users}")
    else:
        print(f"  no accounts yet: open http://127.0.0.1:{port} on THIS PC to create the owner account")
    print(f"  brain:    Ollama at {cfg.ollama_host} (each person picks their model in Settings)")
    print(f"  memory:   {cfg.home}")
    if lan:
        print("  Other devices on your network:")
        for addr in addresses or ["(no network address found)"]:
            print(f"    http://{addr}:{port}")
        print("  This is plain http: use it on a network you trust. For the internet, use a tunnel instead.")
    # 127.0.0.1 only unless --lan: a Cloudflare tunnel reaches the app locally, nothing else can.
    app.run(host="0.0.0.0" if lan else "127.0.0.1", port=port, debug=False, threaded=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
