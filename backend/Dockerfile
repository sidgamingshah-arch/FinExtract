# The backend with Docling and its models BAKED IN, so the image reads scanned pages with no network
# access at run time. Build where the network is available; run anywhere.
#
#   docker build -t finex-backend ./backend
#   docker run -p 8000:8000 -v finex-data:/app/data finex-backend
#
# Nothing is downloaded when the container runs: the models are in /app/models/docling, which
# config.toml's [ocr] docling_models_dir points at, and the Hugging Face client is set offline.
FROM python:3.11-slim

# libgl / glib: OpenCV and the image stack Docling's OCR uses.
RUN apt-get update \
 && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml README.md ./
COPY app ./app
COPY scripts ./scripts
COPY config.toml ./

# CPU-only PyTorch first (the default wheel carries GPU libraries this app never uses).
RUN pip install --no-cache-dir --index-url https://download.pytorch.org/whl/cpu torch torchvision \
 && pip install --no-cache-dir -e ".[pdf,docling]"

# The models, fetched once at build time.
RUN python scripts/fetch_docling_models.py --dir /app/models/docling

ENV HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1 \
    DOCLING_ARTIFACTS_PATH=/app/models/docling \
    FINEX_DATABASE_URL=sqlite:////app/data/finex.db \
    FINEX_OBJECT_STORE_ROOT=/app/data/object_store
RUN mkdir -p /app/data

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
