#!/bin/sh
# One-time setup: creates a Python virtual environment next to this script
# with the two dependencies the audit needs.
set -e
HERE="$(cd "$(dirname "$0")" && pwd)"
if [ ! -x "$HERE/venv/bin/python" ]; then
  python3 -m venv "$HERE/venv"
fi
"$HERE/venv/bin/pip" install -q --upgrade pip >/dev/null 2>&1 || true
"$HERE/venv/bin/pip" install -q nrbf luaparser
echo "ready: $HERE/venv/bin/python $HERE/qsys_audit.py DESIGN.qsys"
