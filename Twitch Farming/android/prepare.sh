#!/usr/bin/env bash
set -euo pipefail
cp ../backend.py ./backend.py
cp ../i18n.py ./i18n.py
cp ../version_info.py ./version_info.py
rm -rf engine locales
cp -R ../engine ./engine
cp -R ../locales ./locales
python -m compileall main.py service.py backend.py i18n.py engine
