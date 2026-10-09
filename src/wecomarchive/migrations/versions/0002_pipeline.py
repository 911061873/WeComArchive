"""第二版持久化流水线；保留第一版数据和类型表。"""

import json

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None
MESSAGE_ID = sa.String(256).with_variant(sa.String(256, collation="utf8mb4_bin"), "mysql")


def upgrade() -> None:
    sqlite = op.get_bind().dialect.name == "sqlite"
    with op.batch_alter_table("archive_encrypted") as batch:
        batch.add_column(sa.Column("publickey_ver", sa.Integer, nullable=False, server_default="0"))
        batch.add_column(
            sa.Column("status", sa.String(32), nullable=False, server_default="pending")
        )
        batch.create_index("ix_archive_encrypted_status", ["status"])
    with op.batch_alter_table("archive_decrypted") as batch:
        batch.add_column(
            sa.Column(
                "raw_text", sa.Text, nullable=not sqlite, server_default="" if sqlite else None
            )
        )
        batch.add_column(
            sa.Column("status", sa.String(32), nullable=False, server_default="pending")
        )
        batch.create_index("ix_archive_decrypted_status", ["status"])
    connection = op.get_bind()
    encrypted = sa.table(
        "archive_encrypted",
        sa.column("msgid"),
        sa.column("raw_data", sa.JSON),
        sa.column("publickey_ver"),
        sa.column("status"),
        sa.column("decrypt_error"),
    )
    decrypted = sa.table(
        "archive_decrypted",
        sa.column("msgid"),
        sa.column("raw_data", sa.JSON),
        sa.column("raw_text"),
        sa.column("status"),
        sa.column("parse_error"),
    )
    # 第一版已解析 JSON，历史明文只能重新序列化，不能恢复原始空白。
    for row in connection.execute(sa.select(encrypted)).mappings().all():
        connection.execute(
            encrypted.update()
            .where(encrypted.c.msgid == row["msgid"])
            .values(
                publickey_ver=row["raw_data"]["publickey_ver"],
                status="failed" if row["decrypt_error"] else "pending",
            )
        )
    for row in connection.execute(sa.select(decrypted)).mappings().all():
        connection.execute(
            decrypted.update()
            .where(decrypted.c.msgid == row["msgid"])
            .values(
                raw_text=json.dumps(row["raw_data"], ensure_ascii=False),
                status="failed" if row["parse_error"] else "pending",
            )
        )
        connection.execute(
            encrypted.update().where(encrypted.c.msgid == row["msgid"]).values(status="success")
        )
    if not sqlite:
        op.alter_column("archive_decrypted", "raw_text", existing_type=sa.Text, nullable=False)
    op.create_table(
        "archive_parsed",
        sa.Column("msgid", MESSAGE_ID, sa.ForeignKey("archive_decrypted.msgid"), primary_key=True),
        sa.Column("msgtime", sa.BigInteger, nullable=False),
        sa.Column("data", sa.JSON, nullable=False),
    )
    op.create_index("ix_archive_parsed_msgtime", "archive_parsed", ["msgtime"])
    op.create_table(
        "archive_consumer_task",
        sa.Column("msgid", MESSAGE_ID, sa.ForeignKey("archive_parsed.msgid"), primary_key=True),
        sa.Column("consumer", MESSAGE_ID, primary_key=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("error", sa.Text),
    )
    op.create_index("ix_archive_consumer_task_status", "archive_consumer_task", ["status"])


def downgrade() -> None:
    raise RuntimeError("第二版包含独立任务状态，拒绝有损降级；请从备份恢复第一版数据库")
