"""
Nhận dạng loại file theo nội dung (magic bytes) – 06 §2.6: không tin phần mở rộng, không tin Content-Type của client.

Đủ cho các loại chứng từ/hợp đồng hiện dùng (pdf, jpg, png, docx, xlsx). Thêm loại (video, audio…) khi làm module cần
tới – hoặc chuyển sang thư viện `filetype` (07 §2) khi danh sách lớn hơn.
"""

import zipfile
from pathlib import Path

PDF = "application/pdf"
JPEG = "image/jpeg"
PNG = "image/png"
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
PPTX = "application/vnd.openxmlformats-officedocument.presentationml.presentation"

EXTENSIONS = {PDF: "pdf", JPEG: "jpg", PNG: "png", DOCX: "docx", XLSX: "xlsx", PPTX: "pptx"}

_OOXML_MARKERS = {"word/document.xml": DOCX, "xl/workbook.xml": XLSX, "ppt/presentation.xml": PPTX}


def sniff(head: bytes, path: Path) -> str | None:
    """Trả MIME nhận dạng được, hoặc None nếu không thuộc loại đã biết."""
    if head.startswith(b"%PDF-"):
        return PDF
    if head.startswith(b"\xff\xd8\xff"):
        return JPEG
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return PNG
    if head.startswith(b"PK\x03\x04"):
        # docx/xlsx/pptx là ZIP: phân biệt theo phần bắt buộc của từng định dạng Office Open XML
        try:
            with zipfile.ZipFile(path) as zf:
                names = set(zf.namelist())
        except (zipfile.BadZipFile, OSError):
            return None
        if "[Content_Types].xml" not in names:
            return None
        for marker, mime in _OOXML_MARKERS.items():
            if marker in names:
                return mime
    return None
