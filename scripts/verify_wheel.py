"""在源码目录外验证已安装的 Nuitka wheel 及迁移资源。"""

from pathlib import Path
from tempfile import TemporaryDirectory

from sqlalchemy import inspect

import wecomarchive
from wecomarchive.db import Base, Database
from wecomarchive.migration import current_revision, head_revision, upgrade


def main():
    package_path = Path(wecomarchive.__file__).resolve()
    if not hasattr(wecomarchive, "__compiled__"):
        raise RuntimeError(f"导入的不是编译扩展模块：{package_path}")
    with TemporaryDirectory(prefix="wecomarchive-wheel-") as directory:
        database_path = Path(directory) / "archive.db"
        db = Database(f"sqlite:///{database_path.as_posix()}")
        try:
            upgrade(db.engine)
            upgrade(db.engine)
            if current_revision(db.engine) != head_revision():
                raise RuntimeError("wheel 数据库迁移版本不正确")
            if not set(Base.metadata.tables).issubset(inspect(db.engine).get_table_names()):
                raise RuntimeError("wheel 数据库迁移缺少业务表")
        finally:
            db.dispose()
    print("编译模块导入及数据库迁移验证通过")


if __name__ == "__main__":
    main()
