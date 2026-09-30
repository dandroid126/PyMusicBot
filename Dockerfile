FROM python:3.14-slim AS base

# FFmpeg decodes and encodes audio. Deno, which yt-dlp needs for YouTube, comes from
# requirements.txt.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# uv installs requirements.txt like pip does, only faster.
COPY --from=ghcr.io/astral-sh/uv:0.11 /uv /usr/local/bin/uv
ENV UV_NO_CACHE=1

WORKDIR /app

# Dependencies get their own layer, so code changes don't reinstall them.
COPY requirements.txt ./
RUN uv pip install --system --require-hashes -r requirements.txt

COPY pyproject.toml README.md ./
COPY src ./src
RUN uv pip install --system --no-deps .


FROM base AS test
COPY requirements-test.txt ./
RUN uv pip install --system --require-hashes -r requirements-test.txt
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
