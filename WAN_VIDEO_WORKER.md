# AI Infinity — Open-Source Wan Video Worker

This is an optional, separate GPU service. It does **not** run inside the lightweight
AI Infinity Render web service. Creator Studio submits a short shot request to this
worker only when the worker proves that its hardware, model, licence, runtime and
persistent storage are ready.

## Model and actual compute floor

The initial adapter is the official **Wan-AI/Wan2.2-TI2V-5B** text/image-to-video
model. Its Hugging Face model card declares Apache-2.0 and its upstream inference
guide specifies a single GPU with at least 24 GB VRAM for 720p TI2V-5B. The upstream
guide reports a five-second 720p inference in under nine minutes on a consumer GPU
under its stated test configuration. That is not a guaranteed service-level time.

- Model card: https://huggingface.co/Wan-AI/Wan2.2-TI2V-5B
- Official code: https://github.com/Wan-Video/Wan2.2
- Licence: https://www.apache.org/licenses/LICENSE-2.0

The model card also includes usage conditions in its License Agreement section.
The operator must review those conditions. A manifest saying Apache-2.0 is not
permission to ignore the rest of the model card or applicable law.

The current worker deliberately supports short native shots only: 2–5 seconds,
24fps, 1280x704 landscape or 704x1280 portrait (720p-class model output). AI Infinity
then mixes the clip with narration/music/SFX in its existing FFmpeg timeline. It
does not claim Wan produces a finished 15-minute film in one inference.

## Prerequisites

Use an independently hosted NVIDIA GPU server, not the Creator's phone or local
browser device. It must have:

- A compatible NVIDIA driver and CUDA-enabled PyTorch installation.
- At least 24 GiB total VRAM and at least 22,000 MiB free before an inference starts.
- The official Wan2.2 code repository and its dependencies installed.
- At least 45 GiB free for model snapshot preparation.
- A persistent worker volume with at least 10 GiB free for job state, logs and outputs.
- A private network or HTTPS reverse proxy between AI Infinity and this worker.

A 24-GB card is the upstream minimum, not a promise that every driver, display
process or competing workload leaves enough free VRAM. The worker checks free
memory immediately before each job and rejects work when resources are insufficient.

## Install the model runtime

Use a Python environment compatible with the official Wan2.2 requirements (Python
3.10+ and a CUDA-enabled PyTorch build). Install the upstream Wan dependencies using
the current official repository instructions. Do not install CPU-only PyTorch and
then describe the worker as ready.

~~~sh
git clone https://github.com/Wan-Video/Wan2.2.git /opt/Wan2.2
cd /opt/Wan2.2
pip install -r requirements.txt
~~~

Install the small AI Infinity worker service requirements in that same environment:

~~~sh
pip install -r wan_worker_requirements.txt
~~~

Download and verify the immutable official model snapshot (the preparer checks the
Hugging Face model metadata, model-card licence statement, immutable revision, and
SHA-256 hashes of all required files):

~~~sh
python scripts/prepare_wan22_model.py --model-dir /models/Wan2.2-TI2V-5B
~~~

The snapshot is large. Do not run this on an ephemeral filesystem or in the
Creator Studio container.

## Configure and run the worker

Mount a durable volume at /data/wan-worker; keep the model weights on a separate
persistent path. Set these environment variables in the worker's secret/config store:

~~~sh
WAN_SOURCE_DIR=/opt/Wan2.2
WAN_MODEL_DIR=/models/Wan2.2-TI2V-5B
WAN_WORKER_DATA_DIR=/data/wan-worker
WAN_WORKER_PERSISTENT_STORAGE_CONFIRMED=true
AI_INFINITY_WAN_WORKER_TOKEN=<long-random-secret>
AI_INFINITY_WAN_MODEL_TERMS_ACKNOWLEDGED=true
WAN_WORKER_MIN_TOTAL_VRAM_MB=24576
WAN_WORKER_MIN_FREE_VRAM_MB=22000
WAN_WORKER_MAX_RUNTIME_SECONDS=1800
~~~

WAN_WORKER_PERSISTENT_STORAGE_CONFIRMED=true is an operator attestation that
WAN_WORKER_DATA_DIR is a durable mounted volume. Do not set it for /tmp, a
container writable layer, or an ephemeral deployment disk.

Start one process and one Uvicorn worker only. Do not scale this service horizontally
against a single GPU volume/database:

~~~sh
uvicorn wan_video_worker:app --host 0.0.0.0 --port 8765 --workers 1
~~~

Place it behind HTTPS or a private authenticated service network. Do not expose its
unauthenticated internal job/artifact routes to the public internet. The service
requires Authorization: Bearer <AI_INFINITY_WAN_WORKER_TOKEN> for all /v1/*
endpoints. /health returns only coarse liveness.

## Connect the existing AI Infinity application

Set these application environment variables. The worker URL must use HTTPS except
for a loopback-only local development target:

~~~sh
AI_INFINITY_WAN_WORKER_URL=https://<private-worker-host>
AI_INFINITY_WAN_WORKER_TOKEN=<same-long-random-secret>
~~~

The existing Creator Studio project page will then show the Wan option only after a
live /v1/capabilities probe returns execution_ready=true, with the pinned model,
Apache-2.0 evidence, sufficient free VRAM, FFmpeg/FFprobe and persistent storage
confirmed. Merely setting a URL or token is not readiness.

No GPU is provisioned by this repository or by the free Render web service. Until an
independently hosted GPU worker is configured and passes those live checks, the
application must report native open-source video generation as unavailable. The
existing free-first footage/voice/FFmpeg rendering path remains separate and usable.

## Job lifecycle, limits and recovery

- One GPU inference at a time; bounded durable queue.
- Maximum 2–5 second output, 24fps, fixed 720p-class model sizes, 1 GiB default
  artifact ceiling, and 30-minute maximum inference execution.
- Wan frame count is rounded to the upstream-required 4n+1 shape.
- Requests use an idempotency key. Repeating a request after an application restart
  returns the original worker job instead of creating another duplicate run.
- The worker stores job state in SQLite WAL mode and outputs/logs on the persistent
  worker volume. On restart it verifies any completed-but-uncommitted output or
  requeues an interrupted inference.
- Every output is checked by FFprobe and full FFmpeg decode before it becomes
  downloadable. The app downloads the bytes, verifies the SHA-256, and sends the
  candidate through the existing project asset storage path.
- Candidate selection remains blocked unless AI Infinity's object store records a
  verified durable object, byte size and SHA-256. Worker-local persistence alone is
  not treated as project-level durable storage.
- Technical QA does not count as creative approval. Generated-media rights remain
  REVIEW_REQUIRED; the owner retains creative and publication control.

## API surface

- GET /health — coarse liveness only.
- GET /v1/capabilities — authenticated live model, licence, GPU and persistence
  readiness.
- POST /v1/jobs — authenticated, idempotent generation enqueue.
- GET /v1/jobs/{job_id} — durable job state and output evidence.
- GET /v1/jobs/{job_id}/artifact — authenticated MP4 download with SHA-256 and
  size rechecked before every transfer.
