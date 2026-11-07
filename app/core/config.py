"""
Cấu hình ứng dụng, đọc từ biến môi trường (có giá trị mặc định).
"""

import os
from pathlib import Path

# Chuỗi kết nối CSDL. Mặc định: file SQLite tại thư mục chạy lệnh.
DATABASE_URL: str = os.getenv("DATABASE_URL", "sqlite:///./business_guide.db")

# Thư mục gốc lưu file upload (tài liệu đào tạo, video, thumbnail).
UPLOAD_DIR: Path = Path(os.getenv("UPLOAD_DIR", "uploads"))
