"""
Cấu hình ứng dụng đọc từ biến môi trường và file `.env` (đặc tả 06 §6).

Dùng `get_settings()` (có cache) thay vì đọc `os.environ` trực tiếp. Biến không có
mặc định trong 06 §6 là tùy chọn (`None`), trừ `JWT_SECRET` – bắt buộc.
"""

from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

Flow = Literal["ops", "control", "learning", "assessment"]
ALL_FLOWS: tuple[Flow, ...] = ("ops", "control", "learning", "assessment")


def _split_csv(value: object) -> object:
    """Biến dạng danh sách viết `a,b,c` (bỏ khoảng trắng, phần tử rỗng)."""
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    return value


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        # .env dùng chung với docker-compose (POSTGRES_PASSWORD, POSTGRES_PORT…) – bỏ qua biến lạ.
        extra="ignore",
        frozen=True,
    )

    # CSDL – PostgreSQL 17 cho mọi môi trường
    database_url: str = "postgresql+psycopg://logistics:logistics@localhost:5432/logistics_3pl_dev"
    test_database_url: str = "postgresql+psycopg://logistics:logistics@localhost:5432/logistics_3pl_test"

    # Lưu trữ file (06 §2.6)
    storage_backend: Literal["local", "s3"] = "local"
    upload_dir: Path = Path("./data/uploads")
    # Ghi đè danh sách loại và dung lượng file theo mục đích; None = giá trị 06 §2.6
    file_limits_json: dict[str, object] | None = None

    # Xác thực (06 §3)
    jwt_secret: SecretStr = Field(min_length=32)
    jwt_access_ttl_min: int = Field(default=15, gt=0)
    jwt_refresh_ttl_days: int = Field(default=7, gt=0)

    # Module (N6)
    enabled_flows: Annotated[frozenset[Flow], NoDecode] = frozenset(ALL_FLOWS)
    event_dispatch_interval_sec: float = Field(default=2, gt=0)

    # HTTP
    cors_origins: Annotated[tuple[str, ...], NoDecode] = ()

    # Thư điện tử
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_username: str | None = None
    smtp_password: SecretStr | None = None
    smtp_from: str | None = None

    # Phỏng vấn online (04 §3.3.1a)
    media_server: Literal["livekit", "jitsi"] = "livekit"
    media_server_url: str | None = None
    media_server_api_key: str | None = None
    media_server_api_secret: SecretStr | None = None
    turn_urls: Annotated[tuple[str, ...], NoDecode] = ()
    turn_shared_secret: SecretStr | None = None
    interview_max_participants: int = Field(default=6, gt=0)
    interview_max_hours: int = Field(default=3, gt=0)

    # Nhận dạng tiếng nói tại máy chủ đơn vị (04 §3.3.1b)
    transcription_engine: str = "faster_whisper"
    transcription_model_path: Path | None = None
    transcription_device: Literal["cuda", "cpu"] = "cuda"

    # Hàng đợi tác vụ nền
    job_queue_url: str | None = None

    @field_validator("enabled_flows", "cors_origins", "turn_urls", mode="before")
    @classmethod
    def _parse_csv(cls, value: object) -> object:
        return _split_csv(value)

    @field_validator("jwt_secret")
    @classmethod
    def _jwt_secret_not_blank(cls, value: SecretStr) -> SecretStr:
        # min_length đếm cả khoảng trắng: chuỗi toàn dấu cách vẫn đủ 32 ký tự nhưng không phải khóa.
        if len(value.get_secret_value().strip()) < 32:
            raise ValueError("JWT_SECRET phải có tối thiểu 32 ký tự không phải khoảng trắng")
        return value

    @field_validator("cors_origins")
    @classmethod
    def _cors_no_wildcard(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        # CORS bật allow_credentials: "*" sẽ cho mọi trang web gọi API kèm thông tin đăng nhập.
        if "*" in value:
            raise ValueError("CORS_ORIGINS không được chứa '*'; liệt kê từng nguồn, vd. https://portal.example.vn")
        return value


@lru_cache
def get_settings() -> Settings:
    """Cấu hình dùng chung toàn ứng dụng; test gọi `get_settings.cache_clear()` sau khi đổi biến môi trường."""
    return Settings()
