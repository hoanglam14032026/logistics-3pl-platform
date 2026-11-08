"""File đính kèm hợp đồng: tải lên, tải xuống, quyền (02 §3.1; 06 §2.6, §4), trên PostgreSQL."""

import hashlib
import os
from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.core.db import SessionLocal
from app.shared.audit.models import SysAuditLog
from app.shared.files.models import SysFile
from tests.conftest import pdf_bytes

URL = "/api/v2/ops/service-contracts"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


def _error(r, status: int, code: str) -> dict:
    assert r.status_code == status, r.text
    assert r.json()["error"]["code"] == code
    return r.json()["error"]


@pytest.fixture()
def world(client, make_party, make_contract, make_user, auth_headers):
    a, b = make_party("Khách A", tax_code="0100000001"), make_party("Khách B", tax_code="0100000002")
    ca, cb = make_contract(a.id), make_contract(b.id)
    roles = {
        "ops_staff": {},
        "manager": {},
        "admin": {},
        "learner": {},
        "content_editor": {},
        "customer": {"party_id": a.id},
    }
    headers = {}
    for role, extra in roles.items():
        make_user(f"u.{role}", roles=[role], **extra)
        headers[role] = auth_headers(f"u.{role}")
    make_user("u.customer.b", roles=["customer"], party_id=b.id)
    headers["customer_b"] = auth_headers("u.customer.b")
    return {"a": ca, "b": cb, "h": headers}


def _upload(client, headers, contract_id, content=None, filename="hop dong.pdf", **form):
    data = {
        "kind": form.pop("kind", "other"),
        **{k: str(v).lower() if isinstance(v, bool) else v for k, v in form.items()},
    }
    return client.post(
        f"{URL}/{contract_id}/attachments",
        files={"file": (filename, content if content is not None else pdf_bytes("x"), "application/octet-stream")},
        data=data,
        headers=headers,
    )


def _stored_files() -> list[Path]:
    root = Path(os.environ["UPLOAD_DIR"])
    return [p for p in root.rglob("*") if p.is_file()]


# --- Tải lên --------------------------------------------------------------------------------------------------


def test_upload_and_download(client, world):
    content = pdf_bytes("Phu luc 01")
    r = _upload(
        client,
        world["h"]["ops_staff"],
        world["a"].id,
        content,
        "Phụ lục 01 – bản ký.pdf",
        kind="amendment",
        description="Phụ lục gia hạn",
    )
    assert r.status_code == 201, r.text
    body = r.json()
    f = body["file"]
    assert (body["kind"], body["customer_visible"], body["description"]) == ("amendment", True, "Phụ lục gia hạn")
    assert (f["mime_type"], f["size_bytes"], f["original_filename"]) == (
        "application/pdf",
        len(content),
        "Phụ lục 01 – bản ký.pdf",
    )
    assert f["sha256"] == hashlib.sha256(content).hexdigest()
    assert f["download_url"] == f"/api/v2/files/{f['file_id']}"
    assert "storage_key" not in f and "path" not in str(f)  # không lộ đường dẫn trên server

    d = client.get(f["download_url"], headers=world["h"]["manager"])
    assert d.status_code == 200 and d.content == content
    assert d.headers["content-type"] == "application/pdf"
    assert "filename*=utf-8''ph%e1%bb%a5%20l%e1%bb%a5c" in d.headers["content-disposition"].lower()
    assert d.headers["x-content-type-options"] == "nosniff" and d.headers["cache-control"] == "private, no-store"
    part = client.get(f["download_url"], headers={**world["h"]["manager"], "Range": "bytes=0-7"})
    assert part.status_code == 206 and part.content == content[:8]

    listed = client.get(f"{URL}/{world['a'].id}/attachments", headers=world["h"]["manager"]).json()
    assert [a["kind"] for a in listed] == ["signed_contract", "amendment"]
    with SessionLocal() as db:
        log = db.scalars(select(SysAuditLog).where(SysAuditLog.action == "contract.attachment_add")).all()[-1]
    assert log.after["sha256"] == f["sha256"] and log.after["kind"] == "amendment"


def test_type_detected_by_content_not_name(client, world):
    r = _upload(client, world["h"]["manager"], world["a"].id, PNG, "anh.pdf")  # đuôi .pdf, nội dung PNG
    assert r.status_code == 201 and r.json()["file"]["mime_type"] == "image/png"
    for content, name in [
        (b"MZ\x90\x00 exe", "hop-dong.pdf"),
        (b"<script>alert(1)</script>", "a.pdf"),
        (b"van ban", "a.txt"),
    ]:
        _error(_upload(client, world["h"]["manager"], world["a"].id, content, name), 415, "UNSUPPORTED_FILE_TYPE")


def test_empty_file_rejected(client, world):
    before = len(_stored_files())
    _error(_upload(client, world["h"]["manager"], world["a"].id, b""), 422, "VALIDATION_ERROR")
    assert len(_stored_files()) == before


def test_too_large_rejected(client, world, monkeypatch):
    from app.core.config import get_settings

    monkeypatch.setenv("FILE_LIMITS_JSON", '{"contract_document": {"max_mb": 0.001}}')  # ~1 KB
    get_settings.cache_clear()
    before = len(_stored_files())
    err = _error(_upload(client, world["h"]["manager"], world["a"].id, pdf_bytes("x" * 3000)), 413, "FILE_TOO_LARGE")
    assert err["details"][0]["field"] == "body.file"
    assert len(_stored_files()) == before  # không để lại file


def test_filename_sanitized(client, world):
    r = _upload(client, world["h"]["manager"], world["a"].id, filename="../../etc/passwd.pdf")
    assert r.json()["file"]["original_filename"] == "passwd.pdf"


@pytest.mark.parametrize("form", [{"kind": "khac"}, {"description": "x" * 501}])
def test_upload_validation(client, world, form):
    _error(_upload(client, world["h"]["manager"], world["a"].id, **form), 422, "VALIDATION_ERROR")


def test_no_orphan_file_when_database_fails(client, world, monkeypatch):
    from app.flows.operations import service

    def boom(*args, **kwargs):
        raise RuntimeError("CSDL lỗi giả lập")

    monkeypatch.setattr(service, "_audit", boom)
    files_before, rows_before = len(_stored_files()), _count_files()
    r = _upload(client, world["h"]["manager"], world["a"].id)
    _error(r, 500, "INTERNAL_ERROR")  # UnhandledErrorMiddleware: lỗi → 500 định dạng thống nhất
    assert len(_stored_files()) == files_before and _count_files() == rows_before


def _count_files() -> int:
    with SessionLocal() as db:
        return db.scalar(select(func.count()).select_from(SysFile))


# --- Quyền tải lên, tải xuống ----------------------------------------------------------------------------------------


@pytest.mark.parametrize("role", ["learner", "content_editor", "customer", "admin"])
def test_who_cannot_upload(client, world, role):
    _error(_upload(client, world["h"][role], world["a"].id), 403, "FORBIDDEN")


def test_download_permissions(client, world):
    signed = client.get(f"{URL}/{world['a'].id}/attachments", headers=world["h"]["manager"]).json()[0]
    internal = _upload(client, world["h"]["manager"], world["a"].id, kind="other").json()  # mặc định không cho khách
    signed_url, internal_url = signed["file"]["download_url"], internal["file"]["download_url"]
    h = world["h"]
    for role in ("ops_staff", "manager", "admin"):
        assert client.get(internal_url, headers=h[role]).status_code == 200
    for role in ("learner", "content_editor"):
        _error(client.get(signed_url, headers=h[role]), 404, "NOT_FOUND")  # 404: không lộ sự tồn tại
    assert client.get(signed_url, headers=h["customer"]).status_code == 200  # khách hàng của hợp đồng
    _error(client.get(internal_url, headers=h["customer"]), 404, "NOT_FOUND")  # file nội bộ
    _error(client.get(signed_url, headers=h["customer_b"]), 404, "NOT_FOUND")  # khách hàng khác
    _error(client.get(signed_url), 401, "UNAUTHENTICATED")
    _error(client.get("/api/v2/files/999999", headers=h["manager"]), 404, "NOT_FOUND")


def test_customer_loses_access_when_portal_disabled(client, world):
    from sqlalchemy import update

    from app.flows.operations.models import OpsServiceContract

    signed = client.get(f"{URL}/{world['a'].id}/attachments", headers=world["h"]["manager"]).json()[0]
    with SessionLocal() as db:
        db.execute(
            update(OpsServiceContract).where(OpsServiceContract.id == world["a"].id).values(portal_enabled=False)
        )
        db.commit()
    _error(client.get(signed["file"]["download_url"], headers=world["h"]["customer"]), 404, "NOT_FOUND")


def test_customer_visible_can_be_set(client, world):
    r = _upload(client, world["h"]["manager"], world["a"].id, kind="other", customer_visible=True)
    assert r.json()["customer_visible"] is True
    assert client.get(r.json()["file"]["download_url"], headers=world["h"]["customer"]).status_code == 200
