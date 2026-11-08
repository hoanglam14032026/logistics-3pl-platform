def test_root(client):
    r = client.get("/")
    assert r.status_code == 200
    assert r.json()["documentation"] == "/docs"


def test_health(client):
    assert client.get("/health").json() == {"status": "healthy"}


def test_openapi_lists_all_paths(client):
    paths = client.get("/openapi.json").json()["paths"]
    for p in (
        "/api/v1/guide",
        "/api/v1/guide/{guide_id}",
        "/api/v1/training/documents",
        "/api/v1/training/documents/{document_id}",
        "/api/v1/business-data/records",
        "/api/v1/business-data/records/{record_id}",
        "/api/v1/video/resources",
        "/api/v1/video/resources/{resource_id}",
    ):
        assert p in paths
