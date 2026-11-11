"""Build the download zips into dist/:

    ReviewDesk-<version>.zip           Mac and Linux (and Windows with Python already installed)
    ReviewDesk-<version>-windows.zip   Windows, with the Python embeddable package from python.org
    SHA256SUMS.txt                     the SHA-256 of each zip, for the release notes

    python tools/build_release.py [--no-windows] [--python-zip FILE]

In a git working copy the zips hold only files that git tracks, and the build stops if any of them has changes that
are not committed. From a source download with no .git folder, the files on disk are used, and the build says so.
The Python package is downloaded from python.org (or taken from --python-zip) and checked against the SHA-256 below
before anything is written; dist/ is then emptied, so it never holds zips and checksums from two different builds.
The zips are the same byte for byte each time they are built from the same files with the same Python and zlib (both
are printed at the end, for the release notes).
"""
from __future__ import annotations

import argparse
import hashlib
import io
import os
import platform
import re
import shutil
import subprocess
import sys
import urllib.request
import zipfile
import zlib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY_VERSION = "3.14.10"
PY_URL = f"https://www.python.org/ftp/python/{PY_VERSION}/python-{PY_VERSION}-embed-amd64.zip"
PY_SHA256 = "90905e64a840557d8c1bc4f5b74db3d7ee85e3ffb217611cac1c7813acea4159"     # as python.org's release page gives it
PY_TAG = "python" + "".join(PY_VERSION.split(".")[:2])            # python314
# ReviewDesk's own ._pth file (it replaces python.org's): the standard library, this folder, and ReviewDesk's app folder
PTH = f"{PY_TAG}.zip\r\n.\r\n..\\app\r\n\r\n# Uncomment to run site.main() automatically\r\n#import site\r\n"
EXECUTABLE = {"Start ReviewDesk (Mac).command", "start-reviewdesk.sh"}
LAUNCHERS = ("Start ReviewDesk.bat", "Start ReviewDesk (Mac).command", "start-reviewdesk.sh", "READ ME FIRST.txt")
SOURCES = ["app", "launchers", "docs", "LICENSE", "NOTICE.txt"]       # everything a zip is made from
JUNK = (".DS_Store", "Thumbs.db", "desktop.ini")


class BuildError(Exception):
    """The build cannot go on; the message says why and what to do."""


def version() -> str:
    with open(os.path.join(ROOT, "app", "reviewdesk", "__init__.py"), encoding="utf-8") as f:
        text = f.read()
    return re.search(r'^VERSION = "([^"]+)"', text, re.M).group(1)


def _git(*args: str) -> bytes:
    return subprocess.run(["git", "-C", ROOT, *args], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE).stdout


def in_git() -> bool:
    """True when ROOT is a git working copy (a clone or a worktree), so the build must use what git tracks."""
    return os.path.exists(os.path.join(ROOT, ".git"))


def source_files(allow_changes: bool = False) -> tuple[list, str]:
    """(paths relative to ROOT with '/', a sentence on where they came from)."""
    if in_git():
        try:
            listed = _git("ls-files", "-z", "--", *SOURCES).decode("utf-8").split("\0")
            changed = _git("status", "--porcelain", "--untracked-files=no", "--", *SOURCES).decode("utf-8")
        except (OSError, subprocess.CalledProcessError) as e:
            raise BuildError(f"This is a git working copy, but git could not list its files ({e}). Install git, or "
                             "build from a copy with no .git folder.")
        if changed.strip():
            if not allow_changes:
                raise BuildError("These files that go into the zips have changes that are not committed, so the zips would "
                                 "not match any commit:\n" + changed.rstrip() + "\nCommit them (or undo them) and build "
                                 "again. To build anyway, for a test only, add --allow-uncommitted.")
            print("WARNING: building with changes that are not committed. These zips match no commit: do not publish them.\n"
                  + changed.rstrip(), file=sys.stderr)
        return sorted(p for p in listed if p), "the files git tracks in this working copy"
    out = []
    for top in SOURCES:
        full = os.path.join(ROOT, top)
        if os.path.isfile(full):
            out.append(top)
            continue
        for d, dirs, names in os.walk(full):
            dirs[:] = sorted(x for x in dirs if x != "__pycache__" and not x.startswith("."))
            for n in sorted(names):
                if n.endswith((".pyc", ".pyo", "~")) or n in JUNK or n.startswith("."):
                    continue
                out.append(os.path.relpath(os.path.join(d, n), ROOT).replace(os.sep, "/"))
    return sorted(out), "the files on disk (there is no .git folder here, so nothing checks they match a commit)"


def files(sources: list) -> list:
    """(name in the zip, path on disk): the app, the start files, the licences and the user guides."""
    out = [(p, os.path.join(ROOT, *p.split("/"))) for p in sources if p.startswith("app/")]
    for n in LAUNCHERS:
        if f"launchers/{n}" not in sources:
            raise BuildError(f"launchers/{n} is missing.")
        out.append((n, os.path.join(ROOT, "launchers", n)))
    out += [("LICENSE.txt", os.path.join(ROOT, "LICENSE")), ("NOTICE.txt", os.path.join(ROOT, "NOTICE.txt"))]
    for p in sources:                                  # the user guides: the files directly in docs/, not docs/dev or images
        parts = p.split("/")
        if len(parts) == 2 and parts[0] == "docs" and p.lower().endswith((".docx", ".pdf")):
            out.append((parts[1], os.path.join(ROOT, "docs", parts[1])))
    return out


def add(z: zipfile.ZipFile, top: str, name: str, data: bytes) -> None:
    info = zipfile.ZipInfo(f"{top}/{name}", date_time=(2026, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_DEFLATED
    info.create_system = 3                               # Unix, so the permission bits below are kept
    info.external_attr = ((0o100755 if os.path.basename(name) in EXECUTABLE else 0o100644) << 16)
    z.writestr(info, data)


def python_package(local: str | None = None) -> bytes:
    if local:
        print("Using", local)
        with open(local, "rb") as f:
            data = f.read()
    else:
        print("Downloading", PY_URL)
        try:
            with urllib.request.urlopen(PY_URL, timeout=120) as r:
                data = r.read()
        except OSError as e:
            raise BuildError(f"Could not download {PY_URL} ({e}). Nothing was written. Try again, or download it yourself "
                             "and give it with --python-zip.")
    got = hashlib.sha256(data).hexdigest()
    if got != PY_SHA256:
        raise BuildError(f"The Python package's SHA-256 is {got}, not {PY_SHA256}: not using it. Nothing was written.")
    return data


def made_with() -> str:
    return (f"Built with Python {platform.python_version()} ({platform.python_implementation()}), zlib "
            f"{zlib.ZLIB_RUNTIME_VERSION}, on {platform.system()} {platform.machine()}.")


def build(windows: bool = True, python_zip: str | None = None, allow_changes: bool = False) -> list:
    v = version()
    top = f"ReviewDesk-{v}"
    sources, where = source_files(allow_changes)
    print(f"Building ReviewDesk {v} from {where}.")
    contents = files(sources)
    py = zipfile.ZipFile(io.BytesIO(python_package(python_zip))) if windows else None
    dist = os.path.join(ROOT, "dist")              # only now that everything needed is at hand: start from an empty dist/
    shutil.rmtree(dist, ignore_errors=True)
    os.makedirs(dist)
    made = []
    plain = os.path.join(dist, f"{top}.zip")
    with zipfile.ZipFile(plain, "w") as z:
        for name, path in contents:
            with open(path, "rb") as f:
                add(z, top, name, f.read())
    made.append(plain)
    if py is not None:
        win = os.path.join(dist, f"{top}-windows.zip")
        with zipfile.ZipFile(win, "w") as z:
            for name, path in contents:
                if name not in EXECUTABLE:
                    with open(path, "rb") as f:
                        add(z, top, name, f.read())
            for n in sorted(py.namelist()):
                if not n.endswith("/") and not n.endswith("._pth"):
                    add(z, top, "python/" + n, py.read(n))
            add(z, top, f"python/{PY_TAG}._pth", PTH.encode("ascii"))
        made.append(win)
    sums = []
    for m in made:
        with open(m, "rb") as f:
            h = hashlib.sha256(f.read()).hexdigest()
        sums.append(f"{h}  {os.path.basename(m)}\n")
        print(f"{os.path.relpath(m, ROOT)}  {os.path.getsize(m):,} bytes  sha256 {h}")
    with open(os.path.join(dist, "SHA256SUMS.txt"), "w", encoding="ascii", newline="\n") as f:
        f.writelines(sums)
    print(made_with())
    return made


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--no-windows", action="store_true", help="skip the Windows zip (no download)")
    ap.add_argument("--python-zip", metavar="FILE", help=f"use this copy of python-{PY_VERSION}-embed-amd64.zip instead "
                    "of downloading it (it is checked against the same SHA-256)")
    ap.add_argument("--allow-uncommitted", action="store_true", help="build even with uncommitted changes (for a test "
                    "only: the zips then match no commit)")
    a = ap.parse_args()
    try:
        build(windows=not a.no_windows, python_zip=a.python_zip, allow_changes=a.allow_uncommitted)
    except BuildError as e:
        sys.exit(str(e))
