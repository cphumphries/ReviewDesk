"""Start ReviewDesk: run the local server and open it in the web browser."""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import threading
import time
import webbrowser

from . import NAME, VERSION, server

# The exit code when another version of ReviewDesk is already running: the start files ("Start ReviewDesk.bat", the
# Mac .command) then keep the window open, so the message can be read.
OTHER_VERSION_RUNNING = 3


# The exit code when ReviewDesk cannot start (its settings folder cannot be written, or the port asked for is taken).
CANNOT_START = 1


def _port(text: str) -> int:
    try:
        n = int(text)
    except ValueError:
        n = -1
    if not 0 <= n <= 65535:
        raise argparse.ArgumentTypeError(f"{text!r} is not a port: give a whole number from 1 to 65535 (or 0 for any free port)")
    return n


def main(argv=None):
    ap = argparse.ArgumentParser(prog="reviewdesk", description=f"{NAME} {VERSION}")
    ap.add_argument("--port", type=_port, default=None, help=f"port to listen on (default: {server.DEFAULT_PORT}, "
                    "or any free port if that one is taken)")
    ap.add_argument("--no-browser", action="store_true", help="do not open the web browser")
    a = ap.parse_args(argv)
    if sys.version_info < (3, 9):
        sys.exit(f"{NAME} needs Python 3.9 or later; this is Python {sys.version.split()[0]}.")
    try:
        running = _already_running() if a.port is None else None
    except OSError as e:                          # (the session token is read, or made and saved, first)
        print(settings_problem(e), flush=True)
        return CANNOT_START
    if running and running[0] == VERSION and running[1]:     # started twice: use the copy that is already running
        print(f"\n  {NAME} is already running. Opening it in your web browser:\n\n    {running[1]}\n")
        if not a.no_browser:
            webbrowser.open(running[1])
        return 0
    if running and running[0] != VERSION:
        print(other_version_message(running[0]), flush=True)
        sys.exit(OTHER_VERSION_RUNNING)
    try:
        httpd, url = server.serve(a.port)
    except server.PortTaken as e:
        print("\n" + _wrap(f"{NAME} cannot use port {e.port}: another program on this computer is using it. Start "
                           f"{NAME} without --port (it then finds a free port by itself), or choose another port.") + "\n",
              flush=True)
        return CANNOT_START
    except OSError as e:
        print(settings_problem(e), flush=True)
        return CANNOT_START
    port = httpd.server_address[1]
    print(f"\n  {NAME} {VERSION} is running.\n")
    if a.port is None and port != server.DEFAULT_PORT:
        print(_wrap(f"Another program on this computer is using ReviewDesk's usual address (port {server.DEFAULT_PORT}), "
                    f"so ReviewDesk is using port {port} instead. Browser tabs and bookmarks from earlier will not work "
                    "this time: use the address below.") + "\n")
    print(_wrap("It should open in your web browser. If it does not, copy this address into the browser:"))
    print(f"\n    {url}\n")
    print(_wrap(stop_instruction()) + "\n", flush=True)
    if os.environ.get("REVIEWDESK_URL_FILE"):
        with open(os.environ["REVIEWDESK_URL_FILE"], "w", encoding="utf-8") as f:
            f.write(url)
    if not a.no_browser:
        threading.Thread(target=lambda: (time.sleep(0.6), webbrowser.open(url)), daemon=True).start()
    try:
        httpd.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        print("\n  ReviewDesk stopped.")
    finally:
        httpd.server_close()
    return 0


def settings_problem(e: OSError) -> str:
    """Why ReviewDesk cannot start when its own settings folder (on this computer, not the project folder) cannot be
    written: a full disk, a read-only home folder, no permission."""
    from . import userconfig
    try:
        where = userconfig.config_dir()
    except Exception:                             # noqa: BLE001 - the message must still be shown
        where = "its settings folder"
    why = (e.strerror or "").strip() or "it could not be written"
    return ("\n" + _wrap(f"{NAME} cannot start: it could not save its settings in {where} ({why}). ReviewDesk keeps the "
                         "key for this browser and its copies for the data checks there.") + "\n\n"
            + _wrap("Check that the disk is not full and that you can save files in that folder (on a work computer, ask "
                    "your IT team), then start ReviewDesk again.") + "\n")


def stop_instruction(platform: str = "") -> str:
    """How to stop ReviewDesk, as this computer's console does it. (In Windows, Ctrl+C in a window started from a
    .bat file asks 'Terminate batch job (Y/N)?' as well.)"""
    platform = platform or sys.platform
    if platform.startswith("win"):
        return "Keep this window open while you work. To stop ReviewDesk, close this window (or press Ctrl+C, then Y)."
    return "Keep this window open while you work. To stop ReviewDesk, close this window (or press Ctrl+C)."


def other_version_message(running: str, platform: str = "") -> str:
    """What to do when another version of ReviewDesk is already running on this computer."""
    platform = platform or sys.platform
    other = f"{NAME} {running}" if running else "another version of ReviewDesk"
    if platform.startswith("win"):
        where = (f"find the other black {NAME} window (the one that says {other} is running) and close it: that stops it. "
                 f"Then double-click \"Start ReviewDesk\" again.")
    elif platform == "darwin":
        where = (f"find the Terminal window where {other} is running and close it: that stops it. Then double-click "
                 f"\"Start ReviewDesk (Mac)\" again.")
    else:
        where = f"stop {other} (close its window, or press Ctrl+C in it), then start ReviewDesk again."
    return ("\n" + _wrap(f"{other} is already running on this computer, so {NAME} {VERSION} cannot start.") + "\n\n"
            + _wrap(f"To use {NAME} {VERSION}, {where} (Your work is saved in the project folder, so nothing is lost when "
                    "you close it.)") + "\n")


def _wrap(text: str) -> str:
    import textwrap
    return textwrap.fill(text, width=78, initial_indent="  ", subsequent_indent="  ", break_on_hyphens=False,
                         break_long_words=False)         # fits a console of 80; a folder's path is never cut in two


def _already_running():
    """(its version, its address) of a ReviewDesk already listening on the usual port on this computer, or None. The
    address is '' when it is another person's (it does not accept this computer's settings)."""
    import hmac
    import http.client
    import secrets
    from . import userconfig
    token = userconfig.session_token()
    once = secrets.token_hex(16)
    try:
        c = http.client.HTTPConnection("127.0.0.1", server.DEFAULT_PORT, timeout=2)
        try:
            # The token is NOT sent: whatever is listening may be another program. A ReviewDesk with this computer's
            # settings proves itself by signing the number sent here with the token (server.Handler._probe).
            c.request("GET", "/api/probe?n=" + once, headers={"Host": f"127.0.0.1:{server.DEFAULT_PORT}"})
            r = c.getresponse()
            head, body = r.getheader("Server") or "", r.read(65536)
        finally:
            c.close()
    except (OSError, http.client.HTTPException):
        return None
    m = re.search(re.escape(NAME) + r"/(\S+)", head)
    if not m:
        return None                                   # another program: ReviewDesk starts on another port
    version = m.group(1)                              # (versions before 1.3.0 refuse the question: their version is here)
    try:
        j = json.loads(body.decode("utf-8")) if r.status == 200 else {}
    except (ValueError, UnicodeDecodeError):
        j = {}
    if not isinstance(j, dict):
        j = {}
    want = hmac.new(token.encode("utf-8"), once.encode("utf-8"), "sha256").hexdigest()
    if hmac.compare_digest(str(j.get("proof") or ""), want):
        return str(j.get("version") or version), f"http://127.0.0.1:{server.DEFAULT_PORT}/?t={token}"
    return str(j.get("version") or version), ""


if __name__ == "__main__":
    sys.exit(main())
