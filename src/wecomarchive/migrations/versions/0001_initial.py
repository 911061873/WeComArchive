"""第一版消息存档结构。固定快照，不导入运行时模型。"""

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

TYPES = (
    "text",
    "image",
    "voice",
    "video",
    "emotion",
    "file",
    "mixed",
    "meeting_voice_call",
    "voip_doc_share",
    "chatrecord",
    "revoke",
    "agree",
    "disagree",
    "card",
    "location",
    "link",
    "weapp",
    "collect",
    "redpacket",
    "meeting",
    "meeting_notification",
    "docmsg",
    "markdown",
    "news",
    "calendar",
    "external_redpacket",
    "sphfeed",
    "voiptext",
    "qydiskfile",
    "solitaire",
    "note",
)
MEDIA = {"image", "voice", "video", "emotion", "file", "meeting_voice_call", "voip_doc_share"}
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
        sa.Column("decrypt_error", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_archive_encrypted_seq", "archive_encrypted", ["seq"])
    op.create_table(
        "archive_decrypted",
        sa.Column("msgid", MESSAGE_ID, sa.ForeignKey("archive_encrypted.msgid"), primary_key=True),
        sa.Column("msgtype", sa.Text, nullable=False),
        sa.Column("msgtime", sa.BigInteger),
        sa.Column("raw_data", sa.JSON, nullable=False),
        sa.Column("parse_error", sa.Text),
    )
    for name in TYPES:
        fields = [
            sa.Column(
                "msgid", MESSAGE_ID, sa.ForeignKey("archive_decrypted.msgid"), primary_key=True
            ),
            sa.Column("payload", sa.JSON, nullable=False),
        ]
        if name == "text":
            fields.append(sa.Column("content", sa.Text, nullable=False))
        if name in MEDIA:
            fields.extend(
                [
                    sa.Column("sdkfileid", sa.Text, nullable=False),
                    sa.Column("filename", sa.Text),
                    sa.Column("md5sum", sa.Text),
                    sa.Column("filesize", sa.BigInteger),
                ]
            )
        op.create_table(f"archive_type_{name}", *fields)


def downgrade() -> None:
    for name in reversed(TYPES):
        op.drop_table(f"archive_type_{name}")
    op.drop_table("archive_decrypted")
    op.drop_table("archive_encrypted")
    op.drop_table("archive_progress")
