from pathlib import Path

URL = "/api/v1/training/documents"


def _create(client, title="SOP xuất kho", filename="sop.pdf"):
    return client.post(
        URL,
        data={"title": title, "file_type": "pdf", "category": "warehouse"},
        files={"file": (filename, b"%PDF-1.4 test", "application/pdf")},
    )


def test_upload_update_delete(client, upload_dir):
    r = _create(client)
    assert r.status_code == 201
    body = r.json()
    path = Path(body["file_path"])
    assert path.is_file()
    assert path.parent == upload_dir / "training_documents"
    assert path.suffix == ".pdf" and ".." not in path.name

    r = client.put(f"{URL}/{body['id']}", json={"description": "Bản 2026"})
    assert r.json()["description"] == "Bản 2026"
    assert r.json()["file_path"] == body["file_path"]

    assert client.delete(f"{URL}/{body['id']}").status_code == 200
    assert not path.exists()


def test_title_not_used_in_filename(client, upload_dir):
    r = _create(client, title="../../evil")
    path = Path(r.json()["file_path"])
    assert path.parent == upload_dir / "training_documents"


def test_missing_file_rejected(client):
    r = client.post(URL, data={"title": "x", "file_type": "pdf"})
    assert r.status_code == 422


def test_put_ignores_file_path(client):
    body = _create(client).json()
    r = client.put(f"{URL}/{body['id']}", json={"file_path": "/etc/passwd"})
    assert r.json()["file_path"] == body["file_path"]
