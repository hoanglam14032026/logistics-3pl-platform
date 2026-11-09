"""
Chặn gọi ra ngoài mạng nội bộ (Giai đoạn 9 – AI local): chỉ cho phép địa chỉ máy cục bộ / mạng nội bộ.

Dùng cho `OLLAMA_URL` (kiểm khi khởi động – sai thì ứng dụng không chạy) và `endpoint` của từng model trong danh mục
`ai_models` (kiểm khi admin lưu và **mỗi lần gọi**).

Hai lớp kiểm tra:
1. Theo cú pháp URL (`check_internal_url`): `http`/`https`, không có tên đăng nhập/mật khẩu, query, fragment; máy chủ là
   - địa chỉ IP loopback / mạng riêng (RFC 1918, `fc00::/7`) – **không** nhận link-local `169.254.0.0/16`, `fe80::/10`
     (địa chỉ metadata của máy ảo đám mây), không nhận địa chỉ công khai;
   - tên `localhost`, tên một nhãn (tên dịch vụ docker-compose, vd. `ollama`), tên có hậu tố nội bộ
     (`.localhost`, `.local`, `.internal`, `.lan`, `.home.arpa`) hoặc nằm trong `AI_ALLOWED_HOSTS`.
2. Lúc kết nối (`resolve_internal`): phân giải DNS, **mọi** địa chỉ nhận được phải là loopback / mạng riêng; kết nối
   thẳng tới địa chỉ đã kiểm (không phân giải lại – chống DNS rebinding). Không dùng proxy (`HTTP(S)_PROXY` bị bỏ qua),
   không theo chuyển hướng.
"""

import ipaddress
import socket
from collections.abc import Iterable
from dataclasses import dataclass
from urllib.parse import urlsplit

INTERNAL_SUFFIXES = (".localhost", ".local", ".internal", ".lan", ".home.arpa")


class ExternalAddressError(ValueError):
    """Địa chỉ không thuộc máy cục bộ / mạng nội bộ."""


@dataclass(frozen=True)
class Target:
    scheme: str
    host: str
    port: int
    base_path: str


def _internal_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    if ip.is_loopback:  # trước is_reserved: ::1 nằm trong dải ::/8 "reserved"
        return True
    if ip.is_link_local or ip.is_multicast or ip.is_unspecified or ip.is_reserved:
        return False
    if isinstance(ip, ipaddress.IPv4Address):
        return any(ip in net for net in _PRIVATE_V4)
    return ip in _ULA_V6


_PRIVATE_V4 = tuple(ipaddress.ip_network(n) for n in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"))
_ULA_V6 = ipaddress.ip_network("fc00::/7")


def _host_allowed(host: str, allowed_hosts: Iterable[str]) -> bool:
    try:
        return _internal_ip(ipaddress.ip_address(host))
    except ValueError:
        pass
    name = host.lower().rstrip(".")
    if not name or any(c in name for c in "/\\@ "):
        return False
    if name == "localhost" or name in {h.lower() for h in allowed_hosts}:
        return True
    if "." not in name:
        return True  # tên một nhãn: chỉ phân giải trong mạng nội bộ (docker, /etc/hosts)
    return name.endswith(INTERNAL_SUFFIXES)


def check_internal_url(url: str, allowed_hosts: Iterable[str] = ()) -> Target:
    """Kiểm cú pháp + máy chủ của URL; trả `Target`. Sai → `ExternalAddressError`."""
    try:
        parts = urlsplit(url.strip())
        port = parts.port
    except ValueError as exc:
        raise ExternalAddressError(f"URL không hợp lệ: {url!r}") from exc
    if parts.scheme not in ("http", "https"):
        raise ExternalAddressError("Chỉ nhận http:// hoặc https://")
    if parts.username or parts.password:
        raise ExternalAddressError("URL không được chứa tên đăng nhập/mật khẩu")
    if parts.query or parts.fragment:
        raise ExternalAddressError("URL không được có query (?…) hoặc fragment (#…)")
    host = parts.hostname or ""
    if not _host_allowed(host, allowed_hosts):
        raise ExternalAddressError(
            f"Máy chủ '{host}' không phải localhost / mạng nội bộ – không gọi ra ngoài (AI chạy local)"
        )
    return Target(
        scheme=parts.scheme,
        host=host,
        port=port or (443 if parts.scheme == "https" else 80),
        base_path=parts.path.rstrip("/"),
    )


def resolve_internal(host: str, port: int) -> list[str]:
    """Phân giải `host`; **mọi** địa chỉ phải nội bộ. Trả danh sách IP (IPv4 trước) để kết nối thẳng, thử lần lượt."""
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise ExternalAddressError(f"Không phân giải được '{host}': {exc}") from exc
    addresses = []
    for info in infos:
        address = info[4][0].split("%", 1)[0]
        ip = ipaddress.ip_address(address)
        if not _internal_ip(ip):
            raise ExternalAddressError(f"'{host}' phân giải ra địa chỉ ngoài mạng nội bộ ({ip}) – chặn")
        if str(ip) not in addresses:
            addresses.append(str(ip))
    if not addresses:
        raise ExternalAddressError(f"Không phân giải được '{host}'")
    return sorted(addresses, key=lambda a: ":" in a)
