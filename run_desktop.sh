#!/bin/bash
# Desktop window version of the scanner.
cd "$(dirname "$0")"
[ -d venv ] && source venv/bin/activate
exec python app.py "$@"
