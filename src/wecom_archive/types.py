from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class FollowRecord:
    userid: str
    remark: str | None
    description: str | None
    create_time: int | None
    add_way: int | None
    tags: tuple[str, ...]
    is_active: bool
    raw_data: dict[str, Any]


@dataclass(frozen=True)
class CustomerRecord:
    external_userid: str
    name: str | None
    avatar: str | None
    type: int | None
    gender: int | None
    unionid: str | None
    is_active: bool
    follows: tuple[FollowRecord, ...]
    raw_data: dict[str, Any]


@dataclass(frozen=True)
class GroupMemberRecord:
    userid: str
    type: int
    name: str | None
    unionid: str | None
    join_time: int | None
    join_scene: int | None
    group_nickname: str | None
    is_active: bool
    raw_data: dict[str, Any]


@dataclass(frozen=True)
class GroupChatRecord:
    chat_id: str
    name: str | None
    owner: str | None
    create_time: int | None
    notice: str | None
    status: int | None
    is_active: bool
    members: tuple[GroupMemberRecord, ...]
    admin_userids: tuple[str, ...]
    raw_data: dict[str, Any]


@dataclass(frozen=True)
class SyncResult:
    run_id: str
    domain: str
    seen_count: int
