from __future__ import annotations

import pytest
from sqlalchemy import event, inspect, select
from sqlalchemy.dialects import mysql, postgresql, sqlite
from sqlalchemy.schema import CreateTable

from wecom_archive.customer_sync.database import upgrade_database
from wecom_archive.customer_sync.models import (
    Base,
    ContactUser,
    Customer,
    CustomerFollow,
    CustomerFollowTag,
    GroupAdmin,
    GroupChat,
    GroupMember,
    SyncRun,
)
from wecom_archive.customer_sync.repository import CustomerDirectoryRepository
from wecom_archive.customer_sync.schemas import (
    ExternalContactDetailItem,
    ExternalContactResponse,
    GroupChatDetail,
    GroupChatSummary,
)


@pytest.fixture
async def repository(tmp_path):
    url = f"sqlite+aiosqlite:///{(tmp_path / 'directory.sqlite3').as_posix()}"
    await upgrade_database(url)
    repo = CustomerDirectoryRepository(url)

    @event.listens_for(repo._engine.sync_engine, "connect")
    def enable_foreign_keys(connection, _):
        connection.execute("PRAGMA foreign_keys=ON")

    try:
        yield repo
    finally:
        await repo.close()


async def test_eight_tables_and_cross_database_ddl(repository):
    expected = {
        "contact_users",
        "customers",
        "customer_follows",
        "customer_follow_tags",
        "group_chats",
        "group_members",
        "group_admins",
        "customer_directory_sync_runs",
    }
    assert set(Base.metadata.tables) == expected
    async with repository._engine.connect() as connection:
        tables = await connection.run_sync(lambda conn: inspect(conn).get_table_names())
    assert expected <= set(tables)
    for dialect in (sqlite.dialect(), mysql.dialect(), postgresql.dialect()):
        for table in Base.metadata.sorted_tables:
            assert str(CreateTable(table).compile(dialect=dialect))


async def test_detail_tags_and_batch_fields_do_not_overwrite_each_other(repository):
    run = await repository.start_run("customers")
    detail = ExternalContactResponse.model_validate(
        {
            "external_contact": {
                "external_userid": "customer",
                "name": "客户",
                "external_profile": {"external_attr": []},
                "future": "保留",
            },
            "follow_user": [
                {
                    "userid": "alice",
                    "remark": "旧备注",
                    "createtime": 5_000_000_000,
                    "wechat_channels": {"nickname": "视频号", "source": 1},
                    "tags": [
                        {"type": 1, "tag_id": "tag", "tag_name": "企业标签", "group_name": "分组"},
                        {"type": 2, "tag_name": "个人标签", "group_name": "个人"},
                    ],
                }
            ],
        }
    )
    await repository.upsert_customer_detail_page(run, detail)
    await repository.upsert_customer_detail_page(run, detail)
    await repository.upsert_customer_items(
        run,
        [
            ExternalContactDetailItem.model_validate(
                {
                    "external_contact": {"external_userid": "customer"},
                    "follow_info": {"userid": "alice", "remark": "", "tag_id": ["tag"]},
                }
            )
        ],
    )
    async with repository._sessions() as session:
        customer = await session.get(Customer, "customer")
        follow = await session.get(CustomerFollow, ("customer", "alice"))
        tags = (
            await session.scalars(select(CustomerFollowTag).order_by(CustomerFollowTag.ordinal))
        ).all()
        assert customer.name == "客户"
        assert customer.external_profile == {"external_attr": []}
        assert customer.raw_data["future"] == "保留"
        assert follow.remark == ""
        assert follow.create_time == 5_000_000_000
        assert follow.wechat_channels["nickname"] == "视频号"
        assert follow.raw_batch_data["remark"] == ""
        assert follow.raw_detail_data["remark"] == "旧备注"
        assert follow.batch_fetched_at and follow.detail_fetched_at and follow.first_seen_at
        assert len(tags) == 2 and tags[1].tag_id is None
    # 未返回 tags 保留集合；显式空数组清空集合。
    for payload, expected in [({"userid": "alice"}, 2), ({"userid": "alice", "tags": []}, 0)]:
        await repository.upsert_customer_detail_page(
            run,
            ExternalContactResponse.model_validate(
                {
                    "external_contact": {"external_userid": "customer", "name": None},
                    "follow_user": [payload],
                }
            ),
        )
        async with repository._sessions() as session:
            assert len((await session.scalars(select(CustomerFollowTag))).all()) == expected
            assert (await session.get(Customer, "customer")).name is None


async def test_group_members_admins_and_unknown_fields_round_trip(repository):
    run = await repository.start_run("group_chats")
    summary = GroupChatSummary(chat_id="group", status=1, extra_status="保留")
    detail = GroupChatDetail.model_validate(
        {
            "chat_id": "group",
            "owner": "departed-owner",
            "name": "群",
            "create_time": 5_000_000_000,
            "member_version": "version",
            "member_list": [
                {"userid": "same-id", "type": 1, "invitor": {"userid": "invitor"}},
                {"userid": "same-id", "type": 2, "unionid": "union", "name": "访客"},
            ],
            "admin_list": [{"userid": "admin", "future": "保留"}],
        }
    )
    await repository.upsert_group_chat(run, summary, detail)
    await repository.upsert_group_chat(run, summary, detail)
    await repository.finalize_group_chats(run, 1)
    async with repository._sessions() as session:
        group = await session.get(GroupChat, "group")
        assert group.owner_userid == "departed-owner" and group.follow_status == 1
        assert group.raw_list_data["extra_status"] == "保留"
        assert len(group.raw_detail_data["member_list"]) == 2
        assert len((await session.scalars(select(GroupMember))).all()) == 2
        assert (await session.get(GroupAdmin, ("group", "admin"))).raw_data["future"] == "保留"
        assert (await session.scalars(select(Customer))).all() == []
    result = await repository.get_group_chat("group")
    assert result.owner == "departed-owner" and result.status == 1
    assert result.admin_userids == ("admin",)
    assert len(result.members) == 2


async def test_contact_users_and_structured_failure_do_not_clear_existing_data(repository):
    run = await repository.start_run("customers", scope={"userid_list": ["alice"]})
    await repository.upsert_contact_users(run, ["alice", "alice"])
    await repository.fail_run(
        run,
        "部分失败",
        failure_details={
            "unlicensed_userid_list": ["alice"],
        },
    )
    async with repository._sessions() as session:
        record = await session.get(SyncRun, run)
        assert record.scope == {"userid_list": ["alice"]}
        assert record.failure_details == {"unlicensed_userid_list": ["alice"]}
        assert record.status == "failed"
        users = (await session.scalars(select(ContactUser))).all()
        assert len(users) == 1 and users[0].is_active and users[0].first_seen_at


async def test_scoped_and_failed_runs_cannot_finalize_globally(repository):
    scoped = await repository.start_run("customers", scope={"userid_list": ["alice"]})
    with pytest.raises(RuntimeError, match="局部范围"):
        await repository.finalize_customers(scoped, 0)
    failed = await repository.start_run("group_chats")
    await repository.fail_run(failed, "失败")
    with pytest.raises(RuntimeError, match="已结束"):
        await repository.finalize_group_chats(failed, 0)
