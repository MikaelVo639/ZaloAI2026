"""Online IMAGE PREPARATION only. Never imported/called by inference."""

import argparse
import json
from pathlib import Path

from datamate.agent.model_runtime import (
    MODEL_ID, MODEL_PARAMETERS, MODEL_REVISION, sha256, verify_snapshot,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    destination = args.output.resolve()
    if destination.exists():
        raise ValueError("Refusing to overwrite an existing snapshot; choose a new directory")
    from huggingface_hub import snapshot_download

    snapshot_download(repo_id=MODEL_ID, revision=MODEL_REVISION, local_dir=str(destination),
                      allow_patterns=["*.json", "*.txt", "*.safetensors", "LICENSE", "README.md"])
    files = {p.relative_to(destination).as_posix(): sha256(p)
             for p in sorted(destination.rglob("*")) if p.is_file()
             and ".cache" not in p.relative_to(destination).parts}
    manifest = {"model_id": MODEL_ID, "revision": MODEL_REVISION,
                "weights_release_date": "2024-09-19", "license": "Apache-2.0",
                "total_parameters": MODEL_PARAMETERS, "format": "BF16 Safetensors",
                "files": files}
    (destination / "model_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    verify_snapshot(destination)
    print(json.dumps({"model_id": MODEL_ID, "revision": MODEL_REVISION,
                      "files": len(files), "status": "verified"}))


if __name__ == "__main__":
    main()
