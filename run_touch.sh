#!/bin/bash
# Fullscreen touchscreen version of the scanner (big buttons).
cd "$(dirname "$0")"
[ -d venv ] && source venv/bin/activate
exec python app.py --touch "$@"
