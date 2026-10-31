# Server SEMPAT untuk layanan cloud (Render, Railway, Fly.io, Google Cloud Run, dsb.)
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
ENV SEMPAT_DATA_DIR=/data \
    PYTHONUNBUFFERED=1
RUN mkdir -p /data && python -c "import sys; sys.argv=['run.py']; from run import ensure_synthetic; ensure_synthetic()"
EXPOSE 8000
CMD ["python", "run.py", "--host", "0.0.0.0", "--no-browser"]
