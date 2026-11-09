"""
Quản trị outbox sự kiện (Giai đoạn 10):

    python -m app.shared.events.cli dispatch            # xếp gửi lại mọi sự kiện `pending`
    python -m app.shared.events.cli dispatch --failed   # thêm sự kiện `failed` (hết lượt thử lại)
"""

import argparse
import logging
import sys

from app.shared.events import outbox


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    parser = argparse.ArgumentParser(prog="python -m app.shared.events.cli")
    sub = parser.add_subparsers(dest="command", required=True)
    dispatch = sub.add_parser("dispatch", help="Xếp gửi lại sự kiện chờ trong outbox")
    dispatch.add_argument("--failed", action="store_true", help="Gồm cả sự kiện đã hết lượt thử lại")
    args = parser.parse_args(argv)
    count = outbox.schedule_pending(include_failed=args.failed)
    print(f"{count} sự kiện được xếp gửi lại")
    return 0


if __name__ == "__main__":
    sys.exit(main())
