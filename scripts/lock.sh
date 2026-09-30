#!/bin/sh
# Re-resolve dependencies and regenerate the requirements files from uv.lock.
#   scripts/lock.sh              keep current versions, just sync the files
#   scripts/lock.sh --upgrade    move everything to the newest allowed versions
#   scripts/lock.sh --upgrade-package yt-dlp   update only yt-dlp
set -e
cd "$(dirname "$0")/.."
uv lock "$@"
uv export --quiet --format requirements-txt --no-emit-project --no-dev -o requirements.txt
uv export --quiet --format requirements-txt --no-emit-project --no-dev --extra test -o requirements-test.txt
echo "uv.lock, requirements.txt and requirements-test.txt are up to date."
