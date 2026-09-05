from __future__ import annotations

from typing import Any, Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, PositiveInt


class WeComModel(BaseModel):
    model_config = ConfigDict(extra="allow")


ExternalUserId: TypeAlias = str
FollowUserId: TypeAlias = str


class ApiResponse(WeComModel):
    errcode: int = 0
    errmsg: str | None = None


class TokenResponse(ApiResponse):
    access_token: str | None = None
    expires_in: PositiveInt = 7200


class FollowUsersResponse(ApiResponse):
    follow_user: list[FollowUserId] = Field(default_factory=list)


class ExternalContactListResponse(ApiResponse):
    external_userid: list[ExternalUserId] = Field(default_factory=list)


class ExternalAttributeText(WeComModel):
    value: str


class ExternalAttributeWeb(WeComModel):
    url: str
    title: str


class ExternalAttributeMiniprogram(WeComModel):
    appid: str
    pagepath: str
    title: str


class ExternalAttribute(WeComModel):
    type: int
    name: str
    text: ExternalAttributeText | None = None
    web: ExternalAttributeWeb | None = None
    miniprogram: ExternalAttributeMiniprogram | None = None


class ExternalProfile(WeComModel):
    external_attr: list[ExternalAttribute] = Field(default_factory=list)


class ExternalContact(WeComModel):
    external_userid: ExternalUserId
    name: str | None = None
    position: str | None = None
    avatar: str | None = None
    corp_name: str | None = None
    corp_full_name: str | None = None
    type: int | None = None
    gender: int | None = None
    unionid: str | None = None
    external_profile: ExternalProfile | None = None


class FollowTag(WeComModel):
    group_name: str
    tag_name: str
    tag_id: str | None = None
    type: int


class WechatChannels(WeComModel):
    nickname: str
    source: int


class FollowUser(WeComModel):
    userid: str
    remark: str | None = None
    description: str | None = None
    createtime: int | None = None
    tags: list[FollowTag] = Field(default_factory=list)
    remark_corp_name: str | None = None
    remark_mobiles: list[str] = Field(default_factory=list)
    oper_userid: str | None = None
    add_way: int | None = None
    state: str | None = None
    wechat_channels: WechatChannels | None = None


class ExternalContactResponse(ApiResponse):
    external_contact: ExternalContact
    follow_user: list[FollowUser] = Field(default_factory=list)
    next_cursor: str = ""


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
    wechat_channels: WechatChannels | None = None


class ExternalContactDetailItem(WeComModel):
    external_contact: ExternalContact
    follow_info: FollowInfo


class ExternalContactFailInfo(WeComModel):
    unlicensed_userid_list: list[str] = Field(default_factory=list)


class ExternalContactPageResponse(ApiResponse):
    external_contact_list: list[ExternalContactDetailItem] = Field(default_factory=list)
    fail_info: ExternalContactFailInfo | None = None
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


class CustomerStrategyPrivilege(WeComModel):
    """规则组权限；未指定的字段不发送，由服务端采用默认值。"""

    view_customer_list: Literal[True] | None = None
    view_customer_data: Literal[True] | None = None
    view_room_list: Literal[True] | None = None
    contact_me: Literal[True] | None = None
    join_room: Literal[True] | None = None
    share_customer: bool | None = None
    oper_resign_customer: bool | None = None
    oper_resign_group: bool | None = None
    send_customer_msg: bool | None = None
    edit_welcome_msg: bool | None = None
    view_behavior_data: bool | None = None
    view_room_data: bool | None = None
    send_group_msg: bool | None = None
    room_deduplication: bool | None = None
    rapid_reply: bool | None = None
    onjob_customer_transfer: bool | None = None
    edit_anti_spam_rule: bool | None = None
    export_customer_list: bool | None = None
    export_customer_data: bool | None = None
    export_customer_group_list: bool | None = None
    manage_customer_tag: bool | None = None


class CustomerStrategyUser(WeComModel):
    type: Literal[1] = 1
    userid: str = Field(min_length=1)


class CustomerStrategyParty(WeComModel):
    type: Literal[2] = 2
    partyid: PositiveInt


CustomerStrategyRange: TypeAlias = CustomerStrategyUser | CustomerStrategyParty


class CustomerStrategySummary(WeComModel):
    strategy_id: int


class CustomerStrategy(CustomerStrategySummary):
    parent_id: int = 0
    strategy_name: str
    create_time: int
    admin_list: list[str] = Field(default_factory=list)
    privilege: CustomerStrategyPrivilege


class CustomerStrategyListResponse(ApiResponse):
    strategy: list[CustomerStrategySummary] = Field(default_factory=list)
    next_cursor: str = ""


class CustomerStrategyResponse(ApiResponse):
    strategy: CustomerStrategy


class CustomerStrategyRangeResponse(ApiResponse):
    range: list[CustomerStrategyRange] = Field(default_factory=list)
    next_cursor: str = ""


class CustomerStrategyCreateResponse(ApiResponse):
    strategy_id: int
