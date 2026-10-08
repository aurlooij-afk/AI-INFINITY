FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONHASHSEED=random \
    OMP_NUM_THREADS=1 \
    OPENBLAS_NUM_THREADS=1 \
    MKL_NUM_THREADS=1 \
    NUMEXPR_NUM_THREADS=1 \
    VECLIB_NUM_THREADS=1 \
    BLIS_NUM_THREADS=1 \
    MALLOC_ARENA_MAX=2 \
    AI_INFINITY_DATA_DIR=/data/ai-infinity \
    AI_INFINITY_3601_BACKGROUND=true \
    AI_INFINITY_FAST_MODE=0 \
    AI_INFINITY_MAX_HEAVY_JOBS=1 \
    AI_INFINITY_FFMPEG_THREADS=1 \
    AI_INFINITY_FFMPEG_FILTER_THREADS=1 \
    AI_INFINITY_MEMORY_GUARD_PERCENT=0.75 \
    AI_INFINITY_MEMORY_RESERVE_MB=128 \
    AI_INFINITY_PERSISTENCE_MODE=volatile \
    AI_INFINITY_PERSISTENT_VOLUME_CONFIRMED=false \
    AI_INFINITY_PROJECT_STATE_DURABLE=false \
    AI_INFINITY_DIVINE_CLOSURE=true \
    AI_INFINITY_PRODUCTION_GRAPH_WORKER=true \
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

COPY entrypoint.sh sitecustomize.py main.py studio_ultimate.py studio_os.py content_factory.py free_api_fabric.py infinity_empire.py creator_os_3624.py ai_infinity_bridge.py creator_studio_2030.html creator_entrypoint.py creator_pro_os.py overlay_final_3700.py runtime_main_3700.py production_graph.py production_intelligence.py production_openai_video.py production_closure_3624.py professional_creator_v2.py professional_creator_v2_timeline_patch.py ./
COPY production_hardening.py creator_final_3800.py ui_3800.html divine_closure_3900.py reality_first_3901.py ai_infinity_canonical.py ai_infinity_app.py professional_creator_fabric.py capability_registry.json ./
COPY ai3701_features.py ai3702_platform.py ai3703_patch.py ai3704_storage_fabric.py ai3705_closure.py ai3706_internal_closure.py ai_infinity ./

RUN python -m py_compile \
    sitecustomize.py main.py studio_ultimate.py studio_os.py content_factory.py free_api_fabric.py \
    infinity_empire.py creator_os_3624.py ai_infinity_bridge.py creator_entrypoint.py creator_pro_os.py \
    overlay_final_3700.py runtime_main_3700.py production_graph.py production_intelligence.py \
    production_hardening.py creator_final_3800.py production_closure_3624.py professional_creator_v2.py professional_creator_v2_timeline_patch.py professional_creator_fabric.py \
    divine_closure_3900.py reality_first_3901.py ai_infinity_canonical.py ai_infinity_app.py \
    ai3701_features.py ai3702_platform.py ai3703_patch.py ai3704_storage_fabric.py ai3705_closure.py ai3706_internal_closure.py

RUN chmod 755 /app/entrypoint.sh \
    && mkdir -p /data/ai-infinity /tmp/ai-infinity \
    && chown -R aiinfinity:aiinfinity /app /data/ai-infinity /tmp/ai-infinity
USER aiinfinity
EXPOSE 10000
VOLUME ["/data"]
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD curl -fsS "http://127.0.0.1:${PORT:-10000}/health" >/dev/null || exit 1
ENTRYPOINT ["/bin/sh","/app/entrypoint.sh"]
CMD uvicorn main:app --host 0.0.0.0 --port ${PORT:-10000}