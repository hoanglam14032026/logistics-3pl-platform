"""Thư mời đặt mật khẩu, quên/đặt lại mật khẩu qua email (Giai đoạn 5), gửi nền (Giai đoạn 6), trên PostgreSQL.
Thư: MAIL_BACKEND=memory. TestClient chạy tác vụ nền trước khi trả response cho test."""

from datetime import timedelta

import pytest
from sqlalchemy import select, update

from app.core.db import SessionLocal
from app.core.timeutil import now_utc
from app.shared.audit.models import SysAuditLog
from app.shared.identity.models import SysUser, SysUserToken
from app.shared.notifications import mail
from tests.conftest import DEFAULT_PASSWORD, link_token

ACCEPT, FORGOT, RESET = "/api/v2/auth/invitations/accept", "/api/v2/auth/password/forgot", "/api/v2/auth/password/reset"
NEW = "MatKhauMoi@2026"
QUEUED = {"message": "Thư mời đã được xếp vào hàng gửi"}


def _error(r, status: int, code: str) -> dict:
    assert r.status_code == status, r.text
    assert r.json()["error"]["code"] == code
    return r.json()["error"]


def _actions(action: str) -> list[SysAuditLog]:
    with SessionLocal() as db:
        return list(db.scalars(select(SysAuditLog).where(SysAuditLog.action == action).order_by(SysAuditLog.id)))


@pytest.fixture()
def world(client, make_party, make_contract, make_user, auth_headers):
    a, b = make_party("Khách A", tax_code="0100000001"), make_party("Khách B", tax_code="0100000002")
    make_contract(a.id)
    make_contract(b.id)
    make_user("quan.ly", roles=["manager"])
    make_user("a.admin", roles=["customer"], party_id=a.id, is_customer_admin=True)
    return {"a": a, "b": b, "manager": auth_headers("quan.ly"), "a_admin": auth_headers("a.admin")}


def _invite(client, world, username="kh.lan", email="lan@a.vn", party="a"):
    r = client.post(
        f"/api/v2/md/parties/{world[party].id}/users",
        json={"username": username, "full_name": "Lan", "email": email},
        headers=world["manager"],
    )
    assert r.status_code == 201, r.text
    return r.json()


# --- Thư mời ------------------------------------------------------------------------------------------------------


def test_invitation_mail_and_accept(client, world, login):
    user = _invite(client, world)
    assert "invitation_sent" not in user
    [m] = [m for m in mail.OUTBOX if m.to == "lan@a.vn"]
    assert "kh.lan" in m.body and "http://localhost:5173/accept-invite#token=" in m.body  # token sau '#'
    token = link_token("lan@a.vn", "accept-invite")
    with SessionLocal() as db:
        stored = db.scalar(select(SysUserToken.token_hash).where(SysUserToken.purpose == "invite"))
    assert token != stored and len(stored) == 64  # chỉ lưu SHA-256
    # Trước khi nhận lời mời: không có mật khẩu nào đăng nhập được
    _error(
        client.post("/api/v2/auth/login", json={"username": "kh.lan", "password": DEFAULT_PASSWORD}),
        401,
        "UNAUTHENTICATED",
    )
    assert client.post(ACCEPT, json={"token": token, "new_password": DEFAULT_PASSWORD}).status_code == 204
    _error(client.post(ACCEPT, json={"token": token, "new_password": NEW}), 422, "VALIDATION_ERROR")  # dùng một lần
    assert login("kh.lan")["access_token"]  # (thiết lập 2FA bắt buộc trong fixture)
    assert [log.entity_id for log in _actions("user.invite_accepted")] == [user["id"]]


@pytest.mark.parametrize("password", ["ngan", "x" * 129, "kh.lan"])
def test_accept_rejects_weak_password(client, world, password):
    _invite(client, world)
    token = link_token("lan@a.vn", "accept-invite")
    _error(client.post(ACCEPT, json={"token": token, "new_password": password}), 422, "VALIDATION_ERROR")
    assert client.post(ACCEPT, json={"token": token, "new_password": DEFAULT_PASSWORD}).status_code == 204  # chưa dùng


def test_expired_invitation(client, world):
    _invite(client, world)
    with SessionLocal() as db:
        db.execute(update(SysUserToken).values(expires_at=now_utc() - timedelta(seconds=1)))
        db.commit()
    err = _error(
        client.post(ACCEPT, json={"token": link_token("lan@a.vn", "accept-invite"), "new_password": NEW}),
        422,
        "VALIDATION_ERROR",
    )
    assert err["details"][0]["field"] == "body.token"


def test_invitation_requires_email(client, world):
    _error(
        client.post(
            f"/api/v2/md/parties/{world['a'].id}/users",
            json={"username": "kh.x", "full_name": "X"},
            headers=world["manager"],
        ),
        422,
        "VALIDATION_ERROR",
    )
    _error(
        client.post(
            f"/api/v2/md/parties/{world['a'].id}/users",
            json={"username": "kh.x", "full_name": "X", "email": "x@a.vn", "password": "MatKhau@2026"},
            headers=world["manager"],
        ),
        422,
        "VALIDATION_ERROR",
    )  # không còn nhận mật khẩu ban đầu


def test_resend_invitation(client, world, accept_invite):
    user = _invite(client, world)
    first = link_token("lan@a.vn", "accept-invite")
    r = client.post(f"/api/v2/md/parties/{world['a'].id}/users/{user['id']}:resend-invite", headers=world["manager"])
    assert r.status_code == 202 and r.json() == QUEUED
    second = link_token("lan@a.vn", "accept-invite")
    assert first != second
    _error(client.post(ACCEPT, json={"token": first, "new_password": NEW}), 422, "VALIDATION_ERROR")  # token cũ vô hiệu
    accept_invite("lan@a.vn")
    _error(
        client.post(f"/api/v2/md/parties/{world['a'].id}/users/{user['id']}:resend-invite", headers=world["manager"]),
        409,
        "INVALID_STATE_TRANSITION",
    )  # đã nhận lời mời
    _error(
        client.post(f"/api/v2/md/parties/{world['b'].id}/users/{user['id']}:resend-invite", headers=world["manager"]),
        404,
        "NOT_FOUND",
    )  # khác khách hàng


def test_customer_admin_invite_sends_mail(client, world, accept_invite, login):
    r = client.post(
        "/api/v2/portal/users",
        json={"username": "kh.hung", "full_name": "Hùng", "email": "hung@a.vn"},
        headers=world["a_admin"],
    )
    assert r.status_code == 201 and [m.to for m in mail.OUTBOX] == ["hung@a.vn"]
    assert (
        client.post(f"/api/v2/portal/users/{r.json()['id']}:resend-invite", headers=world["a_admin"]).status_code == 202
    )
    accept_invite("hung@a.vn")
    assert login("kh.hung")["access_token"]
    other = _invite(client, world, "kh.b", "b@b.vn", party="b")
    _error(client.post(f"/api/v2/portal/users/{other['id']}:resend-invite", headers=world["a_admin"]), 404, "NOT_FOUND")


@pytest.mark.parametrize(
    ("exc", "reason"),
    [
        (mail.MailError("Gửi thư thất bại: SMTPServerDisconnected"), "Gửi thư thất bại: SMTPServerDisconnected"),
        (RuntimeError("bất ngờ"), "Lỗi không xác định: RuntimeError"),
    ],
)
def test_mail_failure_keeps_account_and_allows_resend(client, world, monkeypatch, caplog, exc, reason):
    """Gửi nền lỗi (kể cả lỗi bất ngờ): request vẫn 201, ghi log + nhật ký user.mail_failed; gửi lại được."""

    def broken(_mail, settings=None):
        raise exc

    monkeypatch.setattr(mail, "send", broken)
    user = _invite(client, world)  # 201 – lỗi gửi không làm lỗi request
    [log] = _actions("user.mail_failed")
    assert (log.entity_id, log.actor_id, log.after) == (
        user["id"],
        _user("quan.ly").id,
        {"purpose": "invite", "error": reason},
    )
    assert log.request_id  # cùng request_id với request tạo tài khoản
    assert "Không gửi được thư invite" in caplog.text or "Lỗi không xác định khi gửi thư invite" in caplog.text
    assert [a.action for a in _actions("user.invite_sent")] == ["user.invite_sent"]
    monkeypatch.undo()
    r = client.post(f"/api/v2/md/parties/{world['a'].id}/users/{user['id']}:resend-invite", headers=world["manager"])
    assert r.status_code == 202 and [m.to for m in mail.OUTBOX] == ["lan@a.vn"]


# --- Quên / đặt lại mật khẩu ------------------------------------------------------------------------------------------


@pytest.fixture()
def staff(client, make_user):
    make_user("nhan.vien", email="nv@cty.vn")
    make_user("khong.email")
    make_user("da.khoa", email="khoa@cty.vn", is_active=False)


@pytest.mark.parametrize("identifier", ["nhan.vien", "NV@CTY.VN", " nhan.vien "])
def test_forgot_sends_link(client, staff, identifier):
    r = client.post(FORGOT, json={"identifier": identifier})
    assert r.status_code == 202
    [m] = mail.OUTBOX
    assert m.to == "nv@cty.vn" and "/reset-password#token=" in m.body


@pytest.mark.parametrize("identifier", ["khong-co", "khong.email", "da.khoa", "la@ngoai.vn"])
def test_forgot_does_not_reveal(client, staff, identifier):
    expected = client.post(FORGOT, json={"identifier": "nhan.vien"}).json()
    mail.OUTBOX.clear()
    r = client.post(FORGOT, json={"identifier": identifier})
    assert (r.status_code, r.json()) == (202, expected)  # cùng phản hồi
    assert mail.OUTBOX == []


def test_reset_password(client, staff, login):
    tokens = login("nhan.vien")
    for _ in range(5):
        client.post("/api/v2/auth/login", json={"username": "nhan.vien", "password": "SaiMatKhau1"})
    assert _user("nhan.vien").locked_until is not None
    client.post(FORGOT, json={"identifier": "nhan.vien"})
    token = link_token("nv@cty.vn", "reset-password")
    assert client.post(RESET, json={"token": token, "new_password": NEW}).status_code == 204
    user = _user("nhan.vien")
    assert user.locked_until is None and user.failed_login_count == 0  # mở khóa tạm
    _error(client.post("/api/v2/auth/refresh", json={"refresh_token": tokens["refresh_token"]}), 401, "UNAUTHENTICATED")
    _error(
        client.post("/api/v2/auth/login", json={"username": "nhan.vien", "password": DEFAULT_PASSWORD}),
        401,
        "UNAUTHENTICATED",
    )
    assert login("nhan.vien", NEW)["access_token"]
    _error(client.post(RESET, json={"token": token, "new_password": "Khac@2026xx"}), 422, "VALIDATION_ERROR")  # 1 lần
    [log] = _actions("user.password_reset")
    assert log.after == {"method": "email_link"} and len(_actions("user.password_reset_requested")) == 1


def test_new_reset_request_invalidates_previous(client, staff):
    client.post(FORGOT, json={"identifier": "nhan.vien"})
    first = link_token("nv@cty.vn", "reset-password")
    client.post(FORGOT, json={"identifier": "nhan.vien"})
    _error(client.post(RESET, json={"token": first, "new_password": NEW}), 422, "VALIDATION_ERROR")
    assert (
        client.post(RESET, json={"token": link_token("nv@cty.vn", "reset-password"), "new_password": NEW}).status_code
        == 204
    )


def test_expired_reset_and_wrong_purpose(client, staff, world):
    client.post(FORGOT, json={"identifier": "nhan.vien"})
    with SessionLocal() as db:
        db.execute(
            update(SysUserToken)
            .where(SysUserToken.purpose == "password_reset")
            .values(expires_at=now_utc() - timedelta(seconds=1))
        )
        db.commit()
    _error(
        client.post(RESET, json={"token": link_token("nv@cty.vn", "reset-password"), "new_password": NEW}),
        422,
        "VALIDATION_ERROR",
    )
    _invite(client, world)
    invite_token = link_token("lan@a.vn", "accept-invite")
    _error(
        client.post(RESET, json={"token": invite_token, "new_password": NEW}), 422, "VALIDATION_ERROR"
    )  # sai mục đích
    _error(client.post(RESET, json={"token": "khong-co", "new_password": NEW}), 422, "VALIDATION_ERROR")


def test_reset_keeps_2fa(client, world, accept_invite, login):
    _invite(client, world)
    accept_invite("lan@a.vn")
    login("kh.lan")  # bật 2FA
    client.post(FORGOT, json={"identifier": "kh.lan"})
    client.post(RESET, json={"token": link_token("lan@a.vn", "reset-password"), "new_password": NEW})
    r = client.post("/api/v2/auth/login", json={"username": "kh.lan", "password": NEW})
    assert r.json()["mfa_required"] is True  # vẫn cần mã TOTP


def test_forgot_rate_limited(client, staff, monkeypatch):
    from app.core.config import get_settings

    monkeypatch.setenv("AUTH_LOGIN_RATE_LIMIT_PER_USERNAME", "2")
    get_settings.cache_clear()
    for _ in range(2):
        assert client.post(FORGOT, json={"identifier": "nhan.vien"}).status_code == 202
    r = client.post(FORGOT, json={"identifier": "NHAN.VIEN"})
    assert r.status_code == 429 and r.json()["error"]["code"] == "RATE_LIMITED" and r.headers["Retry-After"]


def _user(username: str) -> SysUser:
    with SessionLocal() as db:
        return db.scalar(select(SysUser).where(SysUser.username == username))


# --- Gửi nền (Giai đoạn 6) ---------------------------------------------------------------------------------------


class _Queue:
    """Thay BackgroundTasks: giữ tác vụ để kiểm tra hàm nghiệp vụ không gửi thư trong request."""

    def __init__(self):
        self.tasks = []

    def add_task(self, func, *args, **kwargs):
        self.tasks.append((func, args, kwargs))

    def run(self):
        return [func(*args, **kwargs) for func, args, kwargs in self.tasks]


def test_service_queues_mail_instead_of_sending(client, staff):
    """Hàm nghiệp vụ commit token rồi chỉ xếp thư – không gọi SMTP trong request; tác vụ nền gửi sau."""
    from app.shared.identity import email_flows

    queue = _Queue()
    with SessionLocal() as db:
        email_flows.request_password_reset(db, "nhan.vien", queue)
    assert mail.OUTBOX == [] and len(queue.tasks) == 1
    with SessionLocal() as db:  # token đã commit trước khi thư được gửi
        assert db.scalar(select(SysUserToken.id).where(SysUserToken.purpose == "password_reset")) is not None
    assert queue.run() == [True]
    assert [m.to for m in mail.OUTBOX] == ["nv@cty.vn"]


def test_reset_sends_password_changed_notice(client, staff):
    client.post(FORGOT, json={"identifier": "nhan.vien"})
    token = link_token("nv@cty.vn", "reset-password")
    mail.OUTBOX.clear()
    assert client.post(RESET, json={"token": token, "new_password": NEW}).status_code == 204
    [m] = mail.OUTBOX
    assert m.to == "nv@cty.vn" and m.subject == "Mật khẩu của bạn đã được đặt lại"
    assert "#token=" not in m.body and NEW not in m.body


def test_forgot_and_reset_mail_failure_do_not_fail_request(client, staff, monkeypatch):
    sent = []

    def flaky(m, settings=None):
        if sent:  # thư đầu (đường dẫn) gửi được, thư báo đổi mật khẩu lỗi
            raise mail.MailError("Gửi thư thất bại: TimeoutError")
        sent.append(m)

    monkeypatch.setattr(mail, "send", flaky)
    assert client.post(FORGOT, json={"identifier": "nhan.vien"}).status_code == 202
    token = sent[0].body.split("#token=")[1].split()[0]
    assert client.post(RESET, json={"token": token, "new_password": NEW}).status_code == 204
    [log] = _actions("user.mail_failed")
    assert log.after == {"purpose": "password_changed", "error": "Gửi thư thất bại: TimeoutError"}
    sent.clear()

    def broken(m, settings=None):
        raise mail.MailError("Gửi thư thất bại: SMTPAuthenticationError")

    monkeypatch.setattr(mail, "send", broken)
    r = client.post(FORGOT, json={"identifier": "nhan.vien"})
    assert r.status_code == 202  # cùng phản hồi như khi gửi được
    assert [log.after["purpose"] for log in _actions("user.mail_failed")] == ["password_changed", "password_reset"]


def test_audit_failure_in_background_is_swallowed(client, staff, monkeypatch, caplog):
    """Gửi lỗi và ghi nhật ký cũng lỗi (CSDL mất kết nối): tác vụ nền chỉ ghi log, không ném lỗi."""
    from app.shared.identity import email_flows

    def broken(m, settings=None):
        raise mail.MailError("x")

    def no_db():
        raise RuntimeError("CSDL không kết nối được")

    monkeypatch.setattr(mail, "send", broken)
    monkeypatch.setattr(email_flows, "SessionLocal", no_db)
    assert email_flows.deliver(mail.Mail("a@b.vn", "s", "b"), 1, "invite", None) is False
    assert "Không ghi được nhật ký lỗi gửi thư user_id=1" in caplog.text


# --- Backend SMTP --------------------------------------------------------------------------------------------------


class FakeSMTP:
    instances: list["FakeSMTP"] = []
    fail: Exception | None = None

    def __init__(self, host, port, timeout=None, context=None):
        self.host, self.port, self.timeout, self.calls = host, port, timeout, []
        FakeSMTP.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.calls.append("quit")

    def starttls(self, context=None):
        self.calls.append("starttls")

    def login(self, user, password):
        self.calls.append(("login", user, password))

    def send_message(self, msg):
        if FakeSMTP.fail:
            raise FakeSMTP.fail
        self.calls.append(("send", msg["From"], msg["To"], msg["Subject"], msg.get_content()))


def _smtp_settings(**override):
    from app.core.config import Settings

    base = {
        "jwt_secret": "s" * 32,
        "smtp_host": "smtp.cty.vn",
        "smtp_port": 587,
        "smtp_username": "no-reply@cty.vn",
        "smtp_password": "bi-mat",
        "smtp_from": "Logistics 3PL <no-reply@cty.vn>",
        "mail_backend": "auto",
    }
    return Settings(_env_file=None, **{**base, **override})


def test_smtp_starttls(monkeypatch):
    import smtplib

    FakeSMTP.instances, FakeSMTP.fail = [], None
    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    mail.send(mail.Mail("lan@a.vn", "Tiêu đề có dấu", "Nội dung"), _smtp_settings())
    [server] = FakeSMTP.instances
    assert (server.host, server.port, server.timeout) == ("smtp.cty.vn", 587, 10)
    assert server.calls[0] == "starttls" and server.calls[1] == ("login", "no-reply@cty.vn", "bi-mat")
    assert server.calls[2][1:4] == ("Logistics 3PL <no-reply@cty.vn>", "lan@a.vn", "Tiêu đề có dấu")
    assert server.calls[2][4].strip() == "Nội dung"


def test_smtp_errors_become_mail_error(monkeypatch):
    import smtplib

    FakeSMTP.instances, FakeSMTP.fail = [], smtplib.SMTPRecipientsRefused({})
    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    with pytest.raises(mail.MailError):
        mail.send(mail.Mail("lan@a.vn", "x", "y"), _smtp_settings())
    with pytest.raises(mail.MailError, match="SMTP_FROM"):
        mail.send(mail.Mail("lan@a.vn", "x", "y"), _smtp_settings(smtp_from=None))
    FakeSMTP.fail = None


def test_console_backend_when_no_smtp(caplog):
    with caplog.at_level("WARNING", logger="app.mail"):
        mail.send(mail.Mail("lan@a.vn", "Thử", "Nội dung"), _smtp_settings(smtp_host=None))
    assert "MAIL_BACKEND=console" in caplog.text and "lan@a.vn" in caplog.text
