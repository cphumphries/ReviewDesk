"""Every test runs with a private settings folder, never the real user's, and never reopens a real project."""
import atexit
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "app"))
sys.path.insert(0, HERE)
if not os.environ.get("REVIEWDESK_HOME"):
    os.environ["REVIEWDESK_HOME"] = tempfile.mkdtemp(prefix="reviewdesk-test-home-")
    atexit.register(shutil.rmtree, os.environ["REVIEWDESK_HOME"], ignore_errors=True)      # nothing is left behind
os.environ["REVIEWDESK_NO_REOPEN"] = "1"
for k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY", "all_proxy", "ALL_PROXY"):
    os.environ.pop(k, None)                      # the tests talk only to stand-ins on this computer
os.environ.pop("REVIEWDESK_AI_URL", None)        # never a real AI service
os.environ.pop("ELM_API_KEY", None)

import pytest  # noqa: E402

import unique_ids  # noqa: E402

unique_ids.apply()          # (see unique_ids.py: two imports given one ID by chance are refused, so tests never draw one twice)


# ReviewDesk refuses a project folder whose path is longer than project.LONG_PATH (163) characters, as Windows would, and
# the tests make projects up to 59 characters below pytest's temporary folder. So that folder must be short.
DEEPEST_TEST_PATH = 59


def temp_folder_too_deep(base: str) -> str:
    """'' if the tests' temporary folder is short enough; otherwise what to do about it, in a sentence."""
    from reviewdesk.project import LONG_PATH
    longest = LONG_PATH - DEEPEST_TEST_PATH
    if len(base) <= longest:
        return ""
    return (f"The tests' temporary folder ({base}) is {len(base)} characters long; the tests need it to be at most "
            f"{longest}, or many fail with 'That place is too deep in your folders'. Run them again with a short one, "
            "for example:  python -m pytest --basetemp=/tmp/rd-tests  (on Windows: --basetemp=C:\\rd-tests)")


def pytest_terminal_summary(terminalreporter, exitstatus, config):
    """Say once, at the end where it is read, when the temporary folder is too deep for the tests."""
    base = getattr(getattr(config, "_tmp_path_factory", None), "_basetemp", None)      # set once a test has used it
    why = temp_folder_too_deep(str(base)) if base else ""
    if why:
        terminalreporter.write_line("")
        terminalreporter.write_line(why, yellow=True, bold=True)


def pytest_collection_modifyitems(config, items):
    """The 3,000-record runs (marked slow) run only when asked for: python -m pytest -m slow."""
    if "slow" in (config.getoption("-m") or ""):
        return
    skip = pytest.mark.skip(reason="slow: run with  python -m pytest -m slow")
    for item in items:
        if "slow" in item.keywords:
            item.add_marker(skip)


@pytest.fixture
def desk(tmp_path):
    """ReviewDesk running on the lead's computer (its own settings folder), in this process."""
    from helpers import LocalServer
    s = LocalServer(str(tmp_path / "home-CH"))
    yield s
    s.stop()


@pytest.fixture
def elm():
    """The stand-in for the AI service (on this computer only)."""
    from mock_elm import MockELM
    m = MockELM().start()
    yield m
    m.stop()


@pytest.fixture
def quick_retries(monkeypatch):
    """AI retries without the waits between them (the waits asked for are kept in the list returned), and a short time
    limit for each call, so failures can be tested in seconds."""
    from reviewdesk import ai
    waits = []

    def wait(self, sec):
        waits.append(sec)
        if self.stopped():
            raise ai.Stopped()
    monkeypatch.setattr(ai.Caller, "_wait", wait)
    real = ai._request
    monkeypatch.setattr(ai, "_request", lambda base, key, path, body=None, timeout=180, **kw: real(base, key, path, body, timeout=min(timeout, 0.5), **kw))
    return waits
