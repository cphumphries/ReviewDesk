"""ReviewDesk names each import and each search-log entry with a random eight-digit number (project._unique_id, which
skips any number already used among the files this computer can see). Two files added by one person on one day on two
computers can still draw the same number, about once in 90 million pairs; Remove duplicates then refuses, naming both
files (test_go_live_fx_order). Before 1.3.1 the number had four digits, and one export's records were then left out
without a word.

So that a test never meets that refusal by chance, the tests draw those numbers without repeats (in this program, and
in each ReviewDesk the tests start as another computer). Nothing else is changed."""
from __future__ import annotations

import itertools
import random
import threading

_LOCK = threading.Lock()
_ORIGINAL = random.SystemRandom.randint
RANGE = (10_000_000, 99_999_999)            # project.ID_DIGITS


def apply() -> None:
    if getattr(random.SystemRandom.randint, "_unique", False):
        return
    lo, hi = RANGE
    start = random.Random().randrange(hi - lo + 1)
    counter = itertools.count()

    def randint(self, a, b):
        if (a, b) == RANGE:
            with _LOCK:
                return lo + (start + next(counter)) % (hi - lo + 1)
        return _ORIGINAL(self, a, b)
    randint._unique = True
    random.SystemRandom.randint = randint


def original():
    return _ORIGINAL
