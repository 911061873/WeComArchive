from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class SyncRun(Base):
    __tablename__ = "customer_directory_sync_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    domain: Mapped[str] = mapped_column(String(32), index=True)
    status: Mapped[str] = mapped_column(String(16), index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    seen_count: Mapped[int] = mapped_column(Integer, default=0)
    error_summary: Mapped[str | None] = mapped_column(Text)
    scope: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    failure_details: Mapped[dict[str, Any] | None] = mapped_column(JSON)


class ObservationMixin:
    """实体最近观测状态，不代表历史版本或已确认删除。"""

    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    last_seen_run_id: Mapped[str] = mapped_column(String(36), index=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ContactUser(ObservationMixin, Base):
    __tablename__ = "contact_users"

    userid: Mapped[str] = mapped_column(String(128), primary_key=True)


class Customer(ObservationMixin, Base):
    __tablename__ = "customers"

    external_userid: Mapped[str] = mapped_column(String(128), primary_key=True)
    name: Mapped[str | None] = mapped_column(String(256))
    position: Mapped[str | None] = mapped_column(String(256))
    avatar: Mapped[str | None] = mapped_column(Text)
    corp_name: Mapped[str | None] = mapped_column(String(256))
    corp_full_name: Mapped[str | None] = mapped_column(String(512))
    type: Mapped[int | None] = mapped_column(Integer)
    gender: Mapped[int | None] = mapped_column(Integer)
    unionid: Mapped[str | None] = mapped_column(String(128))
    external_profile: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    raw_data: Mapped[dict[str, Any]] = mapped_column(JSON)


class CustomerFollow(ObservationMixin, Base):
    __tablename__ = "customer_follows"

    external_userid: Mapped[str] = mapped_column(
        String(128), ForeignKey("customers.external_userid"), primary_key=True
    )
    userid: Mapped[str] = mapped_column(String(128), primary_key=True, index=True)
    remark: Mapped[str | None] = mapped_column(String(256))
    description: Mapped[str | None] = mapped_column(Text)
    create_time: Mapped[int | None] = mapped_column(BigInteger)
    remark_corp_name: Mapped[str | None] = mapped_column(String(256))
    remark_mobiles: Mapped[list[str]] = mapped_column(JSON)
    oper_userid: Mapped[str | None] = mapped_column(String(128))
    add_way: Mapped[int | None] = mapped_column(Integer)
    state: Mapped[str | None] = mapped_column(Text)
    wechat_channels: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    raw_batch_data: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    raw_detail_data: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    batch_fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    detail_fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class GroupChat(ObservationMixin, Base):
    __tablename__ = "group_chats"

    chat_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    name: Mapped[str | None] = mapped_column(String(256))
    owner_userid: Mapped[str | None] = mapped_column(String(128), index=True)
    create_time: Mapped[int | None] = mapped_column(BigInteger)
    notice: Mapped[str | None] = mapped_column(Text)
    follow_status: Mapped[int | None] = mapped_column(Integer)
    member_version: Mapped[str | None] = mapped_column(String(128))
    raw_list_data: Mapped[dict[str, Any]] = mapped_column(JSON)
    raw_detail_data: Mapped[dict[str, Any]] = mapped_column(JSON)


class GroupMember(ObservationMixin, Base):
    __tablename__ = "group_members"
    __table_args__ = (Index("ix_group_members_identity", "userid", "type"),)

    chat_id: Mapped[str] = mapped_column(
        String(128), ForeignKey("group_chats.chat_id"), primary_key=True
    )
    userid: Mapped[str] = mapped_column(String(128), primary_key=True)
    type: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str | None] = mapped_column(String(256))
    unionid: Mapped[str | None] = mapped_column(String(128))
    join_time: Mapped[int | None] = mapped_column(BigInteger)
    join_scene: Mapped[int | None] = mapped_column(Integer)
    invitor_userid: Mapped[str | None] = mapped_column(String(128))
    group_nickname: Mapped[str | None] = mapped_column(String(256))
    raw_data: Mapped[dict[str, Any]] = mapped_column(JSON)


class GroupAdmin(ObservationMixin, Base):
    __tablename__ = "group_admins"

    chat_id: Mapped[str] = mapped_column(
        String(128), ForeignKey("group_chats.chat_id"), primary_key=True
    )
    userid: Mapped[str] = mapped_column(String(128), primary_key=True)
    raw_data: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class CustomerFollowTag(Base):
    """一条跟进关系当前标签集合中的一项，序号不表示永久身份。"""

    __tablename__ = "customer_follow_tags"
    __table_args__ = (
        ForeignKeyConstraint(
            ["external_userid", "userid"],
            ["customer_follows.external_userid", "customer_follows.userid"],
        ),
    )

    external_userid: Mapped[str] = mapped_column(String(128), primary_key=True)
    userid: Mapped[str] = mapped_column(String(128), primary_key=True)
    ordinal: Mapped[int] = mapped_column(Integer, primary_key=True)
    type: Mapped[int] = mapped_column(Integer)
    tag_id: Mapped[str | None] = mapped_column(String(128), index=True)
    group_name: Mapped[str | None] = mapped_column(String(256))
    tag_name: Mapped[str | None] = mapped_column(String(256))
    raw_data: Mapped[dict[str, Any]] = mapped_column(JSON)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
