from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import create_engine, select, update
from sqlalchemy.orm import Session, sessionmaker

from ._models import Customer, CustomerFollow, GroupAdmin, GroupChat, GroupMember, SyncRun
from .exceptions import WeComTransportError
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
        self._engine = create_engine(database_url, pool_pre_ping=True)
        self._sessions = sessionmaker(self._engine, expire_on_commit=False)

    def close(self) -> None:
        self._engine.dispose()

    def start_run(self, domain: str) -> str:
        run_id = str(uuid4())
        with self._sessions.begin() as session:
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

    def fail_run(self, run_id: str, error_summary: str) -> None:
        with self._sessions.begin() as session:
            run = session.get(SyncRun, run_id)
            if run is not None:
                run.status = "failed"
                run.finished_at = _now()
                run.error_summary = error_summary[:500]

    def upsert_customer_item(self, run_id: str, item: dict[str, Any]) -> str:
        external = item.get("external_contact")
        follow = item.get("follow_info")
        if not isinstance(external, dict) or not isinstance(follow, dict):
            raise WeComTransportError("WeCom returned an invalid customer detail item")
        external_userid = external.get("external_userid")
        userid = follow.get("userid")
        if not isinstance(external_userid, str) or not external_userid:
            raise WeComTransportError("Customer detail is missing external_userid")
        if not isinstance(userid, str) or not userid:
            raise WeComTransportError("Customer detail is missing follow_info.userid")

        now = _now()
        with self._sessions.begin() as session:
            customer = session.get(Customer, external_userid)
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
            customer.name = external.get("name")
            customer.position = external.get("position")
            customer.avatar = external.get("avatar")
            customer.corp_name = external.get("corp_name")
            customer.corp_full_name = external.get("corp_full_name")
            customer.type = external.get("type")
            customer.gender = external.get("gender")
            customer.unionid = external.get("unionid")
            customer.raw_data = deepcopy(external)
            customer.is_active = True
            customer.last_seen_run_id = run_id
            customer.last_seen_at = now

            key = {"external_userid": external_userid, "userid": userid}
            relation = session.get(CustomerFollow, key)
            if relation is None:
                relation = CustomerFollow(
                    **key,
                    last_seen_at=now,
                    last_seen_run_id=run_id,
                    is_active=True,
                    raw_data={},
                    tag_ids=[],
                    remark_mobiles=[],
                )
                session.add(relation)
            relation.remark = follow.get("remark")
            relation.description = follow.get("description")
            relation.create_time = follow.get("createtime")
            relation.tag_ids = list(follow.get("tag_id") or [])
            relation.remark_corp_name = follow.get("remark_corp_name")
            relation.remark_mobiles = list(follow.get("remark_mobiles") or [])
            relation.oper_userid = follow.get("oper_userid")
            relation.add_way = follow.get("add_way")
            relation.state = follow.get("state")
            relation.raw_data = deepcopy(follow)
            relation.is_active = True
            relation.last_seen_run_id = run_id
            relation.last_seen_at = now
        return external_userid

    def finalize_customers(self, run_id: str, seen_count: int) -> SyncResult:
        with self._sessions.begin() as session:
            session.execute(
                update(Customer).where(Customer.last_seen_run_id != run_id).values(is_active=False)
            )
            session.execute(
                update(CustomerFollow)
                .where(CustomerFollow.last_seen_run_id != run_id)
                .values(is_active=False)
            )
            self._complete_run(session, run_id, seen_count)
        return SyncResult(run_id=run_id, domain="customers", seen_count=seen_count)

    def upsert_group_chat(
        self, run_id: str, summary: dict[str, Any], detail: dict[str, Any]
    ) -> str:
        chat_id = detail.get("chat_id") or summary.get("chat_id")
        if not isinstance(chat_id, str) or not chat_id:
            raise WeComTransportError("Group-chat detail is missing chat_id")
        members = detail.get("member_list") or []
        admins = detail.get("admin_list") or []
        if not isinstance(members, list) or not isinstance(admins, list):
            raise WeComTransportError("WeCom returned an invalid group-chat member list")

        now = _now()
        with self._sessions.begin() as session:
            group = session.get(GroupChat, chat_id)
            if group is None:
                group = GroupChat(
                    chat_id=chat_id,
                    first_seen_at=now,
                    last_seen_at=now,
                    last_seen_run_id=run_id,
                    is_active=True,
                    raw_data={},
                )
                session.add(group)
            group.name = detail.get("name")
            group.owner = detail.get("owner")
            group.create_time = detail.get("create_time")
            group.notice = detail.get("notice")
            group.status = summary.get("status")
            group.member_version = detail.get("member_version")
            group.raw_data = deepcopy(detail)
            group.is_active = True
            group.last_seen_run_id = run_id
            group.last_seen_at = now

            for member_data in members:
                self._upsert_group_member(session, run_id, chat_id, member_data, now)
            for admin_data in admins:
                if not isinstance(admin_data, dict):
                    raise WeComTransportError("WeCom returned an invalid group administrator")
                userid = admin_data.get("userid")
                if not isinstance(userid, str) or not userid:
                    raise WeComTransportError("Group administrator is missing userid")
                admin = session.get(GroupAdmin, {"chat_id": chat_id, "userid": userid})
                if admin is None:
                    admin = GroupAdmin(
                        chat_id=chat_id,
                        userid=userid,
                        last_seen_at=now,
                        last_seen_run_id=run_id,
                        is_active=True,
                    )
                    session.add(admin)
                admin.is_active = True
                admin.last_seen_run_id = run_id
                admin.last_seen_at = now
        return chat_id

    def finalize_group_chats(self, run_id: str, seen_count: int) -> SyncResult:
        with self._sessions.begin() as session:
            for model in (GroupChat, GroupMember, GroupAdmin):
                session.execute(
                    update(model).where(model.last_seen_run_id != run_id).values(is_active=False)
                )
            self._complete_run(session, run_id, seen_count)
        return SyncResult(run_id=run_id, domain="group_chats", seen_count=seen_count)

    def get_customer(self, external_userid: str) -> CustomerRecord | None:
        with self._sessions() as session:
            customer = session.get(Customer, external_userid)
            if customer is None:
                return None
            follows = session.scalars(
                select(CustomerFollow)
                .where(CustomerFollow.external_userid == external_userid)
                .order_by(CustomerFollow.userid)
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

    def get_group_chat(self, chat_id: str) -> GroupChatRecord | None:
        with self._sessions() as session:
            group = session.get(GroupChat, chat_id)
            if group is None:
                return None
            members = session.scalars(
                select(GroupMember)
                .where(GroupMember.chat_id == chat_id)
                .order_by(GroupMember.type, GroupMember.userid)
            ).all()
            admins = session.scalars(
                select(GroupAdmin)
                .where(GroupAdmin.chat_id == chat_id, GroupAdmin.is_active.is_(True))
                .order_by(GroupAdmin.userid)
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
    def _complete_run(session: Session, run_id: str, seen_count: int) -> None:
        run = session.get(SyncRun, run_id)
        if run is None:
            raise RuntimeError(f"Unknown sync run: {run_id}")
        run.status = "succeeded"
        run.finished_at = _now()
        run.seen_count = seen_count

    @staticmethod
    def _upsert_group_member(
        session: Session,
        run_id: str,
        chat_id: str,
        member_data: Any,
        now: datetime,
    ) -> None:
        if not isinstance(member_data, dict):
            raise WeComTransportError("WeCom returned an invalid group member")
        userid = member_data.get("userid")
        member_type = member_data.get("type")
        if not isinstance(userid, str) or not userid or not isinstance(member_type, int):
            raise WeComTransportError("Group member is missing userid or type")
        key = {"chat_id": chat_id, "userid": userid, "type": member_type}
        member = session.get(GroupMember, key)
        if member is None:
            member = GroupMember(
                **key,
                last_seen_at=now,
                last_seen_run_id=run_id,
                is_active=True,
                raw_data={},
            )
            session.add(member)
        invitor = member_data.get("invitor")
        member.name = member_data.get("name")
        member.unionid = member_data.get("unionid")
        member.join_time = member_data.get("join_time")
        member.join_scene = member_data.get("join_scene")
        member.invitor_userid = invitor.get("userid") if isinstance(invitor, dict) else None
        member.group_nickname = member_data.get("group_nickname")
        member.raw_data = deepcopy(member_data)
        member.is_active = True
        member.last_seen_run_id = run_id
        member.last_seen_at = now
