from __future__ import annotations

from collections.abc import Sequence
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from pydantic import BaseModel
from sqlalchemy import delete, select, tuple_, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from .models import (
    ContactUser,
    Customer,
    CustomerFollow,
    CustomerFollowTag,
    GroupAdmin,
    GroupChat,
    GroupMember,
    SyncRun,
)
from .schemas import (
    ExternalContactDetailItem,
    ExternalContactResponse,
    GroupChatDetail,
    GroupChatSummary,
)
from .schemas import GroupMember as Member
from .types import (
    ContactUserRecord,
    CustomerRecord,
    FollowRecord,
    FollowTagRecord,
    GroupAdminRecord,
    GroupChatRecord,
    GroupMemberRecord,
    SyncResult,
    SyncRunRecord,
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


class CustomerDirectoryRepository:
    def __init__(self, database_url: str) -> None:
        self._engine = create_async_engine(database_url, pool_pre_ping=True)
        self._sessions = async_sessionmaker(self._engine, expire_on_commit=False)

    async def close(self) -> None:
        await self._engine.dispose()

    async def start_run(self, domain: str, *, scope: dict[str, Any] | None = None) -> str:
        run_id = str(uuid4())
        async with self._sessions.begin() as session:
            session.add(
                SyncRun(
                    id=run_id,
                    domain=domain,
                    scope=scope or {},
                    status="running",
                    started_at=_now(),
                    finished_at=None,
                    seen_count=0,
                    error_summary=None,
                )
            )
        return run_id

    async def fail_run(
        self, run_id: str, error_summary: str, *, failure_details: dict[str, Any] | None = None
    ) -> None:
        async with self._sessions.begin() as session:
            run = await session.get(SyncRun, run_id)
            if run is not None:
                run.status = "failed"
                run.finished_at = _now()
                run.error_summary = error_summary
                run.failure_details = failure_details

    async def upsert_customer_item(self, run_id: str, item: ExternalContactDetailItem) -> str:
        await self.upsert_customer_items(run_id, [item])
        return item.external_contact.external_userid

    async def upsert_customer_items(
        self, run_id: str, items: Sequence[ExternalContactDetailItem]
    ) -> set[str]:
        if not items:
            return set()

        customer_data = {
            item.external_contact.external_userid: item.external_contact for item in items
        }
        follow_data = {
            (item.external_contact.external_userid, item.follow_info.userid): item.follow_info
            for item in items
        }
        now = _now()
        async with self._sessions.begin() as session:
            customer_ids = set(customer_data)
            existing_customers = {
                customer.external_userid: customer
                for customer in (
                    await session.scalars(
                        select(Customer).where(Customer.external_userid.in_(customer_ids))
                    )
                ).all()
            }
            for external_userid, external in customer_data.items():
                customer = existing_customers.get(external_userid)
                if customer is None:
                    customer = Customer(
                        external_userid=external_userid,
                        first_seen_at=now,
                        last_seen_at=now,
                        last_seen_run_id=run_id,
                        is_active=True,
                        raw_data={},
                    )
                    session.add(customer)
                self._apply_fields(
                    customer,
                    external,
                    (
                        "name",
                        "position",
                        "avatar",
                        "corp_name",
                        "corp_full_name",
                        "type",
                        "gender",
                        "unionid",
                        "external_profile",
                    ),
                )
                customer.raw_data = {
                    **customer.raw_data,
                    **external.model_dump(mode="json", exclude_unset=True),
                }
                customer.is_active = True
                customer.last_seen_run_id = run_id
                customer.last_seen_at = now

            await session.flush()
            follow_keys = set(follow_data)
            existing_follows = {
                (relation.external_userid, relation.userid): relation
                for relation in (
                    await session.scalars(
                        select(CustomerFollow).where(
                            tuple_(CustomerFollow.external_userid, CustomerFollow.userid).in_(
                                follow_keys
                            )
                        )
                    )
                ).all()
            }
            for (external_userid, userid), follow in follow_data.items():
                relation = existing_follows.get((external_userid, userid))
                if relation is None:
                    relation = CustomerFollow(
                        external_userid=external_userid,
                        userid=userid,
                        last_seen_at=now,
                        last_seen_run_id=run_id,
                        is_active=True,
                        first_seen_at=now,
                        remark_mobiles=[],
                    )
                    session.add(relation)
                self._apply_fields(
                    relation,
                    follow,
                    (
                        "remark",
                        "description",
                        "remark_corp_name",
                        "remark_mobiles",
                        "oper_userid",
                        "add_way",
                        "state",
                        "wechat_channels",
                    ),
                )
                if "createtime" in follow.model_fields_set:
                    relation.create_time = follow.createtime
                relation.raw_batch_data = follow.model_dump(mode="json", exclude_unset=True)
                relation.batch_fetched_at = now
                relation.is_active = True
                relation.last_seen_run_id = run_id
                relation.last_seen_at = now
        return set(customer_data)

    @staticmethod
    def _apply_fields(target: Any, source: BaseModel, fields: Sequence[str]) -> None:
        """只更新实际返回字段；显式 null 和空集合仍用于清空。"""
        data = source.model_dump(mode="json", exclude_unset=True)
        for field in fields:
            if field in data:
                setattr(target, field, data[field])

    async def upsert_contact_users(self, run_id: str, userids: Sequence[str]) -> None:
        """保存本次实际观测到的客户联系成员，不隐式清理其他成员。"""
        now = _now()
        async with self._sessions.begin() as session:
            for userid in dict.fromkeys(userids):
                member = await session.get(ContactUser, userid)
                if member is None:
                    member = ContactUser(userid=userid, first_seen_at=now)
                    session.add(member)
                member.is_active = True
                member.last_seen_at = now
                member.last_seen_run_id = run_id

    async def upsert_customer_detail_page(self, run_id: str, page: ExternalContactResponse) -> None:
        """事务保存一页客户详情；仅替换该页实际返回的跟进关系标签集合。"""
        now = _now()
        external = page.external_contact
        async with self._sessions.begin() as session:
            customer = await session.get(Customer, external.external_userid)
            if customer is None:
                customer = Customer(
                    external_userid=external.external_userid, first_seen_at=now, raw_data={}
                )
                session.add(customer)
            self._apply_fields(
                customer,
                external,
                (
                    "name",
                    "position",
                    "avatar",
                    "corp_name",
                    "corp_full_name",
                    "type",
                    "gender",
                    "unionid",
                    "external_profile",
                ),
            )
            customer.raw_data = {
                **customer.raw_data,
                **external.model_dump(mode="json", exclude_unset=True),
            }
            customer.is_active = True
            customer.last_seen_at = now
            customer.last_seen_run_id = run_id
            await session.flush()
            for follow in page.follow_user:
                key = {"external_userid": external.external_userid, "userid": follow.userid}
                relation = await session.get(CustomerFollow, key)
                if relation is None:
                    relation = CustomerFollow(**key, first_seen_at=now, remark_mobiles=[])
                    session.add(relation)
                self._apply_fields(
                    relation,
                    follow,
                    (
                        "remark",
                        "description",
                        "remark_corp_name",
                        "remark_mobiles",
                        "oper_userid",
                        "add_way",
                        "state",
                        "wechat_channels",
                    ),
                )
                if "createtime" in follow.model_fields_set:
                    relation.create_time = follow.createtime
                relation.raw_detail_data = follow.model_dump(mode="json", exclude_unset=True)
                relation.detail_fetched_at = now
                relation.is_active = True
                relation.last_seen_at = now
                relation.last_seen_run_id = run_id
                await session.flush()
                if "tags" in follow.model_fields_set:
                    await session.execute(
                        delete(CustomerFollowTag).where(
                            CustomerFollowTag.external_userid == external.external_userid,
                            CustomerFollowTag.userid == follow.userid,
                        )
                    )
                    for ordinal, tag in enumerate(follow.tags):
                        session.add(
                            CustomerFollowTag(
                                **key,
                                ordinal=ordinal,
                                type=tag.type,
                                tag_id=tag.tag_id,
                                group_name=tag.group_name,
                                tag_name=tag.tag_name,
                                raw_data=tag.model_dump(mode="json", exclude_unset=True),
                                fetched_at=now,
                            )
                        )

    async def finalize_customers(self, run_id: str, seen_count: int) -> SyncResult:
        async with self._sessions.begin() as session:
            await self._require_full_running_run(session, run_id, "customers")
            await session.execute(
                update(Customer).where(Customer.last_seen_run_id != run_id).values(is_active=False)
            )
            await session.execute(
                update(CustomerFollow)
                .where(CustomerFollow.last_seen_run_id != run_id)
                .values(is_active=False)
            )
            await session.execute(
                update(ContactUser)
                .where(ContactUser.last_seen_run_id != run_id)
                .values(is_active=False)
            )
            await self._complete_run(session, run_id, seen_count)
        return SyncResult(run_id=run_id, domain="customers", seen_count=seen_count)

    async def upsert_group_chat(
        self, run_id: str, summary: GroupChatSummary, detail: GroupChatDetail
    ) -> str:
        now = _now()
        async with self._sessions.begin() as session:
            group = await session.get(GroupChat, detail.chat_id)
            if group is None:
                group = GroupChat(
                    chat_id=detail.chat_id,
                    first_seen_at=now,
                    last_seen_at=now,
                    last_seen_run_id=run_id,
                    is_active=True,
                    raw_list_data={},
                    raw_detail_data={},
                )
                session.add(group)
            group.name = detail.name
            group.owner_userid = detail.owner
            group.create_time = detail.create_time
            group.notice = detail.notice
            group.follow_status = summary.status
            group.member_version = detail.member_version
            group.raw_list_data = summary.model_dump(mode="json", exclude_unset=True)
            group.raw_detail_data = detail.model_dump(mode="json", exclude_unset=True)
            await session.flush()
            group.is_active = True
            group.last_seen_run_id = run_id
            group.last_seen_at = now

            for member_data in detail.member_list:
                await self._upsert_group_member(session, run_id, detail.chat_id, member_data, now)
            for admin_data in detail.admin_list:
                key = {"chat_id": detail.chat_id, "userid": admin_data.userid}
                admin = await session.get(GroupAdmin, key)
                if admin is None:
                    admin = GroupAdmin(
                        **key,
                        first_seen_at=now,
                        last_seen_at=now,
                        last_seen_run_id=run_id,
                        is_active=True,
                    )
                    session.add(admin)
                admin.raw_data = admin_data.model_dump(mode="json", exclude_unset=True)
                admin.is_active = True
                admin.last_seen_run_id = run_id
                admin.last_seen_at = now
        return detail.chat_id

    async def finalize_group_chats(self, run_id: str, seen_count: int) -> SyncResult:
        async with self._sessions.begin() as session:
            await self._require_full_running_run(session, run_id, "group_chats")
            for model in (GroupChat, GroupMember, GroupAdmin):
                await session.execute(
                    update(model).where(model.last_seen_run_id != run_id).values(is_active=False)
                )
            await self._complete_run(session, run_id, seen_count)
        return SyncResult(run_id=run_id, domain="group_chats", seen_count=seen_count)

    @staticmethod
    def _record_fields(record: Any, fields: Sequence[str]) -> dict[str, Any]:
        return {
            name: deepcopy(getattr(record, name))
            for name in (
                *fields,
                "first_seen_at",
                "last_seen_at",
                "last_seen_run_id",
            )
        }

    async def list_contact_users(
        self, *, active_only: bool = True, offset: int = 0, limit: int = 100
    ) -> tuple[ContactUserRecord, ...]:
        if offset < 0 or not 1 <= limit <= 1000:
            raise ValueError("offset 不能为负数，limit 必须在 1 到 1000 之间")
        statement = select(ContactUser).order_by(ContactUser.userid).offset(offset).limit(limit)
        if active_only:
            statement = statement.where(ContactUser.is_active.is_(True))
        async with self._sessions() as session:
            return tuple(
                ContactUserRecord.model_validate(row).model_copy(deep=True)
                for row in (await session.scalars(statement)).all()
            )

    async def get_contact_user(self, userid: str) -> ContactUserRecord | None:
        async with self._sessions() as session:
            row = await session.get(ContactUser, userid)
            return ContactUserRecord.model_validate(row).model_copy(deep=True) if row else None

    async def get_customer_follow_tags(
        self, external_userid: str, userid: str
    ) -> tuple[FollowTagRecord, ...]:
        """读取该关系最后保存的标签集合；关系有效性由客户查询中的 is_active 表示。"""
        async with self._sessions() as session:
            rows = (
                await session.scalars(
                    select(CustomerFollowTag)
                    .where(
                        CustomerFollowTag.external_userid == external_userid,
                        CustomerFollowTag.userid == userid,
                    )
                    .order_by(CustomerFollowTag.ordinal)
                )
            ).all()
            return tuple(FollowTagRecord.model_validate(row).model_copy(deep=True) for row in rows)

    async def get_sync_run(self, run_id: str) -> SyncRunRecord | None:
        async with self._sessions() as session:
            row = await session.get(SyncRun, run_id)
            return SyncRunRecord.model_validate(row).model_copy(deep=True) if row else None

    async def get_customer(self, external_userid: str) -> CustomerRecord | None:
        async with self._sessions() as session:
            customer = await session.get(Customer, external_userid)
            if customer is None:
                return None
            follows = (
                await session.scalars(
                    select(CustomerFollow)
                    .where(CustomerFollow.external_userid == external_userid)
                    .order_by(CustomerFollow.userid)
                )
            ).all()
            tag_rows = (
                await session.scalars(
                    select(CustomerFollowTag)
                    .where(CustomerFollowTag.external_userid == external_userid)
                    .order_by(CustomerFollowTag.userid, CustomerFollowTag.ordinal)
                )
            ).all()
            tags_by_user: dict[str, list[FollowTagRecord]] = {}
            for tag in tag_rows:
                tags_by_user.setdefault(tag.userid, []).append(
                    FollowTagRecord.model_validate(tag).model_copy(deep=True)
                )
            return CustomerRecord(
                **self._record_fields(
                    customer,
                    (
                        "position",
                        "corp_name",
                        "corp_full_name",
                        "external_profile",
                    ),
                ),
                external_userid=customer.external_userid,
                name=customer.name,
                avatar=customer.avatar,
                type=customer.type,
                gender=customer.gender,
                unionid=customer.unionid,
                is_active=customer.is_active,
                follows=tuple(
                    FollowRecord(
                        userid=follow.userid,
                        remark=follow.remark,
                        description=follow.description,
                        create_time=follow.create_time,
                        add_way=follow.add_way,
                        tags=tuple(
                            tag.tag_id
                            for tag in tags_by_user.get(follow.userid, [])
                            if tag.tag_id is not None
                        ),
                        tag_details=tuple(tags_by_user.get(follow.userid, [])),
                        **self._record_fields(
                            follow,
                            (
                                "remark_corp_name",
                                "remark_mobiles",
                                "oper_userid",
                                "state",
                                "wechat_channels",
                                "raw_batch_data",
                                "raw_detail_data",
                                "batch_fetched_at",
                                "detail_fetched_at",
                            ),
                        ),
                        is_active=follow.is_active,
                        raw_data=deepcopy(follow.raw_detail_data or follow.raw_batch_data or {}),
                    )
                    for follow in follows
                ),
                raw_data=deepcopy(customer.raw_data),
            )

    async def get_group_chat(self, chat_id: str) -> GroupChatRecord | None:
        async with self._sessions() as session:
            group = await session.get(GroupChat, chat_id)
            if group is None:
                return None
            members = (
                await session.scalars(
                    select(GroupMember)
                    .where(GroupMember.chat_id == chat_id)
                    .order_by(GroupMember.type, GroupMember.userid)
                )
            ).all()
            admins = (
                await session.scalars(
                    select(GroupAdmin)
                    .where(GroupAdmin.chat_id == chat_id, GroupAdmin.is_active.is_(True))
                    .order_by(GroupAdmin.userid)
                )
            ).all()
            return GroupChatRecord(
                **self._record_fields(
                    group,
                    (
                        "member_version",
                        "owner_userid",
                        "follow_status",
                        "raw_list_data",
                        "raw_detail_data",
                    ),
                ),
                admins=tuple(
                    GroupAdminRecord.model_validate(a).model_copy(deep=True) for a in admins
                ),
                chat_id=group.chat_id,
                name=group.name,
                owner=group.owner_userid,
                create_time=group.create_time,
                notice=group.notice,
                status=group.follow_status,
                is_active=group.is_active,
                members=tuple(
                    GroupMemberRecord(
                        **self._record_fields(member, ("invitor_userid",)),
                        userid=member.userid,
                        type=member.type,
                        name=member.name,
                        unionid=member.unionid,
                        join_time=member.join_time,
                        join_scene=member.join_scene,
                        group_nickname=member.group_nickname,
                        is_active=member.is_active,
                        raw_data=deepcopy(member.raw_data),
                    )
                    for member in members
                ),
                admin_userids=tuple(admin.userid for admin in admins),
                raw_data=deepcopy(group.raw_detail_data),
            )

    @staticmethod
    async def _require_full_running_run(session: AsyncSession, run_id: str, domain: str) -> None:
        """全局收尾只用于无过滤的完整扫描，局部范围不得清理全表。"""
        run = await session.get(SyncRun, run_id)
        if run is None or run.status != "running" or run.domain != domain:
            raise RuntimeError("同步运行不存在、已结束或领域不匹配，不能完成同步")
        if run.scope:
            raise RuntimeError("局部范围同步不能执行全局状态清理")

    @staticmethod
    async def _complete_run(session: AsyncSession, run_id: str, seen_count: int) -> None:
        run = await session.get(SyncRun, run_id)
        if run is None:
            raise RuntimeError(f"未知的同步运行记录：{run_id}")
        run.status = "succeeded"
        run.finished_at = _now()
        run.seen_count = seen_count

    @staticmethod
    async def _upsert_group_member(
        session: AsyncSession,
        run_id: str,
        chat_id: str,
        member_data: Member,
        now: datetime,
    ) -> None:
        key = {"chat_id": chat_id, "userid": member_data.userid, "type": member_data.type}
        member = await session.get(GroupMember, key)
        if member is None:
            member = GroupMember(
                **key,
                first_seen_at=now,
                last_seen_at=now,
                last_seen_run_id=run_id,
                is_active=True,
                raw_data={},
            )
            session.add(member)
        member.name = member_data.name
        member.unionid = member_data.unionid
        member.join_time = member_data.join_time
        member.join_scene = member_data.join_scene
        member.invitor_userid = (
            member_data.invitor.get("userid") if member_data.invitor is not None else None
        )
        member.group_nickname = member_data.group_nickname
        member.raw_data = member_data.model_dump(mode="json")
        member.is_active = True
        member.last_seen_run_id = run_id
        member.last_seen_at = now
