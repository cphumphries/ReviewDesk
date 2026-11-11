#!/bin/bash
# Start ReviewDesk on a Mac. Double-click this file (if the Mac says it cannot be opened, see the user guide:
# open Terminal, type "bash " and drag this file into the window, then press Return).
# Works from the download (the "app" folder is beside this file) and from ReviewDesk's source code (this file is in
# the "launchers" folder and "app" is one folder up).
cd "$(dirname "$0")" || exit 1
export PYTHONDONTWRITEBYTECODE=1
if [ -f app/run_reviewdesk.py ]; then
  entry=app/run_reviewdesk.py
elif [ -f ../app/run_reviewdesk.py ]; then
  entry=../app/run_reviewdesk.py
else
  echo
  echo "ReviewDesk cannot start: its \"app\" folder is not next to this file."
  echo "Extract the whole zip file you downloaded again, and double-click this file in the folder it makes."
  echo
  read -n 1 -s -r -p "Press any key to finish."
  exit 1
fi
ok() { "$1" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' >/dev/null 2>&1; }
for py in /Library/Frameworks/Python.framework/Versions/Current/bin/python3 /opt/homebrew/bin/python3 /usr/local/bin/python3 python3; do
  command -v "$py" >/dev/null 2>&1 || continue
  # Apple's /usr/bin/python3 is only a stand-in until the command line developer tools are installed: running it
  # would ask to install them, while this window says to install Python. So it is skipped until they are there.
  if [ "$(uname)" = Darwin ] && [ "$(command -v "$py")" = /usr/bin/python3 ] && ! xcode-select -p >/dev/null 2>&1; then
    continue
  fi
  if ok "$py"; then
    "$py" "$entry" "$@"
    code=$?
    if [ "$code" -eq 3 ]; then          # another version is running: keep the window open so the message can be read
      echo
      read -n 1 -s -r -p "Press any key to finish."
    fi
    exit "$code"
  fi
done
echo
echo "ReviewDesk needs Python 3.9 or later, and none was found on this Mac."
echo "Install Python 3 from https://www.python.org/downloads/macos/ and double-click this file again."
echo
read -n 1 -s -r -p "Press any key to finish."
