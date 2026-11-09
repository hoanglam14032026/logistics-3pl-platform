"""
Chuyển đổi file chứng từ trước OCR (Giai đoạn 9) – chạy trong tác vụ nền, đọc file từ kho `shared/files`.

| Loại | Kết quả |
|---|---|
| pdf | Mỗi trang: lớp chữ của PDF nếu đủ (PDF số – không OCR); thiếu thì ảnh trang (`DOCAI_PDF_DPI`) để OCR |
| docx | Văn bản (đoạn, bảng, header/footer) + ảnh nhúng `word/media/*` (OCR từng ảnh) |
| xlsx | Văn bản từng sheet (giá trị đã tính, cách nhau tab) |
| jpg, png, tiff, webp | Ảnh chuẩn hóa: xoay theo EXIF, RGB/xám, cạnh ≤ 4000 px, PNG; TIFF nhiều trang → từng trang |
| txt | Văn bản UTF-8 |

Giới hạn: `DOCAI_MAX_PAGES` trang/ảnh (vượt → xử lý phần đầu + cảnh báo); ảnh ≤ 80 megapixel (chống ảnh bom);
docx/xlsx: tổng dung lượng giải nén ≤ 200 MB (chống zip bom). Lỗi định dạng → `ConversionError` (không thử lại).
"""

import io
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError

from app.shared.files import sniff

MAX_PIXELS = 80_000_000
MAX_SIDE = 4000
MAX_UNZIPPED = 200 * 1024 * 1024
MIN_PDF_TEXT_CHARS = 30
MAX_SHEET_ROWS = 5000
MIN_EMBEDDED_IMAGE_SIDE = 64

Image.MAX_IMAGE_PIXELS = MAX_PIXELS


class ConversionError(Exception):
    """File không chuyển đổi được (hỏng, mã hóa, quá lớn) – lỗi cố định, không thử lại."""


@dataclass
class Page:
    number: int
    source: str  # pdf_text | pdf_image | image | docx_text | docx_image | xlsx_sheet | txt
    label: str | None = None
    text: str | None = None
    image: bytes | None = None  # PNG
    needs_ocr: bool = False


@dataclass
class Converted:
    pages: list[Page] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def kind_of(mime: str) -> str:
    """Nhóm khả năng của model cần có: pdf | image | docx (docx, xlsx, txt)."""
    if mime == sniff.PDF:
        return "pdf"
    if mime in (sniff.JPEG, sniff.PNG, sniff.TIFF, sniff.WEBP):
        return "image"
    return "docx"


def _png(img: Image.Image) -> bytes:
    img = ImageOps.exif_transpose(img)
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGBA").convert("RGB") if "A" in img.mode or img.mode == "P" else img.convert("RGB")
    if max(img.size) > MAX_SIDE:
        img.thumbnail((MAX_SIDE, MAX_SIDE))
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=False)
    return buf.getvalue()


def _image_frames(data: bytes | Path, limit: int) -> tuple[list[bytes], int]:
    try:
        with Image.open(data if isinstance(data, Path) else io.BytesIO(data)) as img:
            total = getattr(img, "n_frames", 1)
            if img.format == "WEBP":
                total = 1  # WEBP động: chỉ khung đầu
            frames = []
            for i in range(min(total, limit)):
                img.seek(i)
                frames.append(_png(img.copy()))
            return frames, total
    except Image.DecompressionBombError as exc:
        raise ConversionError(f"Ảnh quá lớn (> {MAX_PIXELS // 1_000_000} megapixel)") from exc
    except (UnidentifiedImageError, OSError, SyntaxError) as exc:
        raise ConversionError(f"Không đọc được ảnh ({type(exc).__name__})") from exc


def _check_zip(path: Path) -> None:
    try:
        with zipfile.ZipFile(path) as zf:
            total = sum(i.file_size for i in zf.infolist())
    except zipfile.BadZipFile as exc:
        raise ConversionError("File Office hỏng") from exc
    if total > MAX_UNZIPPED:
        raise ConversionError("File Office giải nén quá lớn (> 200 MB)")


def _pdf(path: Path, max_pages: int, dpi: int, want_images: int) -> Converted:
    import pypdfium2 as pdfium

    out = Converted()
    try:
        pdf = pdfium.PdfDocument(path)
    except pdfium.PdfiumError as exc:
        raise ConversionError(f"Không mở được PDF (hỏng hoặc có mật khẩu): {exc}") from exc
    try:
        total = len(pdf)
        if total == 0:
            raise ConversionError("PDF không có trang nào")
        if total > max_pages:
            out.warnings.append(f"PDF có {total} trang – chỉ xử lý {max_pages} trang đầu (DOCAI_MAX_PAGES)")
        for i in range(min(total, max_pages)):
            page = pdf[i]
            try:
                textpage = page.get_textpage()
                text = textpage.get_text_bounded().strip()
                textpage.close()
                has_text = len(text) >= MIN_PDF_TEXT_CHARS
                image = None
                if not has_text or i < want_images:
                    bitmap = page.render(scale=dpi / 72)
                    image = _png(bitmap.to_pil())
                    bitmap.close()
                out.pages.append(
                    Page(
                        number=i + 1,
                        source="pdf_text" if has_text else "pdf_image",
                        text=text if has_text else None,
                        image=image,
                        needs_ocr=not has_text,
                    )
                )
            finally:
                page.close()
    finally:
        pdf.close()
    return out


def _docx(path: Path, max_pages: int) -> Converted:
    import docx

    _check_zip(path)
    try:
        document = docx.Document(str(path))
    except Exception as exc:  # python-docx ném nhiều loại lỗi khác nhau khi file hỏng
        raise ConversionError(f"Không đọc được docx ({type(exc).__name__})") from exc
    lines: list[str] = []
    for section in document.sections:
        lines.extend(p.text for p in section.header.paragraphs if p.text.strip())
    lines.extend(p.text for p in document.paragraphs if p.text.strip())
    for table in document.tables:
        for row in table.rows:
            cells = []
            for cell in row.cells:
                value = cell.text.strip()
                if not cells or cells[-1] != value:  # ô gộp lặp lại cùng nội dung
                    cells.append(value)
            if any(cells):
                lines.append(" | ".join(cells))
    for section in document.sections:
        lines.extend(p.text for p in section.footer.paragraphs if p.text.strip())
    out = Converted(pages=[Page(number=1, source="docx_text", text="\n".join(lines))])
    with zipfile.ZipFile(path) as zf:
        media = sorted(n for n in zf.namelist() if n.startswith("word/media/"))
        for name in media:
            if len(out.pages) >= max_pages:
                out.warnings.append(f"docx có {len(media)} ảnh – chỉ OCR {max_pages - 1} ảnh đầu")
                break
            data = zf.read(name)
            try:
                frames, _ = _image_frames(data, 1)
            except ConversionError:
                continue  # emf/wmf/svg… không phải ảnh raster – bỏ qua
            with Image.open(io.BytesIO(frames[0])) as img:
                if min(img.size) < MIN_EMBEDDED_IMAGE_SIDE:
                    continue  # biểu tượng, logo nhỏ
            out.pages.append(
                Page(number=len(out.pages) + 1, source="docx_image", label=name, image=frames[0], needs_ocr=True)
            )
    return out


def _xlsx(path: Path, max_pages: int) -> Converted:
    import openpyxl

    _check_zip(path)
    try:
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    except Exception as exc:
        raise ConversionError(f"Không đọc được xlsx ({type(exc).__name__})") from exc
    out = Converted()
    try:
        sheets = wb.worksheets
        if len(sheets) > max_pages:
            out.warnings.append(f"xlsx có {len(sheets)} sheet – chỉ đọc {max_pages} sheet đầu")
        for ws in sheets[:max_pages]:
            rows = []
            for n, row in enumerate(ws.iter_rows(values_only=True)):
                if n >= MAX_SHEET_ROWS:
                    out.warnings.append(f"Sheet '{ws.title}': chỉ đọc {MAX_SHEET_ROWS} dòng đầu")
                    break
                cells = ["" if v is None else str(v) for v in row]
                while cells and not cells[-1]:
                    cells.pop()
                if cells:
                    rows.append("\t".join(cells))
            out.pages.append(Page(number=len(out.pages) + 1, source="xlsx_sheet", label=ws.title, text="\n".join(rows)))
    finally:
        wb.close()
    return out


def convert(path: Path, mime: str, max_pages: int, dpi: int = 200, want_images: int = 0) -> Converted:
    """`want_images`: số trang đầu cần ảnh kể cả khi đã có chữ (model `vision` xem ảnh)."""
    if mime == sniff.PDF:
        return _pdf(path, max_pages, dpi, want_images)
    if mime in (sniff.JPEG, sniff.PNG, sniff.TIFF, sniff.WEBP):
        frames, total = _image_frames(path, max_pages)
        out = Converted(
            pages=[Page(number=i + 1, source="image", image=f, needs_ocr=True) for i, f in enumerate(frames)]
        )
        if total > max_pages:
            out.warnings.append(f"Ảnh có {total} trang – chỉ xử lý {max_pages} trang đầu")
        return out
    if mime == sniff.DOCX:
        return _docx(path, max_pages)
    if mime == sniff.XLSX:
        return _xlsx(path, max_pages)
    if mime == sniff.TXT:
        return Converted(pages=[Page(number=1, source="txt", text=path.read_text(encoding="utf-8-sig"))])
    raise ConversionError(f"Chưa hỗ trợ chuyển đổi loại {mime}")
