"""
Cấu hình v1.1.0 – từ bước 0.2 lấy từ app/core/config.py (pydantic-settings, đặc tả 06 §6).

Giữ hai tên DATABASE_URL, UPLOAD_DIR để mã v1 không phải sửa.
"""

from pathlib import Path

from app.core.config import get_settings

_settings = get_settings()

# Chuỗi kết nối CSDL. Mặc định: PostgreSQL 17 của docker-compose (CSDL dev).
DATABASE_URL: str = _settings.database_url

# Thư mục gốc lưu file upload (tài liệu đào tạo, video, thumbnail).
UPLOAD_DIR: Path = _settings.upload_dir
