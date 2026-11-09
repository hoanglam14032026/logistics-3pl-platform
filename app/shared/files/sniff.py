"""
Nhận dạng loại file theo nội dung (magic bytes) – 06 §2.6: không tin phần mở rộng, không tin Content-Type của client.

Đủ cho các loại chứng từ/hợp đồng hiện dùng (pdf, jpg, png, docx, xlsx) và chứng từ đưa vào AI trích xuất
(Giai đoạn 9: thêm tiff, webp, txt; nhận ra doc/xls cũ dạng OLE2 để từ chối có tên rõ). Thêm loại (video, audio…) khi
làm module cần tới – hoặc chuyển sang thư viện `filetype` (07 §2) khi danh sách lớn hơn.

`txt` không có magic bytes: chỉ nhận khi **toàn bộ** file là UTF-8 hợp lệ, không có byte điều khiển (trừ tab, xuống
dòng, sang trang) và không mở đầu như HTML/XML/SVG/script – file thực thi (`MZ`, ELF…) hay trang HTML đổi đuôi `.txt`
đều bị từ chối.
"""

import codecs
import re
import zipfile
from pathlib import Path

PDF = "application/pdf"
JPEG = "image/jpeg"
PNG = "image/png"
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
PPTX = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
TIFF = "image/tiff"
WEBP = "image/webp"
TXT = "text/plain"
OLE2 = "application/x-ole-storage"  # doc/xls/ppt cũ (Office 97–2003) – nhận ra để từ chối, chưa chuyển đổi được

EXTENSIONS = {
    PDF: "pdf",
    JPEG: "jpg",
    PNG: "png",
    DOCX: "docx",
    XLSX: "xlsx",
    PPTX: "pptx",
    TIFF: "tiff",
    WEBP: "webp",
    TXT: "txt",
    OLE2: "doc",
}

_OOXML_MARKERS = {"word/document.xml": DOCX, "xl/workbook.xml": XLSX, "ppt/presentation.xml": PPTX}


_MARKUP = re.compile(rb"^\s*<(!doctype|html|head|body|script|\?xml|svg|iframe|meta|object|embed)\b", re.IGNORECASE)
_CONTROL = re.compile(rb"[\x00-\x08\x0b\x0e-\x1f\x7f]")


def _is_plain_text(head: bytes, path: Path) -> bool:
    if _MARKUP.match(head.removeprefix(codecs.BOM_UTF8)):
        return False
    decoder = codecs.getincrementaldecoder("utf-8")()
    try:
        with open(path, "rb") as f:
            while chunk := f.read(1024 * 1024):
                if _CONTROL.search(chunk):
                    return False
                decoder.decode(chunk)
            decoder.decode(b"", final=True)
    except (UnicodeDecodeError, OSError):
        return False
    return True


def sniff(head: bytes, path: Path) -> str | None:
    """Trả MIME nhận dạng được, hoặc None nếu không thuộc loại đã biết."""
    if head.startswith((b"II*\x00", b"MM\x00*")):
        return TIFF
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return WEBP
    if head.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):
        return OLE2
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
    if head and _is_plain_text(head, path):
        return TXT
    return None
