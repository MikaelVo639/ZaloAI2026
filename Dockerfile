# Submission image: grammar + rewrite model (configs/rewrite-v8.toml) on an RTX 5090 host.
# Qwen2.5-Coder-7B-Instruct weights are not in Git: download them into
# models/qwen2.5-coder-7b-instruct (scripts/prepare_agent_model.py) BEFORE building.
# CUDA 12.8 build of PyTorch 2.7.1, as requirements-model.txt pins (Blackwell needs >= 12.8).
FROM pytorch/pytorch:2.7.1-cuda12.8-cudnn9-runtime
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/code/src HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 \
    CONFIG_PATH=configs/rewrite-v8.toml
WORKDIR /code
COPY requirements.txt ./
# torch comes with the base image; the rest of requirements-model.txt, same versions.
RUN python -m pip install --no-cache-dir -r requirements.txt \
      transformers==4.51.3 huggingface-hub==0.31.4 safetensors==0.5.3
COPY pyproject.toml README.md predict.sh ./
COPY src/ ./src/
COPY configs/ ./configs/
COPY scripts/ ./scripts/
COPY docs/ ./docs/
COPY models/ ./models/
COPY training_code/ ./training_code/
# Refuse to build an image without the model it is configured to load.
RUN test -f /code/models/qwen2.5-coder-7b-instruct/model_manifest.json \
    || (echo "Missing model weights: run scripts/prepare_agent_model.py first" >&2 && exit 1)
RUN chmod +x /code/predict.sh && mkdir -p /result /tmp/datamate
ENTRYPOINT []
CMD ["bash", "/code/predict.sh"]
