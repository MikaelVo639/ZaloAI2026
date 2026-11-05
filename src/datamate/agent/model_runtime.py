"""Optional Transformers runtime. Only local, checksummed Safetensors are loaded."""

import hashlib
import json
import os
from pathlib import Path
from typing import Any

MODEL_ID = "Qwen/Qwen2.5-Coder-7B-Instruct"
MODEL_REVISION = "c03e6d358207e414f1eca0bb1891e29f1db0e242"
MODEL_PARAMETERS = 7_610_000_000  # total including embeddings, as published by Qwen


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def verify_snapshot(path: Path) -> dict[str, Any]:
    path = path.resolve(strict=True)
    if not path.is_dir():
        raise ValueError("Model path must be a local directory")
    manifest = json.loads((path / "model_manifest.json").read_text(encoding="utf-8"))
    expected = {"model_id": MODEL_ID, "revision": MODEL_REVISION,
                "total_parameters": MODEL_PARAMETERS, "license": "Apache-2.0"}
    if any(manifest.get(key) != value for key, value in expected.items()):
        raise ValueError("Model declaration differs from the approved prototype")
    files = manifest.get("files")
    if not isinstance(files, dict) or not files:
        raise ValueError("Model manifest has no file checksums")
    required = {"config.json", "tokenizer_config.json", "tokenizer.json", "LICENSE",
                "model.safetensors.index.json"}
    if not required <= files.keys():
        raise ValueError("Model snapshot is incomplete")
    # Refuse unmanifested assets as well: tokenizer/config must be covered, not only weights.
    actual = {p.relative_to(path).as_posix() for p in path.rglob("*") if p.is_file()
              and p.name != "model_manifest.json" and ".cache" not in p.relative_to(path).parts}
    if set(files) != actual:
        raise ValueError("Model file inventory differs from manifest")
    for relative, checksum in files.items():
        item = (path / relative).resolve(strict=True)
        if not item.is_relative_to(path) or not item.is_file():
            raise ValueError("Model file escapes snapshot")
        if sha256(item) != checksum:
            raise ValueError(f"Model checksum mismatch: {relative}")
    index = json.loads((path / "model.safetensors.index.json").read_text(encoding="utf-8"))
    shards = set(index["weight_map"].values())
    if not shards or any(not s.endswith(".safetensors") or s not in files for s in shards):
        raise ValueError("Missing Safetensors shards")
    return manifest


def prepare_model(cache: dict[str, Any], model_path: str) -> dict[str, Any]:
    """Separate load deadline; cache survives business changes in the model worker."""
    path = Path(model_path).resolve(strict=True)
    if cache.get("model_path") == str(path):
        return cache["model_info"]
    manifest = verify_snapshot(path)
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    os.environ["HF_DATASETS_OFFLINE"] = "1"
    os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
    os.environ["CUBLAS_WORKSPACE_CONFIG"] = ":4096:8"
    import torch
    import transformers
    from transformers import AutoModelForCausalLM, AutoTokenizer

    if not torch.cuda.is_available():
        raise ValueError("local_plan requires the prepared CUDA GPU environment")
    torch.manual_seed(0)
    torch.use_deterministic_algorithms(True)
    tokenizer = AutoTokenizer.from_pretrained(str(path), local_files_only=True,
                                              trust_remote_code=False)
    model = AutoModelForCausalLM.from_pretrained(
        str(path), local_files_only=True, trust_remote_code=False, use_safetensors=True,
        torch_dtype=torch.bfloat16, attn_implementation="eager")
    actual_parameters = sum(parameter.numel() for parameter in model.parameters())
    if not 7_600_000_000 <= actual_parameters <= 7_620_000_000:
        raise ValueError("Loaded model parameter count differs from the declared 7.61B model")
    model = model.to("cuda:0").eval()
    cache.update(model_path=str(path), tokenizer=tokenizer, model=model)
    info = {"model_id": MODEL_ID, "revision": MODEL_REVISION,
            "manifest_sha256": sha256(path / "model_manifest.json"),
            "total_parameters": manifest["total_parameters"],
            "actual_parameters": actual_parameters,
            "torch": torch.__version__, "transformers": transformers.__version__,
            "cuda": torch.version.cuda, "gpu": torch.cuda.get_device_name(0)}
    cache["model_info"] = info
    return info


def generate_plan(cache: dict[str, Any], messages: list[dict[str, str]],
                  max_input_tokens: int, max_new_tokens: int) -> dict[str, Any]:
    import torch

    tokenizer, model = cache["tokenizer"], cache["model"]
    text = tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
    inputs = tokenizer([text], return_tensors="pt")
    input_tokens = inputs["input_ids"].shape[1]
    if input_tokens > max_input_tokens:
        raise ValueError("Prompt exceeds token budget; no rule/schema truncation is allowed")
    inputs = inputs.to(model.device)
    with torch.inference_mode():
        output = model.generate(**inputs, max_new_tokens=max_new_tokens, do_sample=False,
                                num_beams=1, pad_token_id=tokenizer.eos_token_id)
    generated = output[0, input_tokens:]
    if len(generated) >= max_new_tokens:
        raise ValueError("Model output reached token limit")
    return {"text": tokenizer.decode(generated, skip_special_tokens=True),
            "input_tokens": input_tokens, "output_tokens": len(generated)}
