#!/bin/sh
# Regenerate requirements.txt (the bot's exact dependency versions, with hashes) and
# requirements-test.txt (test tools) from pyproject.toml and requirements-test.in.
# Existing pins are kept unless you ask for an upgrade:
#   scripts/lock.sh                            after editing pyproject.toml
#   scripts/lock.sh --upgrade-package yt-dlp   update just yt-dlp (e.g. when YouTube breaks it)
#   scripts/lock.sh --upgrade                  update everything within the allowed ranges
# Needs uv (https://docs.astral.sh/uv/). Server owners don't need it; they install with pip.
set -e
cd "$(dirname "$0")/.."
# Resolved for every OS, for the oldest supported Python and up, so one file works everywhere.
common="--universal --generate-hashes --python-version 3.12 --quiet"
uv pip compile pyproject.toml $common -o requirements.txt "$@"
uv pip compile requirements-test.in $common --constraint requirements.txt -o requirements-test.txt "$@"
echo "requirements.txt and requirements-test.txt are up to date."
