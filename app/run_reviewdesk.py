"""Start ReviewDesk (double-click the 'Start ReviewDesk' file next to this folder rather than this one)."""
import os
import sys

# The version is checked before anything else is read: ReviewDesk's own files use Python 3.9 and cannot even be read
# by an older Python, which would then show a long error instead of this. (This file must stay readable by Python 2.7
# and 3.0 to 3.8 for that reason: no f-strings, no annotations.)
if sys.version_info < (3, 9):
    sys.stderr.write("ReviewDesk needs Python 3.9 or later; this is Python %s.\n" % sys.version.split()[0])
    sys.exit(1)

sys.dont_write_bytecode = True          # no __pycache__ folders: this folder may sit in a synced (OneDrive) folder
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from reviewdesk.main import main  # noqa: E402

sys.exit(main())
