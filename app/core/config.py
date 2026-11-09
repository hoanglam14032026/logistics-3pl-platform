"""
Cấu hình ứng dụng đọc từ biến môi trường và file `.env` (đặc tả 06 §6).

Dùng `get_settings()` (có cache) thay vì đọc `os.environ` trực tiếp. Biến không có
mặc định trong 06 §6 là tùy chọn (`None`), trừ `JWT_SECRET`, `TOTP_ENCRYPTION_KEY` – bắt buộc.
"""

import base64
import binascii
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

from app.core.netguard import ExternalAddressError, check_internal_url

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
    # Khóa tạm tài khoản sau N lần đăng nhập sai liên tiếp (06 §3, bước 0.6)
    auth_max_failed_logins: int = Field(default=5, gt=0)
    auth_lockout_minutes: int = Field(default=15, gt=0)
    # Giới hạn tần suất POST /api/v2/auth/login: số lần gọi tối đa mỗi cửa sổ, theo IP và theo tên đăng nhập
    auth_login_rate_limit_per_ip: int = Field(default=20, gt=0)
    auth_login_rate_limit_per_username: int = Field(default=10, gt=0)
    auth_login_rate_window_sec: int = Field(default=60, gt=0)
    # Xác thực 2 lớp TOTP (06 §3): vai trò bắt buộc bật; mfa_token (bước 2 của đăng nhập) sống N giây
    auth_2fa_required_roles: Annotated[frozenset[str], NoDecode] = frozenset({"customer"})
    auth_mfa_token_ttl_sec: int = Field(default=300, gt=0)
    totp_issuer: str = "Logistics 3PL"
    # Khóa Fernet mã hóa totp_secret (Giai đoạn 6) – riêng, khác JWT_SECRET. Tạo: python -m app.core.secretbox
    # Xoay khóa: "<khóa mới>,<khóa cũ>" (mã hóa bằng khóa đầu, giải mã thử mọi khóa).
    totp_encryption_key: SecretStr
    # Thư mời (đặt mật khẩu lần đầu), đặt lại mật khẩu qua email: hạn token
    invite_token_ttl_hours: int = Field(default=72, gt=0)
    password_reset_token_ttl_min: int = Field(default=30, gt=0)
    # Địa chỉ giao diện web – dùng trong đường dẫn gửi qua email
    public_base_url: str = "http://localhost:5173"

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
    smtp_security: Literal["starttls", "ssl", "none"] = "starttls"
    smtp_timeout_sec: int = Field(default=10, gt=0)
    # auto: smtp nếu có SMTP_HOST, ngược lại console (ghi log – chỉ dùng cho máy dev); memory: dùng trong test
    mail_backend: Literal["auto", "smtp", "console", "memory"] = "auto"

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

    # Hàng đợi tác vụ nền bền vững (Giai đoạn 7): Redis + rq. Chưa đặt → tác vụ chạy nền trong tiến trình API
    # (FastAPI BackgroundTasks – mất nếu tiến trình dừng, không retry; chỉ dùng cho máy dev).
    job_queue_url: str | None = None
    test_job_queue_url: str = "redis://localhost:6379/15"
    job_max_retries: int = Field(default=5, ge=0)
    # Giây chờ trước lần thử lại thứ 1, 2, …; hết danh sách thì dùng giá trị cuối
    job_retry_intervals: Annotated[tuple[int, ...], NoDecode] = (10, 30, 120, 600, 1800)
    job_timeout_sec: int = Field(default=60, gt=0)
    job_failure_ttl_days: int = Field(default=30, gt=0)

    # Giới hạn tần suất mọi API và tải file theo tài khoản (Giai đoạn 7; 06 §3). Chưa đăng nhập: theo IP.
    rate_limit_enabled: bool = True
    rate_limit_window_sec: int = Field(default=60, gt=0)
    rate_limit_api_per_user: int = Field(default=300, gt=0)
    rate_limit_api_per_ip: int = Field(default=120, gt=0)
    rate_limit_file_upload_per_user: int = Field(default=20, gt=0)
    rate_limit_file_download_per_user: int = Field(default=120, gt=0)

    # AI local trích xuất chứng từ (Giai đoạn 9). Chỉ gọi máy chủ model trong máy / mạng nội bộ: OLLAMA_URL ngoài
    # localhost / mạng riêng → ứng dụng KHÔNG khởi động (app/core/netguard.py). Danh mục model đọc từ bảng `ai_models`
    # (admin quản lý) – không ghi tên model trong mã nghiệp vụ.
    ollama_url: str = "http://localhost:11434"
    # Tên máy chủ nội bộ thêm (vd. ai.noibo.congty.vn) – lúc gọi vẫn kiểm DNS phải ra địa chỉ mạng riêng.
    ai_allowed_hosts: Annotated[tuple[str, ...], NoDecode] = ()
    # `code` của model được giao diện ĐÁNH DẤU gợi ý trong GET /api/v2/ai-models/active. Chỉ là gợi ý hiển thị:
    # API tạo job vẫn bắt buộc `model_id` người dùng chọn, không tự dùng model này.
    ai_model_default: str | None = None
    docai_ocr_engine: Literal["tesseract", "paddle"] = "tesseract"
    docai_tesseract_cmd: str = "tesseract"
    docai_ocr_langs: str = Field(default="vie+eng", pattern=r"^[a-z_]{3,}(\+[a-z_]{3,})*$")
    # PaddleOCR: thư mục model tải sẵn (det/rec) – bắt buộc khi DOCAI_OCR_ENGINE=paddle; không tải model từ Internet.
    docai_paddle_model_dir: Path | None = None
    docai_ocr_timeout_sec: int = Field(default=120, gt=0)
    # Dưới ngưỡng ở bất kỳ trường nào (hoặc thiếu trường bắt buộc) → needs_review. Đủ ngưỡng vẫn phải duyệt.
    docai_min_confidence: float = Field(default=0.9, gt=0, le=1)
    docai_max_pages: int = Field(default=30, gt=0, le=500)
    docai_pdf_dpi: int = Field(default=200, ge=72, le=600)
    docai_max_text_chars: int = Field(default=60000, gt=1000)
    docai_vision_max_pages: int = Field(default=4, ge=0, le=50)
    docai_llm_timeout_sec: int = Field(default=600, gt=0)
    # Thời gian tối đa một lần chạy tác vụ trích xuất trong worker (OCR + model lớn chạy CPU/GPU cục bộ: lâu hơn email).
    docai_job_timeout_sec: int = Field(default=1800, gt=0)

    @field_validator(
        "enabled_flows",
        "cors_origins",
        "turn_urls",
        "auth_2fa_required_roles",
        "job_retry_intervals",
        "ai_allowed_hosts",
        mode="before",
    )
    @classmethod
    def _parse_csv(cls, value: object) -> object:
        return _split_csv(value)

    @field_validator("auth_2fa_required_roles")
    @classmethod
    def _known_roles(cls, value: frozenset[str]) -> frozenset[str]:
        known = {
            "ops_staff",
            "manager",
            "business_manager",
            "learner",
            "instructor",
            "content_editor",
            "customer",
            "admin",
        }
        unknown = sorted(value - known)
        if unknown:
            raise ValueError(f"Vai trò không tồn tại: {', '.join(unknown)}")
        return value

    @field_validator("job_queue_url", mode="before")
    @classmethod
    def _empty_is_none(cls, value: object) -> object:
        return None if isinstance(value, str) and not value.strip() else value

    @field_validator("job_queue_url", "test_job_queue_url")
    @classmethod
    def _redis_url(cls, value: str | None) -> str | None:
        if value is not None and not value.startswith(("redis://", "rediss://", "unix://")):
            raise ValueError("JOB_QUEUE_URL phải là địa chỉ Redis: redis://, rediss:// hoặc unix://")
        return value

    @field_validator("job_retry_intervals")
    @classmethod
    def _intervals(cls, value: tuple[int, ...]) -> tuple[int, ...]:
        if not value or any(v < 0 for v in value):
            raise ValueError("JOB_RETRY_INTERVALS: danh sách số giây ≥ 0, vd. 10,30,120")
        return value

    @field_validator("public_base_url")
    @classmethod
    def _base_url(cls, value: str) -> str:
        if not value.startswith(("http://", "https://")):
            raise ValueError("PUBLIC_BASE_URL phải bắt đầu bằng http:// hoặc https://")
        return value.rstrip("/")

    @field_validator("jwt_secret")
    @classmethod
    def _jwt_secret_not_blank(cls, value: SecretStr) -> SecretStr:
        # min_length đếm cả khoảng trắng: chuỗi toàn dấu cách vẫn đủ 32 ký tự nhưng không phải khóa.
        if len(value.get_secret_value().strip()) < 32:
            raise ValueError("JWT_SECRET phải có tối thiểu 32 ký tự không phải khoảng trắng")
        return value

    @field_validator("totp_encryption_key")
    @classmethod
    def _fernet_keys(cls, value: SecretStr) -> SecretStr:
        keys = [k.strip() for k in value.get_secret_value().split(",") if k.strip()]
        if not keys:
            raise ValueError("TOTP_ENCRYPTION_KEY bắt buộc; tạo bằng: python -m app.core.secretbox")
        for key in keys:
            try:
                raw = base64.urlsafe_b64decode(key.encode("ascii"))
            except (binascii.Error, ValueError, UnicodeEncodeError):
                raw = b""
            if len(raw) != 32:
                raise ValueError(
                    "TOTP_ENCRYPTION_KEY phải là khóa Fernet (32 byte, base64 url-safe); "
                    "tạo bằng: python -m app.core.secretbox"
                )
        return SecretStr(",".join(keys))

    @field_validator("ai_model_default", mode="before")
    @classmethod
    def _blank_none(cls, value: object) -> object:
        return None if isinstance(value, str) and not value.strip() else value

    @model_validator(mode="after")
    def _ollama_internal(self) -> "Settings":
        try:
            check_internal_url(self.ollama_url, self.ai_allowed_hosts)
        except ExternalAddressError as exc:
            raise ValueError(f"OLLAMA_URL: {exc}") from exc
        object.__setattr__(self, "ollama_url", self.ollama_url.strip().rstrip("/"))
        if self.docai_ocr_engine == "paddle" and self.docai_paddle_model_dir is None:
            raise ValueError(
                "DOCAI_OCR_ENGINE=paddle cần DOCAI_PADDLE_MODEL_DIR (model tải sẵn, không tải từ Internet)"
            )
        return self

    @model_validator(mode="after")
    def _separate_keys(self) -> "Settings":
        if self.jwt_secret.get_secret_value().strip() in self.totp_encryption_keys:
            raise ValueError("TOTP_ENCRYPTION_KEY không được dùng lại JWT_SECRET")
        return self

    @property
    def totp_encryption_keys(self) -> tuple[str, ...]:
        """Khóa đầu tiên dùng để mã hóa; các khóa sau chỉ để giải mã (xoay khóa)."""
        return tuple(self.totp_encryption_key.get_secret_value().split(","))

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
