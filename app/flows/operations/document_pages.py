"""
Xem chứng từ theo trang (Giai đoạn 23) – nửa **bên trái** của màn hình nhập tay chứng từ lô.

Chỉ chuyển file thành trang để đọc, không OCR / AI:

| Loại file | Trang |
|---|---|
| pdf | mỗi trang PDF → ảnh PNG (render theo yêu cầu, từng trang) |
| jpg, png, tiff, webp | mỗi khung ảnh (TIFF nhiều trang) → ảnh PNG |
| docx | trang 1 = văn bản (đoạn, bảng, đầu / chân trang); các trang sau = ảnh nhúng trong file |
| xlsx | mỗi sheet = một trang văn bản |
| txt | một trang văn bản |

Tái dùng `app/shared/docai/convert.py` (đã có chống ảnh bom, zip bom, giới hạn số trang `DOCAI_MAX_PAGES`).
"""

from dataclasses import dataclass
from pathlib import Path

from app.core.config import get_settings
from app.shared.docai import convert
from app.shared.files import sniff

PDF_VIEW_DPI = 130  # đủ rõ để đọc chữ trên màn hình, nhẹ hơn DPI dùng cho OCR


class PageError(Exception):
    def __init__(self, message: str, status: int = 422) -> None:
        super().__init__(message)
        self.status = status


@dataclass
class PageInfo:
    number: int
    kind: str  # image | text
    label: str | None = None


def _pdf_count(path: Path) -> int:
    import pypdfium2 as pdfium

    try:
        pdf = pdfium.PdfDocument(path)
    except pdfium.PdfiumError as exc:
        raise PageError(f"Không mở được PDF (hỏng hoặc có mật khẩu): {exc}") from exc
    try:
        return len(pdf)
    finally:
        pdf.close()


def _pdf_page(path: Path, number: int) -> bytes:
    import pypdfium2 as pdfium

    try:
        pdf = pdfium.PdfDocument(path)
    except pdfium.PdfiumError as exc:
        raise PageError(f"Không mở được PDF: {exc}") from exc
    try:
        page = pdf[number - 1]
        try:
            bitmap = page.render(scale=PDF_VIEW_DPI / 72)
            try:
                return convert._png(bitmap.to_pil())
            finally:
                bitmap.close()
        finally:
            page.close()
    finally:
        pdf.close()


def _converted(path: Path, mime: str) -> convert.Converted:
    try:
        return convert.convert(path, mime, get_settings().docai_max_pages)
    except convert.ConversionError as exc:
        raise PageError(str(exc)) from exc


def describe(path: Path, mime: str) -> list[PageInfo]:
    limit = get_settings().docai_max_pages
    if mime == sniff.PDF:
        return [PageInfo(i + 1, "image") for i in range(min(_pdf_count(path), limit))]
    return [
        PageInfo(p.number, "image" if p.image is not None else "text", p.label) for p in _converted(path, mime).pages
    ]


def image_of(path: Path, mime: str, number: int) -> bytes:
    if number < 1:
        raise PageError("Số trang phải ≥ 1", 404)
    if mime == sniff.PDF:
        if number > min(_pdf_count(path), get_settings().docai_max_pages):
            raise PageError("Không có trang này", 404)
        return _pdf_page(path, number)
    for page in _converted(path, mime).pages:
        if page.number == number:
            if page.image is None:
                raise PageError("Trang này là văn bản – dùng /text", 404)
            return page.image
    raise PageError("Không có trang này", 404)


def text_of(path: Path, mime: str, number: int) -> str:
    for page in _converted(path, mime).pages:
        if page.number == number:
            if page.image is not None:
                raise PageError("Trang này là ảnh – dùng /image", 404)
            return page.text or ""
    raise PageError("Không có trang này", 404)
