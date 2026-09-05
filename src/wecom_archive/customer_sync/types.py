from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class _PublicModel(BaseModel):
    model_config = ConfigDict(frozen=True, from_attributes=True)


class ObservationRecord(_PublicModel):
    first_seen_at: datetime | None = None
    last_seen_at: datetime | None = None
    last_seen_run_id: str | None = None


class ContactUserRecord(ObservationRecord):
    userid: str
    is_active: bool


class FollowTagRecord(_PublicModel):
    external_userid: str
    userid: str
    ordinal: int
    type: int
    tag_id: str | None
    group_name: str | None
    tag_name: str | None
    raw_data: dict[str, Any]
    fetched_at: datetime


class SyncRunRecord(_PublicModel):
    id: str
    domain: str
    status: str
    started_at: datetime
    finished_at: datetime | None
    seen_count: int
    scope: dict[str, Any]
    error_summary: str | None
    failure_details: dict[str, Any] | None


class GroupAdminRecord(ObservationRecord):
    userid: str
    is_active: bool
    raw_data: dict[str, Any]


class FollowRecord(ObservationRecord):
    userid: str
    remark: str | None
    description: str | None
    create_time: int | None
    add_way: int | None
    remark_corp_name: str | None = None
    remark_mobiles: tuple[str, ...] = ()
    oper_userid: str | None = None
    state: str | None = None
    wechat_channels: dict[str, Any] | None = None
    raw_batch_data: dict[str, Any] | None = None
    raw_detail_data: dict[str, Any] | None = None
    batch_fetched_at: datetime | None = None
    detail_fetched_at: datetime | None = None
    tag_details: tuple[FollowTagRecord, ...] = ()
    tags: tuple[str, ...]
    is_active: bool
    raw_data: dict[str, Any]


class CustomerRecord(ObservationRecord):
    external_userid: str
    name: str | None
    avatar: str | None
    type: int | None
    gender: int | None
    unionid: str | None
    is_active: bool
    position: str | None = None
    corp_name: str | None = None
    corp_full_name: str | None = None
    external_profile: dict[str, Any] | None = None
    follows: tuple[FollowRecord, ...]
    raw_data: dict[str, Any]


class GroupMemberRecord(ObservationRecord):
    userid: str
    type: int
    name: str | None
    unionid: str | None
    join_time: int | None
    join_scene: int | None
    invitor_userid: str | None = None
    group_nickname: str | None
    is_active: bool
    raw_data: dict[str, Any]


class GroupChatRecord(ObservationRecord):
    chat_id: str
    name: str | None
    owner: str | None
    create_time: int | None
    notice: str | None
    status: int | None
    is_active: bool
    members: tuple[GroupMemberRecord, ...]
    member_version: str | None = None
    owner_userid: str | None = None
    follow_status: int | None = None
    raw_list_data: dict[str, Any] = Field(default_factory=dict)
    raw_detail_data: dict[str, Any] = Field(default_factory=dict)
    admins: tuple[GroupAdminRecord, ...] = ()
    admin_userids: tuple[str, ...]
    raw_data: dict[str, Any]


class SyncResult(_PublicModel):
    run_id: str
    domain: Literal["customers", "group_chats"]
    seen_count: int
