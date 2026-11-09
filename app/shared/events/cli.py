"""
Quản trị outbox sự kiện (Giai đoạn 10):

    python -m app.shared.events.cli dispatch            # xếp gửi lại mọi sự kiện `pending`
    python -m app.shared.events.cli dispatch --failed   # thêm sự kiện `failed` (hết lượt thử lại)
    python -m app.shared.events.cli consume control     # Giai đoạn 11: đọc stream của luồng, xử lý (cần Redis)
    python -m app.shared.events.cli replay control      # xử lý thẳng từ sys_events (không cần Redis), idempotent
    python -m app.shared.events.cli replay control --dead   # xử lý lại cả sự kiện đã hết lượt
"""

import argparse
import logging
import sys

from app.shared.events import consumer, outbox


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    parser = argparse.ArgumentParser(prog="python -m app.shared.events.cli")
    sub = parser.add_subparsers(dest="command", required=True)
    dispatch = sub.add_parser("dispatch", help="Xếp gửi lại sự kiện chờ trong outbox")
    dispatch.add_argument("--failed", action="store_true", help="Gồm cả sự kiện đã hết lượt thử lại")
    for name in ("consume", "replay"):
        cmd = sub.add_parser(name, help="Xử lý sự kiện cho một luồng nhận")
        cmd.add_argument("consumer", help="ops | control | assessment")
        if name == "replay":
            cmd.add_argument("--dead", action="store_true", help="Xử lý lại sự kiện đã hết lượt")
    args = parser.parse_args(argv)
    if args.command == "dispatch":
        count = outbox.schedule_pending(include_failed=args.failed)
        print(f"{count} sự kiện được xếp gửi lại")
        return 0
    from app import handlers  # noqa: F401  (đăng ký xử lý sự kiện của các luồng)

    stats = consumer.consume(args.consumer) if args.command == "consume" else consumer.replay(args.consumer, args.dead)
    print(f"Luồng {args.consumer}: {stats}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
