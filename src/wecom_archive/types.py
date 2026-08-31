from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict


class _PublicModel(BaseModel):
    model_config = ConfigDict(frozen=True)


class FollowRecord(_PublicModel):
    userid: str
    remark: str | None
    description: str | None
    create_time: int | None
    add_way: int | None
    tags: tuple[str, ...]
    is_active: bool
    raw_data: dict[str, Any]


class CustomerRecord(_PublicModel):
    external_userid: str
    name: str | None
    avatar: str | None
    type: int | None
    gender: int | None
    unionid: str | None
    is_active: bool
    follows: tuple[FollowRecord, ...]
    raw_data: dict[str, Any]


class GroupMemberRecord(_PublicModel):
    userid: str
    type: int
    name: str | None
    unionid: str | None
    join_time: int | None
    join_scene: int | None
    group_nickname: str | None
    is_active: bool
    raw_data: dict[str, Any]


class GroupChatRecord(_PublicModel):
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


class SyncResult(_PublicModel):
    run_id: str
    domain: Literal["customers", "group_chats"]
    seen_count: int
