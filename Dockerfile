FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UPLOAD_DIR=/app/data/uploads

WORKDIR /app

COPY requirements-lock.txt .
RUN pip install --no-cache-dir -r requirements-lock.txt

COPY app ./app
# Ứng dụng tự chạy alembic upgrade head khi khởi động (lifespan); alembic.ini cho lệnh CLI trong container.
COPY alembic.ini ./
COPY migrations ./migrations

RUN mkdir -p /app/data/uploads
VOLUME ["/app/data/uploads"]

EXPOSE 8000

CMD ["uvicorn", "app.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
