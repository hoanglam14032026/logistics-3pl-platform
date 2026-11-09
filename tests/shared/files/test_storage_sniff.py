"""Kho lưu trữ, nhận dạng loại file (06 §2.6) – test đơn vị, không cần CSDL."""

import hashlib
import io
import zipfile

import pytest

from app.shared.files import sniff
from app.shared.files.service import safe_filename
from app.shared.files.storage import LocalStorage, TooLarge
from tests.conftest import pdf_bytes


def _zip(names: list[str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for n in names:
            zf.writestr(n, "x")
    return buf.getvalue()


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        (pdf_bytes(), sniff.PDF),
        (b"\xff\xd8\xff\xe0" + b"0" * 20, sniff.JPEG),
        (b"\x89PNG\r\n\x1a\n" + b"0" * 20, sniff.PNG),
        (_zip(["[Content_Types].xml", "word/document.xml"]), sniff.DOCX),
        (_zip(["[Content_Types].xml", "xl/workbook.xml"]), sniff.XLSX),
        (_zip(["[Content_Types].xml", "ppt/presentation.xml"]), sniff.PPTX),
        (_zip(["a.txt"]), None),  # zip thường, không phải Office
        (b"PK\x03\x04hong", None),  # zip hỏng
        (b"MZ\x90\x00 tep thuc thi", None),
        (b"<html><script>alert(1)</script>", None),
        # Giai đoạn 9: txt nhận dạng khi toàn bộ là UTF-8 không có byte điều khiển, không mở đầu như HTML/XML/SVG
        (b"Day la van ban", sniff.TXT),
        ("Hóa đơn số 01\r\n\tTổng: 1.500.000 đ".encode(), sniff.TXT),
        (b"\xef\xbb\xbf<!DOCTYPE html><p>x</p>", None),  # HTML có BOM đổi đuôi .txt
        (b"  <svg onload=alert(1)>", None),
        (b"<?xml version='1.0'?><a/>", None),
        (b"\x7fELF\x02\x01\x01", None),  # file thực thi Linux
        (b"van ban \xff\xfe hong", None),  # không phải UTF-8
        (b"II*\x00" + b"0" * 20, sniff.TIFF),
        (b"MM\x00*" + b"0" * 20, sniff.TIFF),
        (b"RIFF\x10\x00\x00\x00WEBPVP8 " + b"0" * 10, sniff.WEBP),
        (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"0" * 20, sniff.OLE2),  # doc/xls cũ
    ],
)
def test_sniff_by_content(tmp_path, content, expected):
    path = tmp_path / "f"
    path.write_bytes(content)
    assert sniff.sniff(content[:8192], path) == expected


def test_save_streams_and_hashes(tmp_path):
    storage = LocalStorage(tmp_path)
    data = b"%PDF-" + b"a" * (3 * 1024 * 1024)  # > 1 khúc ghi
    blob = storage.save("ops", io.BytesIO(data), max_bytes=10 * 1024 * 1024)
    assert blob.size_bytes == len(data) and blob.sha256 == hashlib.sha256(data).hexdigest()
    assert blob.head.startswith(b"%PDF-") and len(blob.head) == 8192
    parts = blob.storage_key.split("/")
    assert parts[0] == "ops" and len(parts) == 4 and len(parts[3]) == 36  # ops/yyyy/mm/uuid4
    assert storage.path(blob.storage_key).read_bytes() == data


def test_too_large_leaves_nothing(tmp_path):
    storage = LocalStorage(tmp_path)
    with pytest.raises(TooLarge):
        storage.save("ops", io.BytesIO(b"x" * 2000), max_bytes=1000)
    assert [p for p in tmp_path.rglob("*") if p.is_file()] == []  # không còn file tạm .part


@pytest.mark.parametrize("key", ["../ngoai", "/etc/passwd", "ops/../../ngoai"])
def test_storage_key_cannot_escape_root(tmp_path, key):
    with pytest.raises(ValueError):
        LocalStorage(tmp_path / "goc").path(key)


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Hợp đồng HD-2026.pdf", "Hợp đồng HD-2026.pdf"),
        ("../../etc/passwd", "passwd"),
        ("C:\\Users\\a\\bao gia.xlsx", "bao gia.xlsx"),
        ("ten\x00\x1fla.pdf", "tenla.pdf"),
        ("", "file"),
        (None, "file"),
        ("..", "file"),
        ("a" * 300 + ".pdf", "a" * 255),
    ],
)
def test_safe_filename(name, expected):
    assert safe_filename(name) == expected
