import argparse
import os

from .db import Database
from .migrations import current_revision, upgrade


def main():
    parser = argparse.ArgumentParser(description="WeComArchive 数据库迁移")
    parser.add_argument("command", choices=["upgrade", "current"])
    parser.add_argument(
        "--database-url",
        default=os.environ.get("WECOM_DATABASE_URL", "sqlite:///./wecom_archive.db"),
    )
    args = parser.parse_args()
    db = Database(args.database_url)
    try:
        if args.command == "upgrade":
            upgrade(db.engine)
        print(current_revision(db.engine) or "未初始化")
    finally:
        db.dispose()


if __name__ == "__main__":
    main()
