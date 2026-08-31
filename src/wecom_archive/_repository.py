from __future__ import annotations

from collections.abc import Sequence
from copy import deepcopy
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import select, tuple_, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from ._models import Customer, CustomerFollow, GroupAdmin, GroupChat, GroupMember, SyncRun
from ._schemas import CustomerDetailItem, GroupChatDetail, GroupChatSummary
from ._schemas import GroupMember as Member
from .types import (
    CustomerRecord,
    FollowRecord,
    GroupChatRecord,
    GroupMemberRecord,
    SyncResult,
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


class CustomerDirectoryRepository:
    def __init__(self, database_url: str) -> None:
        self._engine = create_async_engine(database_url, pool_pre_ping=True)
        self._sessions = async_sessionmaker(self._engine, expire_on_commit=False)

    async def close(self) -> None:
        await self._engine.dispose()

    async def start_run(self, domain: str) -> str:
        run_id = str(uuid4())
        async with self._sessions.begin() as session:
            session.add(
                SyncRun(
                    id=run_id,
                    domain=domain,
                    status="running",
                    started_at=_now(),
                    finished_at=None,
                    seen_count=0,
                    error_summary=None,
                )
            )
        return run_id

    async def fail_run(self, run_id: str, error_summary: str) -> None:
        async with self._sessions.begin() as session:
            run = await session.get(SyncRun, run_id)
            if run is not None:
                run.status = "failed"
                run.finished_at = _now()
                run.error_summary = error_summary[:500]

    async def upsert_customer_item(self, run_id: str, item: CustomerDetailItem) -> str:
        await self.upsert_customer_items(run_id, [item])
        return item.external_contact.external_userid

    async def upsert_customer_items(
        self, run_id: str, items: Sequence[CustomerDetailItem]
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
                customer.name = external.name
                customer.position = external.position
                customer.avatar = external.avatar
                customer.corp_name = external.corp_name
                customer.corp_full_name = external.corp_full_name
                customer.type = external.type
                customer.gender = external.gender
                customer.unionid = external.unionid
                customer.raw_data = external.model_dump(mode="json")
                customer.is_active = True
                customer.last_seen_run_id = run_id
                customer.last_seen_at = now

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
                        raw_data={},
                        tag_ids=[],
                        remark_mobiles=[],
                    )
                    session.add(relation)
                relation.remark = follow.remark
                relation.description = follow.description
                relation.create_time = follow.createtime
                relation.tag_ids = follow.tag_id
                relation.remark_corp_name = follow.remark_corp_name
                relation.remark_mobiles = follow.remark_mobiles
                relation.oper_userid = follow.oper_userid
                relation.add_way = follow.add_way
                relation.state = follow.state
                relation.raw_data = follow.model_dump(mode="json")
                relation.is_active = True
                relation.last_seen_run_id = run_id
                relation.last_seen_at = now
        return set(customer_data)

    async def finalize_customers(self, run_id: str, seen_count: int) -> SyncResult:
        async with self._sessions.begin() as session:
            await session.execute(
                update(Customer).where(Customer.last_seen_run_id != run_id).values(is_active=False)
            )
            await session.execute(
                update(CustomerFollow)
                .where(CustomerFollow.last_seen_run_id != run_id)
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
                    raw_data={},
                )
                session.add(group)
            group.name = detail.name
            group.owner = detail.owner
            group.create_time = detail.create_time
            group.notice = detail.notice
            group.status = summary.status
            group.member_version = detail.member_version
            group.raw_data = detail.model_dump(mode="json")
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
                        last_seen_at=now,
                        last_seen_run_id=run_id,
                        is_active=True,
                    )
                    session.add(admin)
                admin.is_active = True
                admin.last_seen_run_id = run_id
                admin.last_seen_at = now
        return detail.chat_id

    async def finalize_group_chats(self, run_id: str, seen_count: int) -> SyncResult:
        async with self._sessions.begin() as session:
            for model in (GroupChat, GroupMember, GroupAdmin):
                await session.execute(
                    update(model).where(model.last_seen_run_id != run_id).values(is_active=False)
                )
            await self._complete_run(session, run_id, seen_count)
        return SyncResult(run_id=run_id, domain="group_chats", seen_count=seen_count)

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
            return CustomerRecord(
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
                        tags=tuple(follow.tag_ids),
                        is_active=follow.is_active,
                        raw_data=deepcopy(follow.raw_data),
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
                chat_id=group.chat_id,
                name=group.name,
                owner=group.owner,
                create_time=group.create_time,
                notice=group.notice,
                status=group.status,
                is_active=group.is_active,
                members=tuple(
                    GroupMemberRecord(
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
                raw_data=deepcopy(group.raw_data),
            )

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
