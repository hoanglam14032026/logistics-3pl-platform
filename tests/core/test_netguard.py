"""Chặn gọi ra ngoài mạng nội bộ (Giai đoạn 9): app/core/netguard.py + OLLAMA_URL khi khởi động. Không đụng CSDL."""

import socket

import pytest
from pydantic import ValidationError

from app.core import netguard
from app.core.config import Settings
from app.core.netguard import ExternalAddressError, check_internal_url, resolve_internal

FERNET_KEY = "dGVzdC1vbmx5LXRvdHAta2V5LTMyLWJ5dGVzLWxvbmc="


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost:11434",
        "http://127.0.0.1:11434/",
        "http://[::1]:11434",
        "http://ollama:11434",  # tên dịch vụ docker-compose
        "http://host.docker.internal:11434",
        "http://gpu01.lan",
        "https://ai.local",
        "http://10.1.2.3:11434",
        "http://172.20.0.5:11434",
        "http://192.168.10.2:11434",
        "http://[fd12:3456::1]:11434",
    ],
)
def test_internal_urls_allowed(url):
    check_internal_url(url)


@pytest.mark.parametrize(
    "url",
    [
        "https://api.openai.com/v1",
        "https://api.anthropic.com",
        "http://ollama.example.com:11434",
        "http://8.8.8.8:11434",
        "http://169.254.169.254",  # metadata máy ảo đám mây (link-local)
        "http://[fe80::1]:11434",
        "http://100.64.0.1",  # CGNAT – không phải mạng riêng RFC 1918
        "http://0.0.0.0:11434",
        "http://224.0.0.1",
        "http://[::ffff:8.8.8.8]",
        "http://user:pw@localhost:11434",
        "http://localhost:11434/api?x=1",
        "file:///etc/passwd",
        "localhost:11434",
    ],
)
def test_external_or_unsafe_urls_rejected(url):
    with pytest.raises(ExternalAddressError):
        check_internal_url(url)


def test_extra_allowed_hosts():
    with pytest.raises(ExternalAddressError):
        check_internal_url("http://ai.noibo.congty.vn")
    check_internal_url("http://ai.noibo.congty.vn", allowed_hosts=("AI.noibo.congty.vn",))


def _addr(*ips):
    return lambda host, port, **kw: [
        (socket.AF_INET6 if ":" in ip else socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port)) for ip in ips
    ]


def test_resolution_must_be_internal_for_every_address(monkeypatch):
    monkeypatch.setattr(netguard.socket, "getaddrinfo", _addr("::1", "127.0.0.1"))
    assert resolve_internal("localhost", 11434) == ["127.0.0.1", "::1"]  # IPv4 trước
    monkeypatch.setattr(netguard.socket, "getaddrinfo", _addr("10.0.0.5", "93.184.216.34"))
    with pytest.raises(ExternalAddressError, match="93.184.216.34"):
        resolve_internal("ollama", 11434)  # một địa chỉ công khai là chặn (DNS rebinding)
    monkeypatch.setattr(netguard.socket, "getaddrinfo", _addr("169.254.169.254"))
    with pytest.raises(ExternalAddressError):
        resolve_internal("metadata.internal", 80)


def test_app_refuses_to_start_with_external_ollama_url(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "s" * 40)
    monkeypatch.setenv("TOTP_ENCRYPTION_KEY", FERNET_KEY)
    for url in ("https://api.openai.com", "http://8.8.8.8:11434", "http://169.254.169.254"):
        monkeypatch.setenv("OLLAMA_URL", url)
        with pytest.raises(ValidationError, match="OLLAMA_URL"):
            Settings(_env_file=None)
    monkeypatch.setenv("OLLAMA_URL", "http://ollama:11434/")
    assert Settings(_env_file=None).ollama_url == "http://ollama:11434"


def test_docai_settings(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "s" * 40)
    monkeypatch.setenv("TOTP_ENCRYPTION_KEY", FERNET_KEY)
    monkeypatch.delenv("OLLAMA_URL", raising=False)
    s = Settings(_env_file=None)
    assert (s.ollama_url, s.docai_ocr_engine, s.docai_ocr_langs, s.docai_min_confidence) == (
        "http://localhost:11434",
        "tesseract",
        "vie+eng",
        0.9,
    )
    assert s.ai_model_default is None
    monkeypatch.setenv("DOCAI_OCR_ENGINE", "paddle")
    with pytest.raises(ValidationError, match="DOCAI_PADDLE_MODEL_DIR"):
        Settings(_env_file=None)
    monkeypatch.setenv("DOCAI_OCR_ENGINE", "tesseract")
    monkeypatch.setenv("DOCAI_OCR_LANGS", "vie; rm -rf /")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)
