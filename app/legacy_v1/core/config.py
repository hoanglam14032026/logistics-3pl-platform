"""
Cấu hình v1.1.0, đọc từ biến môi trường (có giá trị mặc định).

Bước 0.2 thay bằng app/core/config.py (pydantic-settings, đặc tả 06 §6).
"""

import os
from pathlib import Path

# Chuỗi kết nối CSDL. Mặc định: PostgreSQL 17 của docker-compose (CSDL dev).
DATABASE_URL: str = os.getenv(
    "DATABASE_URL",
    "postgresql+psycopg://logistics:logistics@localhost:5432/logistics_3pl_dev",
)

# Thư mục gốc lưu file upload (tài liệu đào tạo, video, thumbnail).
UPLOAD_DIR: Path = Path(os.getenv("UPLOAD_DIR", "uploads"))
