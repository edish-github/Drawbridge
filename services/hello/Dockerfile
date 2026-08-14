FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py ./

# Cloud Run supplies PORT. Single worker: this service holds no state and does no work.
CMD exec uvicorn main:app --host 0.0.0.0 --port ${PORT:-8080}
