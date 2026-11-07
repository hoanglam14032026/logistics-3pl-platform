from pathlib import Path

URL = "/api/v1/video/resources"


def test_upload_with_thumbnail_and_delete(client, upload_dir):
    r = client.post(
        URL,
        data={"title": "Hướng dẫn xe nâng", "duration": "300"},
        files={
            "video_file": ("xe_nang.mp4", b"video-bytes", "video/mp4"),
            "thumbnail_file": ("xe_nang.jpg", b"jpg-bytes", "image/jpeg"),
        },
    )
    assert r.status_code == 201
    body = r.json()
    video, thumb = Path(body["video_path"]), Path(body["thumbnail_path"])
    assert video.is_file() and thumb.is_file()
    assert body["duration"] == 300

    assert client.put(f"{URL}/{body['id']}", json={"duration": 320}).json()["duration"] == 320

    assert client.delete(f"{URL}/{body['id']}").status_code == 200
    assert not video.exists() and not thumb.exists()


def test_upload_without_thumbnail(client):
    r = client.post(URL, data={"title": "Video 2"}, files={"video_file": ("a.mp4", b"x", "video/mp4")})
    assert r.status_code == 201
    assert r.json()["thumbnail_path"] is None


def test_same_title_no_overwrite(client):
    files = lambda: {  # noqa: E731
        "video_file": ("a.mp4", b"x", "video/mp4"),
        "thumbnail_file": ("t.jpg", b"y", "image/jpeg"),
    }
    a = client.post(URL, data={"title": "Trùng"}, files=files()).json()
    b = client.post(URL, data={"title": "Trùng"}, files=files()).json()
    assert a["thumbnail_path"] != b["thumbnail_path"]


def test_negative_duration_rejected(client):
    r = client.post(URL, data={"title": "x", "duration": "-1"}, files={"video_file": ("a.mp4", b"x", "video/mp4")})
    assert r.status_code == 422
