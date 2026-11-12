"""CLI: python -m moda_ui {serve,import,create-admin,gen-key,generate}"""

from __future__ import annotations

import argparse
import getpass
import logging
import sys

from . import repository as repo
from .db import connect, init_schema
from .security import generate_secret_key, hash_password
from .settings import get_settings


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    parser = argparse.ArgumentParser(prog="moda_ui")
    sub = parser.add_subparsers(dest="command", required=True)

    serve = sub.add_parser("serve", help="Chạy web server")
    serve.add_argument("--host", default="0.0.0.0")
    serve.add_argument("--port", type=int, default=8080)

    imp = sub.add_parser("import", help="Nạp cấu hình hiện có (config/, .env) vào SQLite")
    imp.add_argument("--force", action="store_true", help="Ghi đè dữ liệu đã có")

    admin = sub.add_parser("create-admin", help="Tạo tài khoản")
    admin.add_argument("username")
    admin.add_argument("--role", choices=["admin", "viewer"], default="admin")
    admin.add_argument("--password", help="Bỏ trống để nhập từ bàn phím")

    sub.add_parser("gen-key", help="Sinh MODA_CONFIG_SECRET mới")
    sub.add_parser("generate", help="In danh sách file sẽ thay đổi (không ghi, không restart)")
    sub.add_parser("record", help="Đọc kết quả nhận diện từ Kafka và lưu vào SQLite")

    args = parser.parse_args(argv)

    if args.command == "gen-key":
        print(generate_secret_key())
        return 0

    if args.command == "record":
        from .recorder import run as run_recorder

        return run_recorder()

    if args.command == "serve":
        import uvicorn

        from .main import create_app

        # One worker on purpose: apply() uses an in-process lock and progress state.
        uvicorn.run(create_app(), host=args.host, port=args.port, workers=1)
        return 0

    connection = connect()
    init_schema(connection)
    settings = get_settings()

    if args.command == "import":
        from .importer import ImportErrorDetail, import_from_files

        try:
            warnings = import_from_files(connection, settings, force=args.force)
        except ImportErrorDetail as error:
            print(f"Lỗi: {error}", file=sys.stderr)
            return 1
        print(f"Đã nạp cấu hình từ {settings.project_dir} ({len(warnings)} cảnh báo)")
        return 0

    if args.command == "create-admin":
        password = args.password or getpass.getpass("Mật khẩu: ")
        if len(password) < 8:
            print("Mật khẩu phải có ít nhất 8 ký tự", file=sys.stderr)
            return 1
        try:
            repo.create_user(connection, args.username, hash_password(password), args.role)
        except repo.Conflict as error:
            print(f"Lỗi: {error}", file=sys.stderr)
            return 1
        print(f"Đã tạo tài khoản {args.username} ({args.role})")
        return 0

    if args.command == "generate":
        from .applier import preview

        result = preview(connection, settings)
        for error in result["errors"]:
            print(f"LỖI: {error}")
        for item in result["files"]:
            print(item["diff"])
        print(f"{result['pending']} file thay đổi; service bị ảnh hưởng: {result['services']}")
        return 1 if result["errors"] else 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
