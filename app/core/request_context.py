"""
Ngữ cảnh của request đang xử lý: `request_id`, IP client (06 §5 – nhật ký có `ip`, `request_id`).

Middleware gắn vào contextvar; route đồng bộ chạy trong threadpool vẫn đọc được (anyio sao chép context sang
luồng). Ngoài request (CLI, test gọi service trực tiếp): giá trị là None.

IP lấy từ kết nối TCP (`scope["client"]`). Chạy sau reverse proxy: bật `uvicorn --proxy-headers
--forwarded-allow-ips=<IP proxy>` để IP là IP thật của client, không phải IP proxy.
"""

from contextvars import ContextVar

request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)
client_ip_var: ContextVar[str | None] = ContextVar("client_ip", default=None)


def current_request_id() -> str | None:
    return request_id_var.get()


def current_client_ip() -> str | None:
    return client_ip_var.get()
