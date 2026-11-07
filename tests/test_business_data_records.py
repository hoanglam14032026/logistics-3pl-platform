URL = "/api/v1/business-data/records"
PAYLOAD = {"record_type": "contract", "title": "Hợp đồng kho bãi KH A", "data_content": '{"so_hd": "HD-01"}'}


def test_crud_flow(client):
    r = client.post(URL, json=PAYLOAD)
    assert r.status_code == 201
    rid = r.json()["id"]

    r = client.put(f"{URL}/{rid}", json={"category": "kho-bai"})
    assert r.json()["category"] == "kho-bai"
    assert r.json()["record_type"] == "contract"

    assert client.delete(f"{URL}/{rid}").status_code == 200
    assert client.get(f"{URL}/{rid}").status_code == 404


def test_data_content_optional(client):
    r = client.post(URL, json={"record_type": "customer", "title": "KH B"})
    assert r.status_code == 201
    assert r.json()["data_content"] is None


def test_filter_record_type(client):
    client.post(URL, json=PAYLOAD)
    client.post(URL, json={**PAYLOAD, "record_type": "partner"})
    assert len(client.get(URL, params={"record_type": "partner"}).json()) == 1
