FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    AI_INFINITY_DATA_DIR=/tmp/ai-infinity \
    AI_INFINITY_3601_BACKGROUND=true \
    PORT=10000

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates curl ffmpeg espeak-ng libsndfile1 libglib2.0-0 \
        libsm6 libxext6 libxrender1 poppler-utils \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt bridge-requirements.txt ./
RUN python -m pip install --upgrade pip \
    && python -m pip install -r requirements.txt \
    && if [ -s bridge-requirements.txt ]; then python -m pip install -r bridge-requirements.txt; fi

COPY main.py studio_ultimate.py studio_os.py content_factory.py free_api_fabric.py infinity_empire.py creator_os_3624.py ai_infinity_bridge.py creator_studio_2030.html creator_entrypoint.py creator_pro_os.py ./
COPY entrypoint_3700.py ui_3700.html backend_3700_01.part backend_3700_02.part backend_3700_03.part ./

RUN python -m py_compile main.py studio_ultimate.py studio_os.py content_factory.py free_api_fabric.py infinity_empire.py creator_os_3624.py ai_infinity_bridge.py creator_entrypoint.py creator_pro_os.py entrypoint_3700.py

RUN mkdir -p /tmp/ai-infinity
EXPOSE 10000
CMD ["python","entrypoint_3700.py"]
