#!/usr/bin/env bash
# Setup for macOS / Linux
set -e
cd "$(dirname "$0")"
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt
echo "Setup finished. Run:"
echo "  .venv/bin/python retouch_photos.py <photo folder>"
echo "  .venv/bin/python cut_video.py <video> --start 0:30 --end 2:15"
