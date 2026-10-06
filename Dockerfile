FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONHASHSEED=random \
    OMP_NUM_THREADS=1 \
    OPENBLAS_NUM_THREADS=1 \
    MKL_NUM_THREADS=1 \
    NUMEXPR_NUM_THREADS=1 \
    VECLIB_MAXIMUM_THREADS=1 \
    BLIS_NUM_THREADS=1 \
    MALLOC_ARENA_MAX=2 \
    AI_INFINITY_DATA_DIR=/tmp/ai-infinity \
    AI_INFINITY_3601_BACKGROUND=true \
    AI_INFINITY_MAX_HEAVY_JOBS=1 \
    AI_INFINITY_FFMPEG_THREADS=1 \
    AI_INFINITY_FFMPEG_FILTER_THREADS=1 \
    AI_INFINITY_MEMORY_GUARD_PERCENT=0.75 \
    AI_INFINITY_MEMORY_RESERVE_MB=128 \
    AI_INFINITY_PERSISTENCE_MODE=portable \
    AI_INFINITY_DIVINE_CLOSURE=true \
    PORT=10000 \
    PYTHONPATH=/app

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates curl ffmpeg espeak-ng libsndfile1 libglib2.0-0 \
        libsm6 libxext6 libxrender1 poppler-utils tini \
    && rm -rf /var/lib/apt/lists/*

RUN useradd --system --create-home --uid 10001 --shell /usr/sbin/nologin aiinfinity

WORKDIR /app
COPY requirements.txt bridge-requirements.txt ./
RUN python -m pip install --upgrade pip \
    && python -m pip install -r requirements.txt \
    && if [ -s bridge-requirements.txt ]; then python -m pip install -r bridge-requirements.txt; fi

# main.py is the complete application entrypoint.
COPY sitecustomize.py main.py studio_ultimate.py studio_os.py content_factory.py free_api_fabric.py infinity_empire.py creator_os_3624.py ai_infinity_bridge.py creator_studio_2030.html creator_entrypoint.py creator_pro_os.py overlay_final_3700.py runtime_main_3700.py ./
COPY production_hardening.py creator_final_3800.py ui_3800.html divine_closure_3900.py divine_closure_3900.py ./
COPY ai3701_features.py ai3702_platform.py ai3703_patch.py ai3704_storage_fabric.py ai3705_closure.py ai3706_internal_closure.py ./

RUN python -m py_compile \
    sitecustomize.py main.py studio_ultimate.py studio_os.py content_factory.py free_api_fabric.py \
    infinity_empire.py creator_os_3624.py ai_infinity_bridge.py creator_entrypoint.py \
    creator_pro_os.py overlay_final_3700.py runtime_main_3700.py production_hardening.py creator_final_3800.py \
    ai3701_features.py ai3702_platform.py ai3703_patch.py ai3704_storage_fabric.py \
    ai3705_closure.py ai3706_internal_closure.py

RUN mkdir -p /tmp/ai-infinity \
    && chown -R aiinfinity:aiinfinity /app /tmp/ai-infinity

USER aiinfinity

EXPOSE 10000
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD curl -fsS http://127.0.0.1:${PORT:-10000}/infinity/divine/health >/dev/null || exit 1

ENTRYPOINT ["/usr/bin/tini","--"]
CMD ["sh","-c","exec uvicorn main:app --host 0.0.0.0 --port ${PORT:-10000}"]
