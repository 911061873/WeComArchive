"""第二版数据库初始结构。固定快照，不导入运行时模型。"""

import sqlalchemy as sa
from alembic import op

revision = "0001_v2"
down_revision = None
branch_labels = None
depends_on = None

MESSAGE_ID = sa.String(256).with_variant(sa.String(256, collation="utf8mb4_bin"), "mysql")


def upgrade() -> None:
    op.create_table(
        "archive_progress",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("cursor", sa.BigInteger, nullable=False),
    )
    op.create_table(
        "archive_encrypted",
        sa.Column("msgid", MESSAGE_ID, primary_key=True),
        sa.Column("seq", sa.BigInteger, nullable=False),
        sa.Column("raw_data", sa.JSON, nullable=False),
        sa.Column("publickey_ver", sa.Integer, nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("decrypt_error", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_archive_encrypted_seq", "archive_encrypted", ["seq"])
    op.create_index("ix_archive_encrypted_status", "archive_encrypted", ["status"])
    op.create_table(
        "archive_decrypted",
        sa.Column("msgid", MESSAGE_ID, sa.ForeignKey("archive_encrypted.msgid"), primary_key=True),
        sa.Column("msgtype", sa.Text, nullable=False),
        sa.Column("msgtime", sa.BigInteger),
        sa.Column("raw_data", sa.JSON, nullable=False),
        sa.Column("raw_text", sa.Text, nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("parse_error", sa.Text),
    )
    op.create_index("ix_archive_decrypted_status", "archive_decrypted", ["status"])
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
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("error", sa.Text),
    )
    op.create_index("ix_archive_consumer_task_status", "archive_consumer_task", ["status"])


def downgrade() -> None:
    op.drop_table("archive_consumer_task")
    op.drop_table("archive_parsed")
    op.drop_table("archive_decrypted")
    op.drop_table("archive_encrypted")
    op.drop_table("archive_progress")
