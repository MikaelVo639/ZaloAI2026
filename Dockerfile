# CPU starter only. Switch to the organizer's CUDA >=12.8 base when adding GPU models.
FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app/src HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
WORKDIR /app
COPY requirements.txt ./
RUN python -m pip install --no-cache-dir -r requirements.txt
COPY pyproject.toml README.md predict.sh ./
COPY src/ ./src/
COPY configs/ ./configs/
COPY scripts/ ./scripts/
COPY docs/ ./docs/
COPY models/ ./models/
RUN chmod +x /app/predict.sh && mkdir -p /result /tmp/datamate
ENTRYPOINT ["/app/predict.sh"]
