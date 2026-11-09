"""
Gửi tin nhắn Zalo Official Account (Giai đoạn 13) – cảnh báo cho manager / business_manager.

`POST ZALO_API_URL` (mặc định API tin nhắn tư vấn của Zalo OA), header `access_token: ZALO_TOKEN`, body
`{"recipient": {"user_id": …}, "message": {"text": …}}`. Thành công khi phản hồi `{"error": 0}`. Lỗi → `ZaloError`
(nơi gọi thử lại qua hàng đợi). Token không ghi vào log; thông báo lỗi chỉ gồm mã lỗi của Zalo / HTTP.
"""

import json
import urllib.error
import urllib.request

from app.core.config import get_settings


class ZaloError(Exception):
    pass


def enabled() -> bool:
    return get_settings().zalo_token is not None


def send_text(user_id: str, text: str) -> None:
    settings = get_settings()
    if settings.zalo_token is None:
        raise ZaloError("Chưa cấu hình ZALO_TOKEN")
    body = json.dumps({"recipient": {"user_id": user_id}, "message": {"text": text[:2000]}}).encode()
    request = urllib.request.Request(
        settings.zalo_api_url,
        data=body,
        method="POST",
        headers={"Content-Type": "application/json", "access_token": settings.zalo_token.get_secret_value()},
    )
    try:
        with urllib.request.urlopen(request, timeout=settings.zalo_timeout_sec) as resp:  # noqa: S310
            payload = json.loads(resp.read(65536) or b"{}")
    except urllib.error.HTTPError as exc:
        raise ZaloError(f"Zalo trả HTTP {exc.code}") from exc
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise ZaloError(f"Không gửi được Zalo ({type(exc).__name__})") from exc
    if payload.get("error") != 0:
        raise ZaloError(f"Zalo từ chối: error={payload.get('error')} {str(payload.get('message', ''))[:100]}")
