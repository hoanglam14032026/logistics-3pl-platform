"""Test đơn vị app/core/config.py (06 §6) – không đụng CSDL."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from app.core.config import ALL_FLOWS, Settings, get_settings

SECRET = "s" * 32
FERNET_KEY = "dGVzdC1vbmx5LXRvdHAta2V5LTMyLWJ5dGVzLWxvbmc="


@pytest.fixture()
def clean_env(monkeypatch):
    """Xóa mọi biến cấu hình trong môi trường (conftest đã đặt DATABASE_URL, UPLOAD_DIR, JWT_SECRET)."""
    for name in Settings.model_fields:
        monkeypatch.delenv(name.upper(), raising=False)
    monkeypatch.setenv("TOTP_ENCRYPTION_KEY", FERNET_KEY)  # bắt buộc (Giai đoạn 6); test riêng xóa đi khi cần
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


# --- TOTP_ENCRYPTION_KEY (Giai đoạn 6) ---------------------------------------------------------------------------


def test_totp_encryption_key_required(clean_env):
    clean_env.setenv("JWT_SECRET", SECRET)
    clean_env.delenv("TOTP_ENCRYPTION_KEY")
    with pytest.raises(ValidationError, match="totp_encryption_key"):
        make()


@pytest.mark.parametrize("value", ["", " , ", "khong-phai-khoa", "s" * 44, FERNET_KEY[:-4]])
def test_totp_encryption_key_must_be_fernet_key(clean_env, value):
    clean_env.setenv("JWT_SECRET", SECRET)
    clean_env.setenv("TOTP_ENCRYPTION_KEY", value)
    with pytest.raises(ValidationError, match="TOTP_ENCRYPTION_KEY"):
        make()


def test_totp_encryption_key_must_differ_from_jwt_secret(clean_env):
    clean_env.setenv("JWT_SECRET", FERNET_KEY)  # khóa Fernet hợp lệ cũng đủ 32 ký tự cho JWT_SECRET
    with pytest.raises(ValidationError, match="không được dùng lại JWT_SECRET"):
        make()


def test_totp_encryption_key_list_for_rotation(clean_env):
    other = "b3RoZXItdGVzdC10b3RwLWtleS0zMi1ieXRlcy1sb24="
    clean_env.setenv("JWT_SECRET", SECRET)
    clean_env.setenv("TOTP_ENCRYPTION_KEY", f" {other} , {FERNET_KEY} ")
    s = make()
    assert s.totp_encryption_keys == (other, FERNET_KEY)
    assert FERNET_KEY not in repr(s)  # SecretStr – không lộ khóa trong log


# --- Hàng đợi, giới hạn tần suất (Giai đoạn 7) -------------------------------------------------------------------


def test_queue_and_rate_limit_defaults(clean_env):
    clean_env.setenv("JWT_SECRET", SECRET)
    s = make()
    assert s.job_queue_url is None and s.test_job_queue_url == "redis://localhost:6379/15"
    assert (s.job_max_retries, s.job_retry_intervals, s.job_timeout_sec) == (5, (10, 30, 120, 600, 1800), 60)
    assert s.rate_limit_enabled is True and s.rate_limit_window_sec == 60
    assert (s.rate_limit_api_per_user, s.rate_limit_api_per_ip) == (300, 120)
    assert (s.rate_limit_file_upload_per_user, s.rate_limit_file_download_per_user) == (20, 120)


@pytest.mark.parametrize(("value", "expected"), [("", None), ("  ", None), ("redis://r:6379/0", "redis://r:6379/0")])
def test_job_queue_url(clean_env, value, expected):
    clean_env.setenv("JWT_SECRET", SECRET)
    clean_env.setenv("JOB_QUEUE_URL", value)
    assert make().job_queue_url == expected


@pytest.mark.parametrize(
    ("name", "value"),
    [("JOB_QUEUE_URL", "amqp://rabbit"), ("JOB_RETRY_INTERVALS", "10,-1"), ("JOB_RETRY_INTERVALS", " , ")],
)
def test_queue_settings_rejected(clean_env, name, value):
    clean_env.setenv("JWT_SECRET", SECRET)
    clean_env.setenv(name, value)
    with pytest.raises(ValidationError):
        make()


def test_retry_intervals_csv(clean_env):
    clean_env.setenv("JWT_SECRET", SECRET)
    clean_env.setenv("JOB_RETRY_INTERVALS", "0, 5,60")
    assert make().job_retry_intervals == (0, 5, 60)
