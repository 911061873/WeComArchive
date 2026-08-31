from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text
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
    error_summary: Mapped[str | None] = mapped_column(String(500))


class Customer(Base):
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
    raw_data: Mapped[dict[str, Any]] = mapped_column(JSON)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    last_seen_run_id: Mapped[str] = mapped_column(String(36), index=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class CustomerFollow(Base):
    __tablename__ = "customer_follows"

    external_userid: Mapped[str] = mapped_column(
        String(128), ForeignKey("customers.external_userid"), primary_key=True
    )
    userid: Mapped[str] = mapped_column(String(128), primary_key=True)
    remark: Mapped[str | None] = mapped_column(String(256))
    description: Mapped[str | None] = mapped_column(Text)
    create_time: Mapped[int | None] = mapped_column(Integer)
    tag_ids: Mapped[list[str]] = mapped_column(JSON)
    remark_corp_name: Mapped[str | None] = mapped_column(String(256))
    remark_mobiles: Mapped[list[str]] = mapped_column(JSON)
    oper_userid: Mapped[str | None] = mapped_column(String(128))
    add_way: Mapped[int | None] = mapped_column(Integer)
    state: Mapped[str | None] = mapped_column(String(256))
    raw_data: Mapped[dict[str, Any]] = mapped_column(JSON)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    last_seen_run_id: Mapped[str] = mapped_column(String(36), index=True)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class GroupChat(Base):
    __tablename__ = "group_chats"

    chat_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    name: Mapped[str | None] = mapped_column(String(256))
    owner: Mapped[str | None] = mapped_column(String(128), index=True)
    create_time: Mapped[int | None] = mapped_column(Integer)
    notice: Mapped[str | None] = mapped_column(Text)
    status: Mapped[int | None] = mapped_column(Integer)
    member_version: Mapped[str | None] = mapped_column(String(128))
    raw_data: Mapped[dict[str, Any]] = mapped_column(JSON)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    last_seen_run_id: Mapped[str] = mapped_column(String(36), index=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class GroupMember(Base):
    __tablename__ = "group_members"

    chat_id: Mapped[str] = mapped_column(
        String(128), ForeignKey("group_chats.chat_id"), primary_key=True
    )
    userid: Mapped[str] = mapped_column(String(128), primary_key=True)
    type: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str | None] = mapped_column(String(256))
    unionid: Mapped[str | None] = mapped_column(String(128))
    join_time: Mapped[int | None] = mapped_column(Integer)
    join_scene: Mapped[int | None] = mapped_column(Integer)
    invitor_userid: Mapped[str | None] = mapped_column(String(128))
    group_nickname: Mapped[str | None] = mapped_column(String(256))
    raw_data: Mapped[dict[str, Any]] = mapped_column(JSON)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    last_seen_run_id: Mapped[str] = mapped_column(String(36), index=True)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class GroupAdmin(Base):
    __tablename__ = "group_admins"

    chat_id: Mapped[str] = mapped_column(
        String(128), ForeignKey("group_chats.chat_id"), primary_key=True
    )
    userid: Mapped[str] = mapped_column(String(128), primary_key=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    last_seen_run_id: Mapped[str] = mapped_column(String(36), index=True)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
