"""Test đơn vị app/core/config.py (06 §6) – không đụng CSDL."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from app.core.config import ALL_FLOWS, Settings, get_settings

SECRET = "s" * 32


@pytest.fixture()
def clean_env(monkeypatch):
    """Xóa mọi biến cấu hình trong môi trường (conftest đã đặt DATABASE_URL, UPLOAD_DIR, JWT_SECRET)."""
    for name in Settings.model_fields:
        monkeypatch.delenv(name.upper(), raising=False)
    return monkeypatch


def make(**kwargs) -> Settings:
    return Settings(_env_file=None, **kwargs)


def test_defaults_match_spec(clean_env):
    clean_env.setenv("JWT_SECRET", SECRET)
    s = make()
    assert s.database_url == "postgresql+psycopg://logistics:logistics@localhost:5432/logistics_3pl_dev"
    assert s.test_database_url.endswith("/logistics_3pl_test")
    assert s.storage_backend == "local"
    assert s.upload_dir == Path("./data/uploads")
    assert (s.jwt_access_ttl_min, s.jwt_refresh_ttl_days) == (15, 7)
    assert s.enabled_flows == frozenset(ALL_FLOWS)
    assert s.event_dispatch_interval_sec == 2
    assert s.cors_origins == ()
    assert s.media_server == "livekit"
    assert (s.interview_max_participants, s.interview_max_hours) == (6, 3)
    assert (s.transcription_engine, s.transcription_device) == ("faster_whisper", "cuda")
    assert s.file_limits_json is None and s.job_queue_url is None


def test_jwt_secret_required(clean_env):
    with pytest.raises(ValidationError, match="jwt_secret"):
        make()


def test_jwt_secret_too_short_rejected(clean_env):
    clean_env.setenv("JWT_SECRET", "short")
    with pytest.raises(ValidationError, match="jwt_secret"):
        make()


def test_jwt_secret_hidden_in_repr(clean_env):
    clean_env.setenv("JWT_SECRET", SECRET)
    s = make()
    assert SECRET not in repr(s)
    assert s.jwt_secret.get_secret_value() == SECRET


def test_list_variables_parsed_from_csv(clean_env):
    clean_env.setenv("JWT_SECRET", SECRET)
    clean_env.setenv("ENABLED_FLOWS", " ops, learning ,")
    clean_env.setenv("CORS_ORIGINS", "http://localhost:5173,https://portal.example.vn")
    clean_env.setenv("TURN_URLS", "turn:turn.example.vn:3478")
    s = make()
    assert s.enabled_flows == {"ops", "learning"}
    assert s.cors_origins == ("http://localhost:5173", "https://portal.example.vn")
    assert s.turn_urls == ("turn:turn.example.vn:3478",)


def test_empty_enabled_flows_allowed(clean_env):
    clean_env.setenv("JWT_SECRET", SECRET)
    clean_env.setenv("ENABLED_FLOWS", "")
    assert make().enabled_flows == frozenset()


def test_unknown_flow_rejected(clean_env):
    clean_env.setenv("JWT_SECRET", SECRET)
    clean_env.setenv("ENABLED_FLOWS", "ops,finance")
    with pytest.raises(ValidationError, match="enabled_flows"):
        make()


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("STORAGE_BACKEND", "ftp"),
        ("MEDIA_SERVER", "zoom"),
        ("TRANSCRIPTION_DEVICE", "tpu"),
        ("JWT_ACCESS_TTL_MIN", "0"),
        ("EVENT_DISPATCH_INTERVAL_SEC", "-1"),
    ],
)
def test_invalid_values_rejected(clean_env, name, value):
    clean_env.setenv("JWT_SECRET", SECRET)
    clean_env.setenv(name, value)
    with pytest.raises(ValidationError):
        make()


def test_file_limits_json_parsed(clean_env):
    clean_env.setenv("JWT_SECRET", SECRET)
    clean_env.setenv("FILE_LIMITS_JSON", '{"video": {"max_mb": 1024, "ext": ["mp4"]}}')
    assert make().file_limits_json == {"video": {"max_mb": 1024, "ext": ["mp4"]}}


def test_env_file_read_and_unknown_keys_ignored(clean_env, tmp_path):
    env = tmp_path / ".env"
    env.write_text(f"JWT_SECRET={SECRET}\nPOSTGRES_PASSWORD=logistics\nJWT_ACCESS_TTL_MIN=30\n", encoding="utf-8")
    s = Settings(_env_file=env)
    assert s.jwt_access_ttl_min == 30


def test_environment_overrides_env_file(clean_env, tmp_path):
    env = tmp_path / ".env"
    env.write_text(f"JWT_SECRET={SECRET}\nJWT_ACCESS_TTL_MIN=30\n", encoding="utf-8")
    clean_env.setenv("JWT_ACCESS_TTL_MIN", "5")
    assert Settings(_env_file=env).jwt_access_ttl_min == 5


def test_get_settings_cached():
    assert get_settings() is get_settings()


def test_settings_immutable(clean_env):
    clean_env.setenv("JWT_SECRET", SECRET)
    s = make()
    with pytest.raises(ValidationError):
        s.jwt_access_ttl_min = 99


def test_jwt_secret_blank_is_rejected(clean_env):
    with pytest.raises(ValidationError, match="không phải khoảng trắng"):
        make(jwt_secret=" " * 40)
    with pytest.raises(ValidationError):
        make(jwt_secret="a" * 31 + " " * 10)


def test_cors_wildcard_is_rejected(clean_env):
    clean_env.setenv("JWT_SECRET", SECRET)
    clean_env.setenv("CORS_ORIGINS", "http://localhost:5173,*")
    with pytest.raises(ValidationError, match="không được chứa"):
        make()
