"""What a stranger meets outside the program itself: the start files run from the source code as well as from the
download, the release build ships only what git tracks and never leaves a wrong checksum file, the tests say when
their temporary folder is too deep, and the documents say exactly what the data checks can and cannot do."""
from __future__ import annotations

import hashlib
import importlib.util
import io
import os
import shutil
import subprocess
import sys
import zipfile

import pytest

import conftest
from helpers import HERE, read_text

ROOT = os.path.dirname(HERE)
LAUNCHERS = os.path.join(ROOT, "launchers")
needs_sh = pytest.mark.skipif(sys.platform.startswith("win") or not shutil.which("sh"), reason="needs a Unix shell")


def _fake_tree(tmp_path, layout: str):
    """A folder laid out as the download ('zip': the start files beside app/) or as the source code ('source': the start
    files in launchers/, app/ one folder up), whose run_reviewdesk.py only says where it is and what it was given."""
    top = tmp_path / layout
    starts = top if layout == "zip" else top / "launchers"
    (top / "app").mkdir(parents=True)
    starts.mkdir(parents=True, exist_ok=True)
    (top / "app" / "run_reviewdesk.py").write_text(
        "import os, sys\nprint('STARTED', os.path.basename(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),"
        " sys.argv[1:])\n", encoding="utf-8")
    for n in ("start-reviewdesk.sh", "Start ReviewDesk (Mac).command"):
        shutil.copy(os.path.join(LAUNCHERS, n), starts / n)
    return starts


def _run(cmd, cwd=None):
    r = subprocess.run(cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=60)
    return r.returncode, r.stdout.decode("utf-8", "replace")


# ================================================================================================ the start files
@needs_sh
@pytest.mark.parametrize("layout", ["zip", "source"])
def test_the_linux_start_file_finds_the_app_in_the_download_and_in_the_source_code(tmp_path, layout):
    starts = _fake_tree(tmp_path, layout)
    code, out = _run(["sh", str(starts / "start-reviewdesk.sh"), "--no-browser", "--port", "8123"], cwd="/")
    assert code == 0 and f"STARTED {layout} ['--no-browser', '--port', '8123']" in out, out


@needs_sh
def test_the_linux_start_file_works_through_a_link(tmp_path):
    starts = _fake_tree(tmp_path, "zip")
    (tmp_path / "desktop").mkdir()
    os.symlink(str(starts / "start-reviewdesk.sh"), str(tmp_path / "desktop" / "ReviewDesk"))
    code, out = _run(["sh", str(tmp_path / "desktop" / "ReviewDesk")], cwd="/")
    assert code == 0 and "STARTED zip" in out, out


@needs_sh
def test_the_linux_start_file_says_what_is_wrong_without_its_app_folder(tmp_path):
    shutil.copy(os.path.join(LAUNCHERS, "start-reviewdesk.sh"), tmp_path / "start-reviewdesk.sh")
    code, out = _run(["sh", str(tmp_path / "start-reviewdesk.sh")])
    assert code == 1 and 'its "app" folder is not next to this file' in out and "Traceback" not in out


@needs_sh
def test_the_linux_start_file_names_the_python_it_found_when_it_is_too_old(tmp_path):
    starts = _fake_tree(tmp_path, "zip")
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    for tool in ("dirname", "readlink"):
        os.symlink(shutil.which(tool), str(bin_ / tool))
    old = bin_ / "python3"            # a Python 3.6 that answers the version check as a real one would
    old.write_text("#!/bin/sh\ncase \"$2\" in *'>= (3, 9)'*) exit 1;; esac\necho 3.6.8\n", encoding="utf-8")
    old.chmod(0o755)
    env = {"PATH": str(bin_)}
    r = subprocess.run(["/bin/sh", str(starts / "start-reviewdesk.sh")], env=env, stdout=subprocess.PIPE,
                       stderr=subprocess.STDOUT, timeout=60)
    out = r.stdout.decode()
    assert r.returncode == 1 and "Python 3.6.8 (the command python3)" in out and "3.9 or later" in out, out
    old.unlink()
    r = subprocess.run(["/bin/sh", str(starts / "start-reviewdesk.sh")], env=env, stdout=subprocess.PIPE,
                       stderr=subprocess.STDOUT, timeout=60)
    assert r.returncode == 1 and "none was found on this computer" in r.stdout.decode()


def test_the_linux_start_file_tries_each_python_3_by_name():
    sh = read_text(os.path.join(LAUNCHERS, "start-reviewdesk.sh"))
    assert "python3.15 python3.14 python3.13 python3.12 python3.11 python3.10 python3.9" in sh


@needs_sh
@pytest.mark.skipif(not shutil.which("bash"), reason="needs bash")
@pytest.mark.parametrize("layout", ["zip", "source"])
def test_the_mac_start_file_finds_the_app_in_the_download_and_in_the_source_code(tmp_path, layout):
    starts = _fake_tree(tmp_path, layout)
    if sys.platform == "darwin" and not shutil.which("python3"):
        pytest.skip("no python3 on this Mac's PATH")
    code, out = _run(["bash", str(starts / "Start ReviewDesk (Mac).command"), "--no-browser"], cwd="/")
    assert code == 0 and f"STARTED {layout} ['--no-browser']" in out, out


def test_the_mac_start_file_skips_apples_stand_in_python_and_says_press_any_key_to_finish():
    mac = read_text(os.path.join(LAUNCHERS, "Start ReviewDesk (Mac).command"))
    assert 'xcode-select -p' in mac and '"$(command -v "$py")" = /usr/bin/python3' in mac
    assert "Press any key to close this window" not in mac          # Terminal does not close the window by itself
    assert mac.count('read -n 1 -s -r -p "Press any key to finish."') == 3


def test_the_windows_start_file_runs_the_source_code_with_an_installed_python_and_keeps_crlf():
    raw = open(os.path.join(LAUNCHERS, "Start ReviewDesk.bat"), "rb").read()
    if b"\r\n" in raw:                                   # a checkout that follows .gitattributes: every line ends CRLF
        assert raw.count(b"\n") == raw.count(b"\r\n")
    bat = raw.decode("ascii").replace("\r\n", "\n")
    source = 'if not exist "%~dp0app\\run_reviewdesk.py" if exist "%~dp0..\\app\\run_reviewdesk.py" set "SOURCE=1"'
    assert bat.index(source) < bat.index('if not exist "%~dp0app\\run_reviewdesk.py" goto notextracted')
    assert 'py -3 "%~dp0..\\app\\run_reviewdesk.py" %*' in bat and 'python "%~dp0..\\app\\run_reviewdesk.py" %*' in bat
    assert "if defined SOURCE goto nopythonsource" in bat
    # the source code has no python folder, so it never sends the user to "extract the whole zip file again"
    nopy = bat[bat.index(":nopythonsource"):bat.index(":notextracted")]
    assert "Extract" not in nopy and "Releases page" in nopy and "python.org" in nopy
    # the download itself still runs exactly as before
    assert '"%~dp0python\\python.exe" "%~dp0app\\run_reviewdesk.py" %*' in bat


@pytest.mark.skipif(not os.path.exists(os.path.join(ROOT, ".git")) or not shutil.which("git"), reason="not a git checkout")
def test_the_shell_start_files_are_executable_in_git():
    modes = subprocess.run(["git", "-C", ROOT, "ls-files", "-s", "launchers"], stdout=subprocess.PIPE).stdout.decode()
    for line in modes.splitlines():
        mode, name = line.split()[0], line.split("\t", 1)[1]
        assert mode == ("100755" if name.endswith((".sh", ".command")) else "100644"), line


# ================================================================================================ the release build
@pytest.fixture
def builder(tmp_path, monkeypatch):
    """tools/build_release.py on a small tree of its own, with a stand-in for python.org's package."""
    spec = importlib.util.spec_from_file_location("build_release_under_test", os.path.join(ROOT, "tools", "build_release.py"))
    br = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(br)
    tree = tmp_path / "tree"
    for rel, data in {"app/run_reviewdesk.py": "print('hi')\n", "app/reviewdesk/__init__.py": 'VERSION = "9.8.7"\n',
                      "launchers/Start ReviewDesk.bat": "@echo off\r\n", "launchers/start-reviewdesk.sh": "#!/bin/sh\n",
                      "launchers/Start ReviewDesk (Mac).command": "#!/bin/bash\n", "launchers/READ ME FIRST.txt": "Hi\n",
                      "docs/Guide.docx": "docx", "docs/dev/DESIGN.md": "dev", "LICENSE": "MIT\n", "NOTICE.txt": "N\n"}.items():
        f = tree / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(data.encode())
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("python.exe", b"MZ")
        z.writestr("python314._pth", b"python314.zip\r\n.\r\n")
    pyzip = tmp_path / "embed.zip"
    pyzip.write_bytes(buf.getvalue())
    monkeypatch.setattr(br, "ROOT", str(tree))
    monkeypatch.setattr(br, "PY_SHA256", hashlib.sha256(buf.getvalue()).hexdigest())
    br.pyzip = str(pyzip)
    return br, tree


def _git_init(tree):
    if not shutil.which("git"):
        pytest.skip("needs git")
    for args in (["init", "-q"], ["add", "-A"], ["-c", "user.name=T", "-c", "user.email=t@example.org", "commit", "-q", "-m", "x"]):
        subprocess.run(["git", "-C", str(tree), *args], check=True, stdout=subprocess.PIPE)


def _names(path):
    with zipfile.ZipFile(path) as z:
        return sorted(z.namelist())


def test_the_build_ships_only_what_git_tracks(builder):
    br, tree = builder
    _git_init(tree)
    for junk in ("app/reviewdesk/.DS_Store", "app/reviewdesk/notes.txt~", "app/reviewdesk/.idea/workspace.xml"):
        (tree / junk).parent.mkdir(parents=True, exist_ok=True)
        (tree / junk).write_text("junk")
    made = br.build(python_zip=br.pyzip)
    names = _names(made[0])
    assert "ReviewDesk-9.8.7/app/reviewdesk/__init__.py" in names and "ReviewDesk-9.8.7/Guide.docx" in names
    assert not [n for n in names if "DS_Store" in n or n.endswith("~") or ".idea" in n or "DESIGN" in n], names
    assert "ReviewDesk-9.8.7/python/python314._pth" in _names(made[1])


def test_the_build_refuses_uncommitted_changes(builder, capsys):
    br, tree = builder
    _git_init(tree)
    with open(tree / "app" / "run_reviewdesk.py", "a") as f:
        f.write("# an uncommitted line\n")
    with pytest.raises(br.BuildError, match="not committed"):
        br.build(python_zip=br.pyzip)
    assert not (tree / "dist").exists()
    br.build(python_zip=br.pyzip, allow_changes=True)               # for a test only, and it says so loudly
    assert "do not publish them" in capsys.readouterr().err


def test_a_failed_build_leaves_no_wrong_checksum_file(builder):
    br, tree = builder
    _git_init(tree)
    br.build(python_zip=br.pyzip)
    before = (tree / "dist" / "SHA256SUMS.txt").read_bytes()
    (tree / "app" / "run_reviewdesk.py").write_text("print('changed')\n")
    subprocess.run(["git", "-C", str(tree), "-c", "user.name=T", "-c", "user.email=t@example.org", "commit", "-qam", "y"],
                   check=True)
    bad = tree.parent / "bad.zip"
    bad.write_bytes(b"not python")
    with pytest.raises(br.BuildError, match="Nothing was written"):
        br.build(python_zip=str(bad))                               # Python is checked before anything is written
    assert (tree / "dist" / "SHA256SUMS.txt").read_bytes() == before
    for line in before.decode().splitlines():                      # ... so the checksums still match the zips there
        h, name = line.split("  ")
        assert hashlib.sha256((tree / "dist" / name).read_bytes()).hexdigest() == h


def test_the_build_empties_dist_first_and_is_the_same_twice(builder, capsys):
    br, tree = builder
    _git_init(tree)
    (tree / "dist").mkdir()
    (tree / "dist" / "ReviewDesk-1.0.0.zip").write_bytes(b"old")
    br.build(python_zip=br.pyzip)
    first = (tree / "dist" / "SHA256SUMS.txt").read_bytes()
    assert sorted(os.listdir(tree / "dist")) == ["ReviewDesk-9.8.7-windows.zip", "ReviewDesk-9.8.7.zip", "SHA256SUMS.txt"]
    br.build(python_zip=br.pyzip)
    assert (tree / "dist" / "SHA256SUMS.txt").read_bytes() == first
    out = capsys.readouterr().out
    assert "Built with Python " in out and ", zlib " in out


def test_the_build_works_from_a_source_download_with_no_git_folder(builder, capsys):
    br, tree = builder
    (tree / "app" / "reviewdesk" / ".DS_Store").write_text("junk")
    made = br.build(windows=False)
    assert "the files on disk (there is no .git folder here" in capsys.readouterr().out
    assert not [n for n in _names(made[0]) if "DS_Store" in n]


# ================================================================================================ the tests' temporary folder
def test_the_tests_say_when_their_temporary_folder_is_too_deep():
    assert conftest.temp_folder_too_deep("/tmp/pytest-of-sam/pytest-3") == ""
    why = conftest.temp_folder_too_deep("/" + "d" * 120)
    assert "121 characters long" in why and "at most 104" in why and "--basetemp" in why


# ================================================================================================ the documents
def test_the_documents_do_not_claim_more_than_the_data_checks_do():
    readme, security, contributing, design = (read_text(os.path.join(ROOT, n)) for n in
                                              ("README.md", "SECURITY.md", "CONTRIBUTING.md", "docs/dev/DESIGN.md"))
    for text in (readme, contributing):
        assert "Each person writes only their own files" not in text
        assert "attributed and fingerprinted" not in text
    assert "only the computer that wrote" in security or "only that computer" in security
    gaps = security[security.index("## What it does not protect against"):]
    assert "accidents and sync faults" in gaps and "in another person's name" in gaps
    assert "worksheet" in readme and "worksheet" in design


def test_upgrading_is_explained_where_users_look():
    for name in ("README.md", "UPGRADING.md", os.path.join("launchers", "READ ME FIRST.txt")):
        text = read_text(os.path.join(ROOT, name))
        assert "new folder" in text and "same day" in text, name


def test_every_relative_link_in_the_documents_resolves():
    import re
    import urllib.parse
    for name in ("README.md", "SECURITY.md", "CONTRIBUTING.md", "UPGRADING.md", "CODE_OF_CONDUCT.md",
                 os.path.join("docs", "dev", "RELEASING.md")):
        text = read_text(os.path.join(ROOT, name))
        for link in re.findall(r"\]\(([^)\s]+)\)", text):
            if re.match(r"[a-z]+:", link) or link.startswith("#") or link.startswith("../../"):
                continue
            target = os.path.join(os.path.dirname(os.path.join(ROOT, name)), urllib.parse.unquote(link.split("#")[0]))
            assert os.path.exists(target), f"{name}: {link}"


def test_notice_names_the_bundled_python_its_contents_and_prisma():
    notice = read_text(os.path.join(ROOT, "NOTICE.txt"))
    spec = importlib.util.spec_from_file_location("build_release_for_notice", os.path.join(ROOT, "tools", "build_release.py"))
    br = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(br)
    assert f"Python {br.PY_VERSION}," in notice and br.PY_SHA256 in notice and f"python/{br.PY_TAG}._pth" in notice
    for part in ("OpenSSL", "libffi", "bzip2", "SQLite", "zlib", "Expat", "mpdecimal", "XZ Utils", "Zstandard",
                 "vcruntime140.dll", "python/LICENSE.txt", "PRISMA 2020", "CC BY 4.0", "IBM Plex Sans 3.201",
                 "Source Serif 4, version 4.004"):
        assert part in notice, part
