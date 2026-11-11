#!/bin/sh
# Start ReviewDesk on Linux: sh start-reviewdesk.sh   (add --no-browser or --port 8123 if you need them)
# Works from the download (the "app" folder is beside this file), from ReviewDesk's source code (this file is in the
# "launchers" folder and "app" is one folder up) and through a link to this file made with ln -s.
me=$0
while [ -L "$me" ]; do                      # a link to this file: find the real file, and so the app folder beside it
  to=$(readlink "$me")
  case $to in
    /*) me=$to ;;
    *) me=$(dirname "$me")/$to ;;
  esac
done
cd "$(dirname "$me")" || exit 1
export PYTHONDONTWRITEBYTECODE=1

if [ -f app/run_reviewdesk.py ]; then
  entry=app/run_reviewdesk.py
elif [ -f ../app/run_reviewdesk.py ]; then
  entry=../app/run_reviewdesk.py
else
  echo "ReviewDesk cannot start: its \"app\" folder is not next to this file (in $(pwd))."
  echo "Extract the whole zip file you downloaded again, and run start-reviewdesk.sh from the folder it makes."
  exit 1
fi

found=""
for py in python3 python3.15 python3.14 python3.13 python3.12 python3.11 python3.10 python3.9 python; do
  command -v "$py" >/dev/null 2>&1 || continue
  if "$py" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' >/dev/null 2>&1; then
    exec "$py" "$entry" "$@"
  fi
  v=$("$py" -c 'import sys; print("%d.%d.%d" % sys.version_info[:3])' 2>/dev/null)
  [ -n "$v" ] && found="$found
  Python $v (the command $py)"
done
if [ -n "$found" ]; then
  echo "ReviewDesk needs Python 3.9 or later. The Python found on this computer is too old:$found"
else
  echo "ReviewDesk needs Python 3.9 or later, and none was found on this computer."
fi
echo "Install Python 3.9 or later (for example from your system's software centre or package manager) and try again."
exit 1
