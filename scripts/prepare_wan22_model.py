from __future__ import annotations

"""Download and cryptographically manifest the official Wan2.2-TI2V-5B model.

The worker will not load a model directory unless this script has verified the
official Hugging Face model card license at an immutable repository revision and
recorded SHA-256 hashes for the required model files.
"""

import argparse
import hashlib
import json
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

from huggingface_hub import HfApi, hf_hub_download, snapshot_download


MODEL_ID = "Wan-AI/Wan2.2-TI2V-5B"
LICENSE_ID = "apache-2.0"
MODEL_CARD_URL = "https://huggingface.co/Wan-AI/Wan2.2-TI2V-5B"
LICENSE_URL = "https://www.apache.org/licenses/LICENSE-2.0"
MANIFEST_NAME = ".ai-infinity-model-manifest.json"
REQUIRED_FILES = (
    "config.json",
    "diffusion_pytorch_model.safetensors.index.json",
    "diffusion_pytorch_model-00001-of-00003.safetensors",
    "diffusion_pytorch_model-00002-of-00003.safetensors",
    "diffusion_pytorch_model-00003-of-00003.safetensors",
    "models_t5_umt5-xxl-enc-bf16.pth",
    "Wan2.2_VAE.pth",
    "README.md",
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description="Prepare the verified Wan2.2-TI2V-5B model for AI Infinity")
    parser.add_argument("--model-dir", required=True, type=Path)
    parser.add_argument("--minimum-free-gib", type=float, default=55.0)
    args = parser.parse_args()

    model_dir = args.model_dir.expanduser().resolve()
    model_dir.mkdir(parents=True, exist_ok=True)
    free_gib = shutil.disk_usage(model_dir).free / (1024 ** 3)
    if free_gib < args.minimum_free_gib:
        raise RuntimeError(
            f"Only {free_gib:.1f} GiB free; at least {args.minimum_free_gib:.1f} GiB is required "
            "for the public model snapshot and verified worker state."
        )

    api = HfApi()
    info = api.model_info(MODEL_ID, files_metadata=True)
    revision = str(getattr(info, "sha", "") or "")
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise RuntimeError("Hugging Face did not return an immutable model commit SHA")

    card_path = Path(hf_hub_download(
        repo_id=MODEL_ID,
        filename="README.md",
        revision=revision,
        local_dir=str(model_dir),
    ))
    card_text = card_path.read_text(encoding="utf-8", errors="replace")
    card_license_match = re.search(r"(?m)^license:\s*([a-zA-Z0-9.-]+)\s*$", card_text)
    card_license = card_license_match.group(1).lower() if card_license_match else ""
    card_data = getattr(info, "card_data", None)
    metadata_license = str(getattr(card_data, "license", "") or "").lower()
    if metadata_license and metadata_license != LICENSE_ID:
        raise RuntimeError(f"Hugging Face model metadata license is {metadata_license!r}, not Apache-2.0")
    if card_license != LICENSE_ID:
        raise RuntimeError(f"Downloaded model card declares {card_license!r}, not Apache-2.0")
    if "License Agreement" not in card_text or "Apache 2.0 License" not in card_text:
        raise RuntimeError("Model card's license agreement text is missing; refusing to prepare an unverified model")

    snapshot_download(
        repo_id=MODEL_ID,
        revision=revision,
        local_dir=str(model_dir),
    )

    files = []
    required_missing = [rel for rel in REQUIRED_FILES if not (model_dir / rel).is_file()]
    if required_missing:
        raise RuntimeError("Required Wan2.2 model files are missing: " + ", ".join(required_missing))
    # Hash every regular file in the actual pinned snapshot, not only the main
    # weights. This includes tokenizer/config assets used by the upstream
    # inference runtime and prevents an unverified sidecar from being swapped.
    for rel in REQUIRED_FILES:
        candidate = model_dir / rel
        if candidate.is_symlink() or not candidate.is_file():
            raise RuntimeError(f"Required Wan2.2 model file is missing or symlinked: {rel}")
    for path in sorted(model_dir.rglob("*")):
        if path.is_symlink():
            raise RuntimeError(f"Model snapshot contains an unexpected symlink: {path.relative_to(model_dir)}")
        if not path.is_file():
            continue
        rel_path = path.relative_to(model_dir)
        if any(part == ".cache" for part in rel_path.parts) or path.name == MANIFEST_NAME:
            continue
        rel = rel_path.as_posix()
        files.append({
            "path": rel,
            "size_bytes": path.stat().st_size,
            "sha256": sha256_file(path),
        })

    manifest = {
        "schema": "ai-infinity-open-model-manifest-v1",
        "model_id": MODEL_ID,
        "revision": revision,
        "license": LICENSE_ID,
        "license_url": LICENSE_URL,
        "model_card_url": MODEL_CARD_URL,
        "model_card_sha256": hashlib.sha256(card_text.encode("utf-8")).hexdigest(),
        "license_evidence": {
            "source": "Hugging Face model metadata and immutable model-card snapshot",
            "metadata_license": metadata_license or None,
            "card_license": card_license,
            "agreement_section_present": True,
        },
        "usage_terms": (
            "The publisher states Apache-2.0 and separately requires compliance with applicable law "
            "and the model-card restrictions on harmful, privacy-invasive, or misleading use. "
            "The deploying operator must read and acknowledge the complete model card."
        ),
        "verified_at": datetime.now(timezone.utc).isoformat(),
        "files": files,
    }
    target = model_dir / MANIFEST_NAME
    temporary = target.with_suffix(".tmp")
    temporary.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(target)
    print(json.dumps({
        "status": "verified",
        "model_id": MODEL_ID,
        "revision": revision,
        "license": LICENSE_ID,
        "files_verified": len(files),
        "manifest": str(target),
        "truthful": True,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"WAN MODEL PREPARATION FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(1)
