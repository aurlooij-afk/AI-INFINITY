FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    AI_INFINITY_DATA_DIR=/tmp/ai-infinity \
    AI_INFINITY_OWNER_EMAIL=aurlooijlooij@gmail.com \
    PORT=10000

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        ffmpeg \
        espeak-ng \
        libsndfile1 \
        libglib2.0-0 \
        libsm6 \
        libxext6 \
        libxrender1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install application dependencies first for Docker-layer caching.
COPY requirements.txt bridge-requirements.txt ./

RUN python -m pip install --upgrade pip \
    && python -m pip install -r requirements.txt \
    && if [ -s bridge-requirements.txt ]; then \
         python -m pip install -r bridge-requirements.txt; \
       fi

# Complete AI Infinity runtime source.
COPY main.py \
     studio_ultimate.py \
     studio_os.py \
     content_factory.py \
     free_api_fabric.py \
     infinity_empire.py \
     creator_os_3624.py \
     ai_infinity_bridge.py \
     creator_studio_2030.html \
     creator_entrypoint.py \
     ./

# Fail the image build immediately if a required Python source is broken.
RUN python -m py_compile \
        main.py \
        studio_ultimate.py \
        studio_os.py \
        content_factory.py \
        free_api_fabric.py \
        infinity_empire.py \
        creator_os_3624.py \
        ai_infinity_bridge.py \
        creator_entrypoint.py

RUN mkdir -p /tmp/ai-infinity

EXPOSE 10000

# Render supplies PORT; 10000 remains the local/default port.
# The entrypoint preserves the existing foundation app and mounts the complete
# Creator Studio backend on /studio and /infinity/studio.
CMD ["sh", "-c", "exec uvicorn creator_entrypoint:application --host 0.0.0.0 --port ${PORT:-10000}"]
