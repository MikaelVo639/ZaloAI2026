# Deterministic CPU baseline. A model backend needs an RTX 5090-compatible base.
FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/code/src HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
WORKDIR /code
COPY requirements.txt ./
RUN python -m pip install --no-cache-dir -r requirements.txt
COPY pyproject.toml README.md predict.sh ./
COPY src/ ./src/
COPY configs/ ./configs/
COPY scripts/ ./scripts/
COPY docs/ ./docs/
COPY models/ ./models/
COPY training_code/ ./training_code/
RUN chmod +x /code/predict.sh && mkdir -p /result /tmp/datamate
ENTRYPOINT []
CMD ["bash", "/code/predict.sh"]
