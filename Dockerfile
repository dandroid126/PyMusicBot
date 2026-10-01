# uv installs requirements.txt like pip does, only faster. It's mounted only while installing,
# so it isn't part of the image.
FROM ghcr.io/astral-sh/uv:0.11 AS uv

FROM python:3.14-slim AS base

# FFmpeg decodes and encodes audio. Deno, which yt-dlp needs for YouTube, comes from
# requirements.txt. upgrade: every build gets Debian's security fixes released since the base
# image was made (CI rebuilds weekly for this).
# FFmpeg's video output devices depend on Mesa's graphics drivers, which pull in LLVM and z3
# (about 190 MB). They're removed: ffmpeg and ffprobe don't link to them, and the GL library
# loads them only to draw graphics, which an audio bot never does. The names are Debian 13's.
RUN apt-get update \
    && apt-get upgrade -y \
    && apt-get install -y --no-install-recommends ffmpeg \
    && dpkg --remove --force-depends libllvm19 mesa-libgallium libz3-4 \
    && rm -rf /var/lib/apt/lists/*
# Bytecode is compiled at build time, because the container's files are read-only at runtime.
ENV UV_NO_CACHE=1 \
    UV_COMPILE_BYTECODE=1

WORKDIR /app

# Dependencies get their own layer, so code changes don't reinstall them.
COPY requirements.txt ./
RUN --mount=from=uv,source=/uv,target=/usr/local/bin/uv uv pip install --system --require-hashes -r requirements.txt

COPY pyproject.toml README.md ./
COPY src ./src
RUN --mount=from=uv,source=/uv,target=/usr/local/bin/uv uv pip install --system --no-deps .


FROM base AS test
COPY requirements-test.txt ./
RUN --mount=from=uv,source=/uv,target=/usr/local/bin/uv uv pip install --system --require-hashes -r requirements-test.txt
COPY config.example.toml ./
COPY tests ./tests
CMD ["pytest", "-q"]


FROM base AS runtime
RUN useradd --create-home --uid 1000 bot \
    && mkdir -p /config /data /music \
    && chown bot:bot /data
USER bot
# yt-dlp and Deno keep their caches under XDG_CACHE_HOME, in /tmp: compose.yaml makes
# everything else read-only.
ENV PYMUSICBOT_CONFIG=/config/config.toml \
    PYMUSICBOT_DATA=/data \
    PYMUSICBOT_MUSIC=/music \
    PYTHONUNBUFFERED=1 \
    XDG_CACHE_HOME=/tmp/cache
VOLUME ["/data"]
CMD ["python", "-m", "pymusicbot"]
