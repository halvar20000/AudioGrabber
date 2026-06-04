#!/bin/sh
# Audio Grabber launcher for Linux — requires python3 and ffmpeg
DIR="$(cd "$(dirname "$0")" && pwd)"
exec python3 "$DIR/Audio Grabber.app/Contents/Resources/server.py"
