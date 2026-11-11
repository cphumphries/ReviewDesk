"""Settings that belong to one person on one computer, never to the shared project folder:
recent projects, who they are in each project, and private keys (the AI service's API key: on Windows encrypted
for this Windows account with the system's data protection, elsewhere in a file only this account can read)."""
from __future__ import annotations

import os
import secrets
import sys
import threading
import time

from . import util


def config_dir() -> str:
    if os.environ.get("REVIEWDESK_HOME"):
        return os.environ["REVIEWDESK_HOME"]
    if sys.platform.startswith("win"):
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
        return os.path.join(base, "ReviewDesk")
    if sys.platform == "darwin":
        return os.path.join(os.path.expanduser("~"), "Library", "Application Support", "ReviewDesk")
    return os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config"), "reviewdesk")


def path() -> str:
    return os.path.join(config_dir(), "settings.json")


_DATA_DIRS: dict = {}
_MOVE_LOCK = threading.Lock()
_LOCK = threading.RLock()        # every change to settings.json: load, change and save under it


def data_dir() -> str:
    """Where this computer keeps its own copies of project files (for the data checks), including PDFs. On Windows
    this is the local, non-roaming part of the profile (%LOCALAPPDATA%), so the copies are not copied to an
    organisation's profile servers. Copies kept by earlier versions in %APPDATA% are moved there once in each run
    (under a lock, so requests arriving together all wait for the move and all get the new place); a 'projects'
    folder an earlier version makes there again later is merged in at the next start."""
    if os.environ.get("REVIEWDESK_HOME") or not sys.platform.startswith("win") or not os.environ.get("LOCALAPPDATA"):
        return config_dir()
    new = os.path.join(os.environ["LOCALAPPDATA"], "ReviewDesk")
    old_projects = os.path.join(config_dir(), "projects")
    key = (old_projects, new)
    got = _DATA_DIRS.get(key)
    if got:
        return got
    with _MOVE_LOCK:
        got = _DATA_DIRS.get(key)
        if not got:
            got = new if move_local_copies(old_projects, os.path.join(new, "projects")) else config_dir()
            _DATA_DIRS[key] = got
    return got


def move_local_copies(old: str, new: str) -> bool:
    """Move this computer's copies from an earlier version's folder (old) to the new one: True if the new folder is
    the one to use. A rename where both are on one drive; otherwise (a roaming profile redirected to a server, say)
    each file is copied, checked against the original, and only then removed from the old place. If the new folder
    already exists, the old one is merged into it: a file only in the old folder is moved; a file in both is kept as
    the newer of the two, and the other is set aside in a folder next to the new one, never deleted."""
    if not os.path.isdir(old):
        return True
    try:
        os.makedirs(os.path.dirname(new), exist_ok=True)
        if not os.path.exists(new):
            try:
                os.replace(old, new)                          # one drive: a rename, all or nothing
                return True
            except OSError:
                pass                                          # another drive: copied below
            part = new + ".moving"                            # (a part-copy left by a move that stopped is
            _copy_tree(old, part)                             # completed: the old folder was still the one in use)
            os.replace(part, new)                             # every file copied and checked before any is removed
            _remove_tree(old)
            return True
        _merge(old, new, time.strftime("%Y%m%d-%H%M%S"))
        return True
    except OSError:
        return os.path.isdir(new)        # the first move could not be made: keep using the old place (tried again
        # at the next start); a merge stopped part-way: the new place stays in use, and the rest is merged next time


def _same(a: str, b: str) -> bool:
    try:
        if os.path.getsize(a) != os.path.getsize(b):
            return False
        return util.sha256_file(a) == util.sha256_file(b)
    except OSError:
        return False


def _copy_file(src: str, dst: str) -> None:
    """Copy one file (contents and modification time) and check the copy is identical."""
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    tmp = dst + ".copying"
    with open(src, "rb") as fi, open(tmp, "wb") as fo:
        for block in iter(lambda: fi.read(1 << 20), b""):
            fo.write(block)
        fo.flush()
        os.fsync(fo.fileno())
    st = os.stat(src)
    os.utime(tmp, ns=(st.st_atime_ns, st.st_mtime_ns))
    if not _same(src, tmp):
        os.remove(tmp)
        raise OSError(f"The copy of {src} is not identical to it")
    os.replace(tmp, dst)


def _files(root: str) -> list:
    out = []
    for d, _, names in os.walk(root):
        out += [os.path.relpath(os.path.join(d, n), root) for n in names]
    return sorted(out)


def _copy_tree(src: str, dst: str) -> None:
    for rel in _files(src):
        if not _same(os.path.join(src, rel), os.path.join(dst, rel)):
            _copy_file(os.path.join(src, rel), os.path.join(dst, rel))


def _remove_tree(root: str) -> None:
    """Remove a folder whose files have all been copied (whatever cannot be removed now is merged at the next start)."""
    for d, dirs, names in os.walk(root, topdown=False):
        for n in names:
            try:
                os.remove(os.path.join(d, n))
            except OSError:
                pass
        try:
            os.rmdir(d)
        except OSError:
            pass


def _merge(old: str, new: str, stamp: str) -> None:
    """Merge the folder old into new without losing anything (see move_local_copies), then remove old."""
    aside = os.path.join(os.path.dirname(new), f"{os.path.basename(new)} set aside {stamp}")
    for rel in _files(old):
        src, dst = os.path.join(old, rel), os.path.join(new, rel)
        if os.path.exists(dst) and not _same(src, dst):
            if os.path.getmtime(src) > os.path.getmtime(dst):      # the earlier version's copy is newer: it is used
                _copy_file(dst, os.path.join(aside, rel))
                _copy_file(src, dst)
            else:                                                  # the copy in use is newer: it stays
                _copy_file(src, os.path.join(aside, rel))
        elif not os.path.exists(dst):
            _copy_file(src, dst)
        os.remove(src)
    _remove_tree(old)


# ------------------------------------------------------------------------------------------------ the AI key
_DPAPI_PREFIX = "dpapi:"


def _dpapi(data: bytes, protect: bool) -> bytes:
    """Encrypt or decrypt with Windows data protection (DPAPI), tied to this Windows account."""
    import ctypes
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    buf = ctypes.create_string_buffer(data, len(data))
    inp = Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    out = Blob()
    crypt32, kernel32 = ctypes.windll.crypt32, ctypes.windll.kernel32
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    fn = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
    if not fn(ctypes.byref(inp), None, None, None, None, 0x1, ctypes.byref(out)):   # 0x1: never show a prompt
        raise OSError("Windows data protection refused the request")
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        kernel32.LocalFree(ctypes.cast(out.pbData, ctypes.c_void_p))


def protect(secret: str) -> str:
    """How a key is stored: encrypted for this Windows account where Windows can do so, otherwise as it is (in a
    settings file only this account can read)."""
    if not secret or not sys.platform.startswith("win"):
        return secret
    try:
        import base64
        return _DPAPI_PREFIX + base64.b64encode(_dpapi(secret.encode("utf-8"), True)).decode("ascii")
    except Exception:                                        # noqa: BLE001 - keep working without encryption
        return secret


def unprotect(stored: str) -> str:
    """The key from its stored form; '' if it was encrypted for another account or computer and cannot be read."""
    if not stored or not stored.startswith(_DPAPI_PREFIX):
        return stored or ""
    try:
        import base64
        return _dpapi(base64.b64decode(stored[len(_DPAPI_PREFIX):]), False).decode("utf-8")
    except Exception:                                        # noqa: BLE001
        return ""


def ai_key() -> str:
    """The AI service's key, ready to use. A key saved by an earlier version without encryption is encrypted now."""
    stored = load().get("elm_key") or ""
    key = unprotect(stored)
    if key and stored == key and sys.platform.startswith("win"):
        sealed = protect(key)
        if sealed != key:
            with _LOCK:
                cfg = load()
                if cfg.get("elm_key") == stored:
                    cfg["elm_key"] = sealed
                    save(cfg)
    return key


DEFAULTS = {"recent": [], "identity": {}, "elm_key": "", "elm_url": "",         # the AI service's key and address
            "unpaywall_email": "", "my_name": ""}


def load() -> dict:
    cfg = dict(DEFAULTS)
    cfg.update(util.read_json(path(), {}) or {})
    return cfg


def save(cfg: dict) -> None:
    """Write the settings (callers that changed what they loaded hold _LOCK). The computer's ID seed and signing
    secrets already in use are always kept, even if settings.json was removed meanwhile, so the ID never changes
    while ReviewDesk runs."""
    seed = _SEED.get(path())
    if seed and not cfg.get("device_seed"):
        cfg["device_seed"] = seed
    known = {h.rpartition("|")[2]: v for h, v in _SECRET.items() if h.rpartition("|")[0] == config_dir()}
    if known and any(k not in (cfg.get("device_secrets") or {}) for k in known):
        cfg["device_secrets"] = {**known, **dict(cfg.get("device_secrets") or {})}
    util.write_json_atomic(path(), cfg)
    try:
        os.chmod(path(), 0o600)
    except OSError:
        pass


def update(**changes) -> dict:
    with _LOCK:
        cfg = load()
        cfg.update(changes)
        save(cfg)
        return cfg


_SEED: dict = {}          # settings file -> this computer's ID seed, once read (it never changes while ReviewDesk runs)
_HOST: dict = {}          # settings file -> the host name a Mac's ID is made with (see device_id)


def _host_name() -> str:
    import socket
    try:
        return socket.gethostname()
    except OSError:
        return ""


def device_id() -> str:
    """A short name for this computer, made once. Files a person writes in a shared project carry it
    (decisions/CH@3f9a1c.jsonl), so someone who works on two computers never has two computers writing one file.
    It mixes in the computer's own name, so two computers whose settings were copied (or roam: managed Windows
    desktops, shared Linux home folders) still differ. A Mac's host name follows the network it is on, so on a Mac the
    name seen when the ID was first made (or, for an ID made by an earlier version, at the first start of this one) is
    kept in the settings and used from then on: the same Mac keeps one ID at home and on campus."""
    import hashlib
    key = path()
    mac = sys.platform == "darwin"
    d, host = _SEED.get(key), _HOST.get(key) if mac else None
    if not d or (mac and host is None):
        with _LOCK:
            cfg = load()
            d = cfg.get("device_seed") or _SEED.get(key) or ""
            changed = not cfg.get("device_seed")
            if not d:
                d = secrets.token_hex(8)
            if mac:
                host = cfg.get("device_host")
                if not isinstance(host, str):
                    host = _host_name()
                    cfg["device_host"] = host
                    changed = True
                _HOST[key] = host
            _SEED[key] = d
            if changed:
                cfg["device_seed"] = d
                save(cfg)
    if host is None:
        host = _host_name()
    return hashlib.sha256(f"{d}|{host}".encode("utf-8")).hexdigest()[:6]


_SECRET: dict = {}


def device_secret() -> str:
    """A secret that never leaves this computer. Rows this computer writes in its own files carry a signature made
    with it, so this computer can tell its own rows from any written by something else (even with a valid
    fingerprint)."""
    dev = device_id()
    home = config_dir() + "|" + dev
    if home in _SECRET:
        return _SECRET[home]
    with _LOCK:                       # one per computer: settings copied to another computer get a secret of their own
        cfg = load()
        all_ = dict(cfg.get("device_secrets") or {})
        s = all_.get(dev) or ""
        if not s:
            s = secrets.token_hex(16)
            all_[dev] = s
            cfg["device_secrets"] = all_
            save(cfg)
    _SECRET[home] = s
    return s


def remember_key(folder: str, key: str) -> None:
    """Which project lives in which folder on this computer (so it is known even if the project file is damaged)."""
    folder = os.path.normcase(os.path.abspath(folder))
    with _LOCK:
        cfg = load()
        keys = cfg.get("project_keys") or {}
        if keys.get(folder) != key:
            keys[folder] = key
            cfg["project_keys"] = keys
            save(cfg)


def known_key(folder: str) -> str:
    return (load().get("project_keys") or {}).get(os.path.normcase(os.path.abspath(folder)), "")


def session_token() -> str:
    """The key the browser shows with every request, kept between runs so a tab left open still works after a
    restart. It never leaves this computer."""
    with _LOCK:
        cfg = load()
        t = cfg.get("session_token") or ""
        if len(t) < 24:
            t = secrets.token_urlsafe(24)
            cfg["session_token"] = t
            save(cfg)
    return t


def same_folder(a: str, b: str) -> bool:
    """The same folder, however it was typed (on Windows: in any letter case, with / or \\, with or without a \\ at
    the end)."""
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


def remember_project(folder: str, title: str) -> None:
    folder = os.path.abspath(folder)
    with _LOCK:
        cfg = load()
        rec = [r for r in cfg.get("recent", []) if not same_folder(r.get("path", ""), folder)]
        rec.insert(0, {"path": folder, "title": title, "opened": util.now()})
        cfg["recent"] = rec[:12]
        save(cfg)


def identity_for(folder: str, key: str = "") -> str:
    ids = load().get("identity") or {}
    return ids.get(os.path.abspath(folder)) or (ids.get("key:" + key, "") if key else "")


def set_identity(folder: str, reviewer_id: str, key: str = "") -> None:
    with _LOCK:
        cfg = load()
        ids = cfg.get("identity") or {}
        ids[os.path.abspath(folder)] = reviewer_id
        if key:
            ids["key:" + key] = reviewer_id        # still known if the folder is moved or renamed
        cfg["identity"] = ids
        save(cfg)


def public_view(cfg: dict) -> dict:
    """What the browser may see: never the key itself."""
    out = {k: v for k, v in cfg.items() if k not in ("elm_key", "device_seed", "device_secret", "device_secrets", "session_token", "project_keys")}
    k = unprotect(cfg.get("elm_key") or "")
    out["elm_key_set"] = bool(k)
    out["elm_key_hint"] = ("ending " + k[-4:]) if len(k) > 10 else ("set" if k else "")
    out["config_file"] = path()
    return out
