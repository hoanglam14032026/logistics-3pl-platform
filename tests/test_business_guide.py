URL = "/api/v1/guide"
PAYLOAD = {"title": "Quy trình nhập kho", "category": "warehouse", "content": "Kiểm đếm, dán nhãn, cất hàng"}


def test_crud_flow(client):
    r = client.post(URL, json=PAYLOAD)
    assert r.status_code == 201
    gid = r.json()["id"]
    assert r.json()["is_active"] is True

    assert client.get(f"{URL}/{gid}").json()["title"] == PAYLOAD["title"]

    # Cập nhật từng phần
    r = client.put(f"{URL}/{gid}", json={"content": "Nội dung mới"})
    assert r.status_code == 200
    assert r.json()["content"] == "Nội dung mới"
    assert r.json()["title"] == PAYLOAD["title"]

    r = client.delete(f"{URL}/{gid}")
    assert r.json() == {"message": "Business guide deleted successfully"}
    assert client.get(f"{URL}/{gid}").status_code == 404


def test_filters(client):
    client.post(URL, json=PAYLOAD)
    client.post(URL, json={**PAYLOAD, "category": "transport"})
    client.post(URL, json={**PAYLOAD, "is_active": False})

    assert len(client.get(URL).json()) == 2  # mặc định is_active=true
    assert len(client.get(URL, params={"category": "warehouse"}).json()) == 1
    assert len(client.get(URL, params={"is_active": False}).json()) == 1


def test_validation_and_not_found(client):
    assert client.post(URL, json={"title": "x"}).status_code == 422
    assert client.put(f"{URL}/999", json={"title": "x"}).status_code == 404
    assert client.delete(f"{URL}/999").status_code == 404
    gid = client.post(URL, json=PAYLOAD).json()["id"]
    assert client.put(f"{URL}/{gid}", json={"title": None}).status_code == 422


def test_put_cannot_change_id(client):
    gid = client.post(URL, json=PAYLOAD).json()["id"]
    r = client.put(f"{URL}/{gid}", json={"id": 500, "title": "Mới"})
    assert r.status_code == 200
    assert r.json()["id"] == gid
