#!/bin/sh
# Run the test suite inside Docker.
set -e
cd "$(dirname "$0")/.."
docker build --quiet --target test -t pymusicbot-test . >/dev/null
docker run --rm pymusicbot-test pytest -q "$@"
