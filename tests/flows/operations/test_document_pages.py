"""Giai đoạn 23 – xem chứng từ theo trang (nửa trái màn hình nhập tay) và biểu mẫu nhập: không cần CSDL."""

import io

import pytest
from PIL import Image

from app.flows.operations import contract_document_service as entry_service
from app.flows.operations import document_pages as pages
from app.shared.files import sniff

PNG_MAGIC = b"\x89PNG"


def _img(color: str) -> Image.Image:
    return Image.new("RGB", (300, 200), color)


def _save(tmp_path, name: str, **kwargs) -> tuple:
    path = tmp_path / name
    return path, kwargs


def test_every_schema_is_loadable_with_required_fields():
    schemas = entry_service.all_schemas()
    assert len(schemas) >= 18
    for schema in schemas.values():
        assert any(f.required for f in schema.fields), schema.code
        assert schema.target is None  # nhập tay: không tạo bản ghi qua docai


def test_catalog_seed_matches_schemas():
    """Mọi giấy tờ trong danh mục seed (migration 0022) có file biểu mẫu cùng mã, và ngược lại."""
    import importlib.util
    from pathlib import Path

    path = next(Path("migrations/versions").glob("0022_*.py"))
    spec = importlib.util.spec_from_file_location("m0022", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    codes = [row[0] for row in module.CATALOG]
    assert len(codes) == len(set(codes))
    assert set(codes) == set(entry_service.all_schemas())
    assert all(row[2] in ("customer", "partner") and row[4] in ("contract", "shipment") for row in module.CATALOG)


def test_png_single_page(tmp_path):
    path = tmp_path / "a.png"
    _img("red").save(path)
    infos = pages.describe(path, sniff.PNG)
    assert [(p.number, p.kind) for p in infos] == [(1, "image")]
    assert pages.image_of(path, sniff.PNG, 1).startswith(PNG_MAGIC)
    with pytest.raises(pages.PageError) as exc:
        pages.image_of(path, sniff.PNG, 2)
    assert exc.value.status == 404
    with pytest.raises(pages.PageError):
        pages.text_of(path, sniff.PNG, 1)  # trang ảnh không có /text


def test_multipage_tiff_can_flip_pages(tmp_path):
    path = tmp_path / "a.tiff"
    _img("red").save(path, save_all=True, append_images=[_img("green"), _img("blue")])
    infos = pages.describe(path, sniff.TIFF)
    assert [p.number for p in infos] == [1, 2, 3]
    colors = []
    for n in (1, 2, 3):
        with Image.open(io.BytesIO(pages.image_of(path, sniff.TIFF, n))) as im:
            colors.append(im.convert("RGB").getpixel((10, 10)))
    assert len(set(colors)) == 3  # ba trang khác nhau


def test_pdf_pages_rendered_one_by_one(tmp_path):
    path = tmp_path / "a.pdf"
    _img("red").save(path, save_all=True, append_images=[_img("green")])
    assert [p.kind for p in pages.describe(path, sniff.PDF)] == ["image", "image"]
    assert pages.image_of(path, sniff.PDF, 2).startswith(PNG_MAGIC)
    with pytest.raises(pages.PageError) as exc:
        pages.image_of(path, sniff.PDF, 3)
    assert exc.value.status == 404


def test_broken_pdf_is_a_clean_error(tmp_path):
    path = tmp_path / "bad.pdf"
    path.write_bytes(b"%PDF-1.4 broken")
    with pytest.raises(pages.PageError):
        pages.describe(path, sniff.PDF)


def test_docx_text_page_then_embedded_images(tmp_path):
    import docx

    img_path = tmp_path / "logo.png"
    _img("blue").save(img_path)
    document = docx.Document()
    document.add_paragraph("Số B/L: ABC123")
    document.add_picture(str(img_path))
    path = tmp_path / "a.docx"
    document.save(path)
    infos = pages.describe(path, sniff.DOCX)
    assert [p.kind for p in infos] == ["text", "image"]
    assert "ABC123" in pages.text_of(path, sniff.DOCX, 1)
    assert pages.image_of(path, sniff.DOCX, 2).startswith(PNG_MAGIC)


def test_txt_page(tmp_path):
    path = tmp_path / "a.txt"
    path.write_text("Số hóa đơn: INV-1\n", encoding="utf-8")
    assert [(p.number, p.kind) for p in pages.describe(path, sniff.TXT)] == [(1, "text")]
    assert "INV-1" in pages.text_of(path, sniff.TXT, 1)


def test_clean_values_validates_types_and_unknown_fields():
    schema = entry_service.get_schema("commercial_invoice")
    ok = entry_service.clean_values(
        schema, {"invoice_no": "  INV  01 ", "total_amount": "1250.50", "invoice_date": "2026-11-12", "currency": ""}
    )
    assert ok == {"invoice_no": "INV 01", "total_amount": "1250.50", "invoice_date": "2026-11-12", "currency": None}
    for bad in ({"total_amount": "abc"}, {"invoice_date": "12/13/2026"}, {"khong_co": "x"}):
        with pytest.raises(Exception) as exc:
            entry_service.clean_values(schema, bad)
        assert getattr(exc.value, "status_code", None) == 422, bad


def test_missing_required():
    schema = entry_service.get_schema("commercial_invoice")
    assert set(entry_service.missing_required(schema, {"invoice_no": "X"})) == {
        "invoice_date",
        "seller_name",
        "buyer_name",
        "currency",
        "total_amount",
    }
