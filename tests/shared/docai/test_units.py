"""Test đơn vị AI trích xuất (Giai đoạn 9): schema loại chứng từ, chuẩn hóa giá trị, độ tin cậy, chuyển đổi file, OCR.
Không đụng CSDL, không gọi model."""

import io
import json
import struct
import sys
import types
import zlib

import openpyxl
import pytest
from docx import Document
from PIL import Image

from app.shared.docai import convert, extract, ocr, registry
from app.shared.docai.registry import FieldSpec, InvalidValue, normalize
from app.shared.files import sniff


def spec(type_, **kw) -> FieldSpec:
    return FieldSpec(key="f", label="F", type=type_, **kw)


# --- Schema loại chứng từ ----------------------------------------------------------------------------------------


def test_builtin_schemas_load():
    schemas = registry.all_schemas()
    assert {"party", "service_contract", "shipment", "commercial_invoice"} <= set(schemas)
    assert schemas["party"].target == "party" and schemas["commercial_invoice"].target is None


def test_new_doc_type_by_adding_a_json_file(tmp_path):
    (tmp_path / "packing_list.json").write_text(
        json.dumps(
            {
                "code": "packing_list",
                "version": 1,
                "name": "Phiếu đóng gói",
                "fields": [{"key": "packages", "label": "Số kiện", "type": "integer", "required": True}],
            }
        ),
        encoding="utf-8",
    )
    loaded = registry.load_dir(tmp_path)
    assert loaded["packing_list"].fields[0].type == "integer"


@pytest.mark.parametrize(
    ("content", "message"),
    [
        (
            {"code": "khac_ten", "version": 1, "name": "x", "fields": [{"key": "a", "label": "A", "type": "string"}]},
            "trùng tên file",
        ),
        ({"code": "loai", "version": 1, "name": "x", "fields": [{"key": "a", "label": "A", "type": "money"}]}, "type"),
        ({"code": "loai", "version": 1, "name": "x", "fields": [{"key": "a", "label": "A", "type": "enum"}]}, "values"),
        (
            {
                "code": "loai",
                "version": 1,
                "name": "x",
                "fields": [{"key": "a", "label": "A", "type": "string", "form_field": "a"}],
            },
            "target",
        ),
        (
            {"code": "loai", "version": 1, "name": "x", "fields": [{"key": "a", "label": "A", "type": "string"}] * 2},
            "Trùng",
        ),
    ],
)
def test_broken_schema_file_stops_loading(tmp_path, content, message):
    (tmp_path / "loai.json").write_text(json.dumps(content), encoding="utf-8")
    with pytest.raises(registry.SchemaFileError, match=message):
        registry.load_dir(tmp_path)


# --- Chuẩn hóa giá trị -------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("type_", "raw", "expected"),
    [
        ("date", "05/11/2026", "2026-11-05"),
        ("date", "5-11-2026", "2026-11-05"),
        ("date", "2026-11-05", "2026-11-05"),
        ("datetime", "2026-11-20", "2026-11-20T00:00:00+07:00"),
        ("datetime", "2026-11-20T08:30:00", "2026-11-20T08:30:00+07:00"),
        ("datetime", "2026-11-20T01:00:00Z", "2026-11-20T01:00:00+00:00"),
        ("number", "1.500.000", "1500000"),
        ("number", "1,500,000.50", "1500000.50"),
        ("number", "1.500.000,50", "1500000.50"),
        ("number", "12,5", "12.5"),
        ("number", 18500, "18500"),
        ("integer", "30", 30),
        ("boolean", "Có", True),
        ("boolean", False, False),
        ("string", "  Cty   Minh\nAn ", "Cty Minh An"),
        ("string", "   ", None),
    ],
)
def test_normalize(type_, raw, expected):
    assert normalize(spec(type_), raw) == expected


@pytest.mark.parametrize(
    ("type_", "raw"),
    [
        ("date", "31/02/2026"),
        ("date", "hôm qua"),
        ("number", "abc"),
        ("number", True),
        ("integer", "1.5"),
        ("boolean", "có lẽ"),
        ("number", "NaN"),
    ],
)
def test_normalize_rejects(type_, raw):
    with pytest.raises(InvalidValue):
        normalize(spec(type_), raw)


def test_normalize_enum_and_array():
    assert normalize(spec("enum", values=["CIF", "FOB"]), "cif") == "CIF"
    with pytest.raises(InvalidValue):
        normalize(spec("enum", values=["CIF"]), "DDU")
    items = [
        FieldSpec(key="no", label="Số", type="string", required=True),
        FieldSpec(key="w", label="W", type="number"),
    ]
    arr = spec("array", items=items)
    assert normalize(arr, [{"no": "MSKU1", "w": "1,5"}, {"no": None, "w": None}]) == [{"no": "MSKU1", "w": "1.5"}]
    with pytest.raises(InvalidValue, match="thiếu no"):
        normalize(arr, [{"w": 2}])
    with pytest.raises(InvalidValue, match="trường lạ"):
        normalize(arr, [{"no": "A", "x": 1}])
    assert normalize(spec("string", max_length=3), "abc") == "abc"
    with pytest.raises(InvalidValue):
        normalize(spec("string", max_length=3), "abcd")


# --- Đầu ra model, độ tin cậy ------------------------------------------------------------------------------------


def test_output_schema_and_prompt():
    party = registry.get_schema("party")
    schema = extract.output_schema(party)
    assert schema["required"] == [f.key for f in party.fields]
    assert schema["properties"]["party_type"]["properties"]["value"]["anyOf"][0]["enum"][0] == "customer"
    system, user, truncated = extract.build_prompt(party, "x" * 5000, max_chars=2000)
    assert truncated and user.count("x") == 2000 and "không bịa" in system and "DỮ LIỆU" in system


@pytest.mark.parametrize(
    "raw",
    ['{"a": 1}', '```json\n{"a": 1}\n```', 'Kết quả:\n{"a": 1}\nHết.'],
)
def test_parse_output(raw):
    assert extract.parse_output(raw) == {"a": 1}


@pytest.mark.parametrize("raw", ["không có json", "[1, 2]", "{hỏng"])
def test_parse_output_rejects(raw):
    with pytest.raises(extract.ExtractionError):
        extract.parse_output(raw)


def test_confidence_grounding_and_review_reasons():
    party = registry.get_schema("party")
    source = "CÔNG TY MINH AN, MST 0101234567, Hà Nội. Ngày 05/11/2026"
    data = {
        "name": {"value": "Công ty Minh An", "confidence": 0.99},  # khớp không dấu/hoa thường
        "tax_code": {"value": "0101234567", "confidence": 95},  # phần trăm → 0.95
        "address": {"value": "Đà Nẵng", "confidence": 0.99},  # không có trong văn bản → bị hạ
        "phone": {"value": "0909", "confidence": "cao"},  # confidence không phải số → 0
        "party_type": {"value": "customer", "confidence": 0.95},
        "email": "a@b.vn",  # model trả thẳng giá trị → confidence 0
    }
    fields = extract.build_fields(party, data, source)
    assert fields["name"]["grounded"] is True and fields["name"]["confidence"] == 0.99
    assert fields["tax_code"]["confidence"] == 0.95
    assert fields["address"]["confidence"] == extract.UNGROUNDED_CAP and fields["address"]["issue"]
    assert fields["phone"]["confidence"] == 0.0 and fields["email"]["confidence"] == 0.0
    assert fields["country_code"]["value"] is None and fields["party_type"]["grounded"] is None
    reasons = extract.review_reasons(party, fields, 0.9)
    assert reasons == {"low_confidence": ["address", "phone", "email"], "missing_required": []}
    assert extract.overall(fields) == 0.0


def test_date_and_number_grounding():
    contract = registry.get_schema("service_contract")
    fields = extract.build_fields(
        contract,
        {
            "valid_from": {"value": "2026-11-01", "confidence": 1},
            "valid_to": {"value": "2027-10-31", "confidence": 1},
            "payment_due_days": {"value": 45, "confidence": 1},
        },
        "Hiệu lực từ ngày 01 tháng 11 năm 2026 đến 31-10-2027. Thanh toán 30 ngày.",
    )
    assert fields["valid_from"]["grounded"] and fields["valid_to"]["grounded"]
    assert fields["payment_due_days"]["grounded"] is False  # 45 không có trong văn bản


def test_form_payload_only_mapped_fields():
    shipment = registry.get_schema("shipment")
    values = {"direction": "import", "shipper_name": "A", "etd": None, "origin_location": "CNSHA"}
    assert extract.form_payload(shipment, values) == {"direction": "import", "origin_location": "CNSHA"}


# --- Chuyển đổi trước OCR ----------------------------------------------------------------------------------------


def _text_pdf(text: str) -> bytes:
    """PDF 1 trang có lớp chữ (font Helvetica chuẩn)."""
    stream = f"BT /F1 18 Tf 50 750 Td ({text}) Tj ET".encode()
    objs = [
        b"<</Type/Catalog/Pages 2 0 R>>",
        b"<</Type/Pages/Kids[3 0 R]/Count 1>>",
        b"<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]/Contents 4 0 R/Resources<</Font<</F1 5 0 R>>>>>>",
        b"<</Length %d>>stream\n" % len(stream) + stream + b"\nendstream",
        b"<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>",
    ]
    out, offsets = io.BytesIO(), []
    out.write(b"%PDF-1.4\n")
    for i, body in enumerate(objs, 1):
        offsets.append(out.tell())
        out.write(b"%d 0 obj\n" % i + body + b"\nendobj\n")
    xref = out.tell()
    out.write(b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1))
    for off in offsets:
        out.write(b"%010d 00000 n \n" % off)
    out.write(b"trailer<</Size %d/Root 1 0 R>>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref))
    return out.getvalue()


def test_pdf_text_layer_and_scanned_pages(tmp_path):
    digital = tmp_path / "a.pdf"
    digital.write_bytes(_text_pdf("HOA DON THUONG MAI INV-2026-001 TONG 1250.50 USD"))
    out = convert.convert(digital, sniff.PDF, max_pages=5)
    [page] = out.pages
    assert page.source == "pdf_text" and "INV-2026-001" in page.text and not page.needs_ocr and page.image is None
    out = convert.convert(digital, sniff.PDF, max_pages=5, want_images=1)
    assert out.pages[0].image.startswith(b"\x89PNG")  # model vision: kèm ảnh trang

    scanned = tmp_path / "scan.pdf"
    Image.new("RGB", (600, 800), "white").save(
        scanned, format="PDF", save_all=True, append_images=[Image.new("RGB", (600, 800), "white")] * 2
    )
    out = convert.convert(scanned, sniff.PDF, max_pages=2, dpi=72)
    assert [p.source for p in out.pages] == ["pdf_image", "pdf_image"] and all(p.needs_ocr for p in out.pages)
    assert out.warnings == ["PDF có 3 trang – chỉ xử lý 2 trang đầu (DOCAI_MAX_PAGES)"]


def test_broken_pdf(tmp_path):
    bad = tmp_path / "x.pdf"
    bad.write_bytes(b"%PDF-1.4\nhong")
    with pytest.raises(convert.ConversionError):
        convert.convert(bad, sniff.PDF, max_pages=5)


def test_images_are_normalized(tmp_path):
    tiff = tmp_path / "a.tiff"
    frames = [Image.new("L", (100, 50), 255), Image.new("RGB", (5000, 2000), "white")]
    frames[0].save(tiff, format="TIFF", save_all=True, append_images=frames[1:])
    out = convert.convert(tiff, sniff.TIFF, max_pages=10)
    assert [p.source for p in out.pages] == ["image", "image"]
    with Image.open(io.BytesIO(out.pages[1].image)) as img:
        assert img.format == "PNG" and max(img.size) == convert.MAX_SIDE  # thu nhỏ
    webp = tmp_path / "a.webp"
    Image.new("RGBA", (80, 40), (255, 0, 0, 128)).save(webp, format="WEBP")
    [page] = convert.convert(webp, sniff.WEBP, max_pages=10).pages
    with Image.open(io.BytesIO(page.image)) as img:
        assert img.mode == "RGB"


def _png_header(width: int, height: int) -> bytes:
    """PNG khai báo kích thước rất lớn nhưng gần như không có dữ liệu (ảnh bom)."""

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(b"")) + chunk(b"IEND", b"")


def test_decompression_bomb_rejected(tmp_path):
    bomb = tmp_path / "bomb.png"
    bomb.write_bytes(_png_header(30000, 30000))  # 900 megapixel
    with pytest.raises(convert.ConversionError, match="quá lớn"):
        convert.convert(bomb, sniff.PNG, max_pages=1)


def test_docx_text_tables_and_images(tmp_path):
    doc = Document()
    doc.sections[0].header.paragraphs[0].text = "CÔNG TY MINH AN"
    doc.add_paragraph("HỢP ĐỒNG DỊCH VỤ")
    t = doc.add_table(rows=2, cols=2)
    t.cell(0, 0).merge(t.cell(0, 1)).text = "Bên A"
    t.cell(1, 0).text, t.cell(1, 1).text = "MST", "0101234567"
    small, big = io.BytesIO(), io.BytesIO()
    Image.new("RGB", (20, 20)).save(small, format="PNG")
    Image.new("RGB", (400, 200), "white").save(big, format="PNG")
    doc.add_picture(io.BytesIO(small.getvalue()))
    doc.add_picture(io.BytesIO(big.getvalue()))
    path = tmp_path / "hd.docx"
    doc.save(path)
    out = convert.convert(path, sniff.DOCX, max_pages=10)
    text = out.pages[0].text
    assert text.splitlines() == ["CÔNG TY MINH AN", "HỢP ĐỒNG DỊCH VỤ", "Bên A", "MST | 0101234567"]
    assert [p.source for p in out.pages] == ["docx_text", "docx_image"]  # logo nhỏ bỏ qua


def test_zip_bomb_guard(tmp_path, monkeypatch):
    path = tmp_path / "hd.docx"
    Document().save(path)
    monkeypatch.setattr(convert, "MAX_UNZIPPED", 1000)
    with pytest.raises(convert.ConversionError, match="giải nén"):
        convert.convert(path, sniff.DOCX, max_pages=10)


def test_xlsx_sheets(tmp_path):
    wb = openpyxl.Workbook()
    wb.active.title = "Hóa đơn"
    wb.active.append(["Số", "INV-01", None])
    wb.active.append([None, None])
    wb.active.append(["Tổng", 1250.5])
    wb.create_sheet("Dòng hàng").append(["Áo", 10])
    path = tmp_path / "hd.xlsx"
    wb.save(path)
    out = convert.convert(path, sniff.XLSX, max_pages=10)
    assert [(p.label, p.text) for p in out.pages] == [("Hóa đơn", "Số\tINV-01\nTổng\t1250.5"), ("Dòng hàng", "Áo\t10")]


def test_txt(tmp_path):
    path = tmp_path / "a.txt"
    path.write_bytes("﻿Hóa đơn".encode())
    assert convert.convert(path, sniff.TXT, max_pages=1).pages[0].text == "Hóa đơn"


# --- OCR ---------------------------------------------------------------------------------------------------------


def test_parse_tesseract_tsv():
    tsv = (
        "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n"
        "1\t1\t0\t0\t0\t0\t0\t0\t800\t600\t-1\t\n"
        "5\t1\t1\t1\t1\t1\t10\t20\t50\t15\t96.5\tCÔNG\n"
        "5\t1\t1\t1\t1\t2\t65\t20\t30\t15\t90\tTY\n"
        "5\t1\t1\t1\t2\t1\t10\t40\t90\t15\t80\t0101234567\n"
    )
    r = ocr.parse_tsv(tsv)
    assert r.text == "CÔNG TY\n0101234567" and r.mean_confidence == round((0.965 + 0.9 + 0.8) / 3, 3)
    assert r.words[0] == {"t": "CÔNG", "c": 0.965, "b": [10, 20, 50, 15]}


def test_tesseract_missing_is_reported(monkeypatch):
    from app.core.config import get_settings

    monkeypatch.setenv("DOCAI_TESSERACT_CMD", "/khong/co/tesseract")
    get_settings.cache_clear()
    buf = io.BytesIO()
    Image.new("RGB", (10, 10)).save(buf, format="PNG")
    with pytest.raises(ocr.OcrError, match="tesseract-ocr-vie"):
        ocr.ocr_image(buf.getvalue(), "tesseract")


def test_paddle_engine_with_local_models(monkeypatch, tmp_path):
    """PaddleOCR (nhóm tùy chọn): nạp model từ DOCAI_PADDLE_MODEL_DIR, tắt kiểm tra nguồn model. Giả lập paddleocr."""
    from app.core.config import get_settings

    (tmp_path / "det").mkdir()
    (tmp_path / "rec").mkdir()
    created = {}

    class FakePaddle:
        def __init__(self, **kw):
            created.update(kw)

        def predict(self, array):
            return [
                {
                    "rec_texts": ["MST", "0101234567"],
                    "rec_scores": [0.99, 0.95],
                    "rec_boxes": [[1, 2, 31, 12], [40, 2, 140, 12]],
                }
            ]

    monkeypatch.setitem(sys.modules, "paddleocr", types.SimpleNamespace(PaddleOCR=FakePaddle))
    monkeypatch.setenv("DOCAI_OCR_ENGINE", "paddle")
    monkeypatch.setenv("DOCAI_PADDLE_MODEL_DIR", str(tmp_path))
    monkeypatch.delenv("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", raising=False)
    if "numpy" not in sys.modules:  # numpy chỉ có trong nhóm ocr-paddle
        monkeypatch.setitem(sys.modules, "numpy", types.SimpleNamespace(array=lambda img: img))
    get_settings.cache_clear()
    ocr._paddle_engine.cache_clear()
    buf = io.BytesIO()
    Image.new("RGB", (200, 20)).save(buf, format="PNG")
    try:
        r = ocr.ocr_image(buf.getvalue(), "paddle")
    finally:
        ocr._paddle_engine.cache_clear()
    assert r.text == "MST\n0101234567" and r.words[1] == {"t": "0101234567", "c": 0.95, "b": [40, 2, 100, 10]}
    assert created["text_detection_model_dir"] == str(tmp_path / "det")
    import os

    assert os.environ["PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK"] == "True"
