"""
Tạo tài khoản quản trị đầu tiên (chưa có ai đăng nhập được để gọi POST /api/v2/users).

    python -m app.shared.identity.cli create-admin --username admin --full-name "Quản trị hệ thống"
    echo "$MAT_KHAU" | python -m app.shared.identity.cli create-admin --username admin --full-name "…" --password-stdin

Mật khẩu nhập ẩn (getpass) hoặc đọc từ stdin – không nhận qua tham số dòng lệnh (lộ trong lịch sử shell, `ps`).
"""

import argparse
import getpass
import sys

from pydantic import ValidationError

from app.core.db import SessionLocal, engine
from app.core.errors import AppError
from app.core.migrations import upgrade_to_head
from app.core.rbac import Role
from app.shared.identity import service
from app.shared.identity.schemas import UserCreate


def _read_password(from_stdin: bool) -> str:
    if from_stdin:
        return sys.stdin.readline().rstrip("\n")
    first = getpass.getpass("Mật khẩu: ")
    if first != getpass.getpass("Nhập lại mật khẩu: "):
        raise SystemExit("Hai lần nhập mật khẩu không khớp")
    return first


def create_admin(username: str, full_name: str, email: str | None, password: str) -> int:
    upgrade_to_head(engine)
    data = UserCreate(username=username, full_name=full_name, email=email, password=password, roles=[Role.ADMIN])
    with SessionLocal() as db:
        return service.create_user(db, data, actor_id=None).id


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.shared.identity.cli")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("create-admin", help="Tạo người dùng có vai trò admin")
    p.add_argument("--username", required=True)
    p.add_argument("--full-name", required=True)
    p.add_argument("--email")
    p.add_argument("--password-stdin", action="store_true", help="Đọc mật khẩu từ stdin (dùng trong script)")
    args = parser.parse_args(argv)

    try:
        user_id = create_admin(args.username, args.full_name, args.email, _read_password(args.password_stdin))
    except ValidationError as exc:
        print(f"Dữ liệu không hợp lệ:\n{exc}", file=sys.stderr)
        return 2
    except AppError as exc:
        print(f"Không tạo được: {exc.message}", file=sys.stderr)
        return 1
    print(f"Đã tạo admin id={user_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
