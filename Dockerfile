# Minimal production image for Hostless Workers and personal Linux servers.
FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    TZ=Asia/Tehran

WORKDIR /app

# ffmpeg supports media downloads; shared libraries are required by OpenCV/NudeNet.
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      ca-certificates tzdata ffmpeg libgl1 libglib2.0-0 libsm6 libxext6 libxrender1 \
 && ln -snf /usr/share/zoneinfo/Asia/Tehran /etc/localtime \
 && echo "Asia/Tehran" > /etc/timezone \
 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./requirements.txt
RUN python -m pip install --no-cache-dir -r requirements.txt

RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin armando \
 && mkdir -p /app/data /app/logs \
 && chown -R armando:armando /app

# Only runtime code and its bundled static JSON assets enter the image.
COPY --chown=armando:armando app/ ./app/

USER armando:armando
STOPSIGNAL SIGTERM
CMD ["python", "-m", "app.main"]
