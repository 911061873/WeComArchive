from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, PositiveInt


class WeComModel(BaseModel):
    model_config = ConfigDict(extra="allow")


class ApiResponse(WeComModel):
    errcode: int = 0
    errmsg: str | None = None


class TokenResponse(ApiResponse):
    access_token: str | None = None
    expires_in: PositiveInt = 7200


class FollowUsersResponse(ApiResponse):
    follow_user: list[str] = Field(default_factory=list)


class ExternalContact(WeComModel):
    external_userid: str
    name: str | None = None
    position: str | None = None
    avatar: str | None = None
    corp_name: str | None = None
    corp_full_name: str | None = None
    type: int | None = None
    gender: int | None = None
    unionid: str | None = None


class FollowInfo(WeComModel):
    userid: str
    remark: str | None = None
    description: str | None = None
    createtime: int | None = None
    tag_id: list[str] = Field(default_factory=list)
    remark_corp_name: str | None = None
    remark_mobiles: list[str] = Field(default_factory=list)
    oper_userid: str | None = None
    add_way: int | None = None
    state: str | None = None


class CustomerDetailItem(WeComModel):
    external_contact: ExternalContact
    follow_info: FollowInfo


class CustomerPageResponse(ApiResponse):
    external_contact_list: list[CustomerDetailItem] = Field(default_factory=list)
    next_cursor: str = ""


class GroupChatSummary(WeComModel):
    chat_id: str
    status: int | None = None


class GroupChatPageResponse(ApiResponse):
    group_chat_list: list[GroupChatSummary] = Field(default_factory=list)
    next_cursor: str = ""


class GroupMember(WeComModel):
    userid: str
    type: int
    name: str | None = None
    unionid: str | None = None
    join_time: int | None = None
    join_scene: int | None = None
    invitor: dict[str, Any] | None = None
    group_nickname: str | None = None


class GroupAdmin(WeComModel):
    userid: str


class GroupChatDetail(WeComModel):
    chat_id: str
    name: str | None = None
    owner: str | None = None
    create_time: int | None = None
    notice: str | None = None
    member_version: str | None = None
    member_list: list[GroupMember] = Field(default_factory=list)
    admin_list: list[GroupAdmin] = Field(default_factory=list)


class GroupChatResponse(ApiResponse):
    group_chat: GroupChatDetail
