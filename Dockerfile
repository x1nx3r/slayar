# CPU-only image. Model weights are NOT baked in — mount them at /models/laya
# from a named volume / PVC (see docker-compose.yml, k8s/).
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    USE_TF=0 \
    MODEL_BASE=/models/laya \
    DEVICE=cpu \
    MAX_LOADED=1
# LAYA_API_KEY is intentionally NOT defaulted here (secret hygiene) — inject
# at runtime via compose / k8s secret. Unset = open dev mode.

WORKDIR /app

COPY requirements-cpu.txt .
RUN pip install --no-cache-dir -r requirements-cpu.txt

COPY server.py router.py ./

EXPOSE 8000
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8000"]
