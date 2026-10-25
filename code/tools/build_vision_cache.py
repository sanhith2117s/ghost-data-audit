"""Regenerate the screenshot vision-transcription cache.

Usage (from the repository root):

    # Inspect what would change, without making any model calls:
    python code/tools/build_vision_cache.py --dry-run

    # Regenerate the cache. Requires the provider API key in the env var named
    # by config/models.yaml (OPENCODE_API_KEY by default). Images whose SHA-256
    # is already cached are skipped (idempotent).
    OPENCODE_API_KEY=... python code/tools/build_vision_cache.py

The graded pipeline itself makes zero model calls: it reads the cached
transcriptions from code/evidence_cache/images.json. This script is a
development-time tool only.

The cache stores only raw extracted content (resource reference, record type,
displayed state, direct identifiers, timestamp as shown). It never stores case
IDs, case-level decisions, or expected labels.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "code") not in sys.path:
    sys.path.insert(0, str(ROOT / "code"))

MODELS_CONFIG = ROOT / "config" / "models.yaml"
IMAGES_DIR = ROOT / "dataset" / "evidence" / "images"
CACHE_PATH = ROOT / "code" / "evidence_cache" / "images.json"


def load_models_config() -> dict:
    if not MODELS_CONFIG.is_file():
        return {}
    with MODELS_CONFIG.open(encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def content_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_cache() -> dict:
    if CACHE_PATH.is_file():
        with CACHE_PATH.open(encoding="utf-8") as handle:
            return json.load(handle)
    return {"schema_version": 1, "note": [], "entries": {}}


def save_cache(cache: dict) -> None:
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with CACHE_PATH.open("w", encoding="utf-8") as handle:
        json.dump(cache, handle, indent=2, ensure_ascii=False)
        handle.write("\n")


def transcribe_image_live(image_path: Path, config: dict) -> dict | None:
    """Transcribe one screenshot with the configured vision model.

    Returns a list of observation dicts, or None if transcription is not
    available (no API key configured / no router endpoint). The graded pipeline
    never calls this; it is used only by this cache-builder script.
    """
    vision = (config.get("vision") or {})
    api_key_env = vision.get("api_key_env")
    api_key = os.environ.get(api_key_env) if api_key_env else None
    if not api_key:
        print(
            f"  skip {image_path.name}: no API key in env var {api_key_env}; "
            "set it to transcribe live.",
            file=sys.stderr,
        )
        return None
    base_url = vision.get("base_url")
    model = vision.get("model")
    print(
        f"  transcribe {image_path.name} with {vision.get('provider')}/{model}"
        + (f" ({base_url})" if base_url else ""),
        file=sys.stderr,
    )
    # Router integration point: replace with a real call to the configured
    # provider's vision endpoint, passing `api_key`, `model`, `base_url`, and
    # the image bytes. The return value must match the observation schema used
    # below (system_id, resource_id, category, state, observed_at, emails,
    # phones, names, references, pii).
    raise NotImplementedError(
        "no vision router endpoint is wired; implement transcribe_image_live "
        "against config/models.yaml when a provider key is available"
    )


def image_to_entry(image_path: Path, config: dict, dry_run: bool) -> dict | None:
    digest = content_hash(image_path)
    entry = {
        "file_name": image_path.name,
        "observations": [],
    }
    if dry_run:
        print(f"  would transcribe {image_path.name} (hash {digest[:12]}...)")
        return entry
    try:
        observations = transcribe_image_live(image_path, config)
    except NotImplementedError as exc:
        print(f"  skip {image_path.name}: {exc}", file=sys.stderr)
        return None
    if observations is None:
        return None
    entry["observations"] = observations
    return entry


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="list images that would be transcribed; make no model calls",
    )
    args = parser.parse_args()

    config = load_models_config()
    cache = load_cache()
    entries = cache.setdefault("entries", {})

    images = sorted(IMAGES_DIR.glob("*.png")) + sorted(IMAGES_DIR.glob("*.jpg")) + sorted(IMAGES_DIR.glob("*.jpeg"))
    if not images:
        print(f"no images found in {IMAGES_DIR}")
        return 0

    changed = False
    for image_path in images:
        digest = content_hash(image_path)
        if digest in entries:
            print(f"cached  {image_path.name} (hash {digest[:12]}...)")
            continue
        entry = image_to_entry(image_path, config, args.dry_run)
        if entry is not None:
            entries[digest] = entry
            changed = True

    if changed and not args.dry_run:
        save_cache(cache)
        print(f"cache updated: {CACHE_PATH}")
    elif args.dry_run:
        print("dry run complete: no cache written")
    else:
        print("all images already cached; nothing to do")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
