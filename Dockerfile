# Image chạy (mặc định, stage cuối `runtime`): docker compose build / docker build .
# Image kiểm thử (Giai đoạn 7): docker build --target test -t logistics-3pl-platform:test .
#   – cùng tầng phụ thuộc chạy và mã nguồn với image chạy, thêm công cụ dev + tests/ để chạy pytest trong container.

FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UPLOAD_DIR=/app/data/uploads

WORKDIR /app

# OCR cục bộ (Giai đoạn 9 – AI local trích xuất chứng từ): Tesseract + dữ liệu tiếng Việt, tiếng Anh.
RUN apt-get update \
    && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-vie tesseract-ocr-eng \
    && rm -rf /var/lib/apt/lists/*

COPY requirements-lock.txt .
RUN pip install --no-cache-dir -r requirements-lock.txt

COPY app ./app
# Ứng dụng tự chạy alembic upgrade head khi khởi động (lifespan); alembic.ini cho lệnh CLI trong container.
COPY alembic.ini ./
COPY migrations ./migrations

RUN mkdir -p /app/data/uploads


FROM base AS test

COPY requirements-dev-lock.txt pyproject.toml ./
RUN pip install --no-cache-dir -r requirements-dev-lock.txt
COPY tests ./tests
CMD ["pytest"]


FROM base AS runtime

VOLUME ["/app/data/uploads"]

EXPOSE 8000

# Worker hàng đợi dùng cùng image: command ["python", "-m", "app.worker"] (docker-compose.yml)
CMD ["uvicorn", "app.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
