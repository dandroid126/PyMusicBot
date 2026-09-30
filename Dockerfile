FROM python:3.14-slim AS base

# Deno is the JavaScript runtime yt-dlp needs for YouTube; FFmpeg decodes and encodes audio.
COPY --from=denoland/deno:bin /deno /usr/local/bin/deno
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install dependencies in their own layer so code changes don't reinstall them.
COPY pyproject.toml ./
RUN python -c "import tomllib; print('\n'.join(tomllib.load(open('pyproject.toml', 'rb'))['project']['dependencies']))" \
        > /tmp/requirements.txt \
    && pip install --no-cache-dir --root-user-action=ignore -r /tmp/requirements.txt

COPY README.md ./
COPY src ./src
RUN pip install --no-cache-dir --root-user-action=ignore --no-deps .


FROM base AS test
RUN pip install --no-cache-dir --root-user-action=ignore pytest
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
