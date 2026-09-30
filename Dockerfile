FROM python:3.14-slim AS base

# Deno is the JavaScript runtime yt-dlp needs for YouTube; FFmpeg decodes and encodes audio.
COPY --from=denoland/deno:bin /deno /usr/local/bin/deno
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# uv installs the exact versions pinned in uv.lock.
COPY --from=ghcr.io/astral-sh/uv:0.11 /uv /usr/local/bin/uv
ENV UV_NO_CACHE=1

WORKDIR /app

# Dependencies get their own layer, so code changes don't reinstall them.
COPY pyproject.toml uv.lock ./
RUN uv export --locked --no-emit-project --no-dev > /tmp/requirements.txt \
    && uv pip install --system --require-hashes -r /tmp/requirements.txt

COPY README.md ./
COPY src ./src
RUN uv pip install --system --no-deps .


FROM base AS test
RUN uv export --locked --no-emit-project --no-dev --extra test > /tmp/requirements-test.txt \
    && uv pip install --system --require-hashes -r /tmp/requirements-test.txt
COPY config.example.toml ./
COPY tests ./tests
CMD ["pytest", "-q"]


FROM base AS runtime
RUN useradd --create-home --uid 1000 bot \
    && mkdir -p /config /data /music \
    && chown bot:bot /data
USER bot
ENV PYMUSICBOT_CONFIG=/config/config.toml \
    PYMUSICBOT_DATA=/data \
    PYMUSICBOT_MUSIC=/music \
    PYTHONUNBUFFERED=1
VOLUME ["/data"]
CMD ["python", "-m", "pymusicbot"]
