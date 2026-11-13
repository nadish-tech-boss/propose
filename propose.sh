#!/usr/bin/env sh
# Share the proposal page with a temporary Cloudflare link and watch it live.
# Usage:  ./propose.sh            (public trycloudflare.com link)
#         ./propose.sh --local    (this computer only)
cd "$(dirname "$0")" || exit 1
if command -v python3 >/dev/null 2>&1; then
  exec python3 propose.py "$@"
elif command -v python >/dev/null 2>&1; then
  exec python propose.py "$@"
fi
echo
echo "  Python 3 is needed:  sudo apt install python3   (or your distro's package manager)"
echo
exit 1
