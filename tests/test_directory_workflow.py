from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import select

from wecom_archive import CustomerContactDirectory, WeComArchiveError, WeComTransportError
from wecom_archive.customer_sync.models import SyncRun
from wecom_archive.customer_sync.schemas import (
    ExternalContactPageResponse,
    ExternalContactResponse,
    GroupChatDetail,
    GroupChatSummary,
)


class Client:
    def __init__(self):
        self.detail_ids = []
        self.fail = None
        self.empty = False

    async def aclose(self):
        pass

    async def get_follow_user_ids(self):
        return [] if self.empty else ["alice", "bob"]

    async def iter_customer_batch_pages(self, userids):
        payload = {
            "external_contact_list": [
                {
                    "external_contact": {"external_userid": "customer", "name": "客户"},
                    "follow_info": {"userid": userid, "tag_id": ["old-batch-tag"]},
                }
                for userid in userids
            ],
        }
        if self.fail == "partial":
            payload["fail_info"] = {"unlicensed_userid_list": ["bob"]}
        yield ExternalContactPageResponse.model_validate(payload)

    async def iter_customer_detail_pages(self, external_userid):
        self.detail_ids.append(external_userid)
        for userid in ["alice", "bob"]:
            if userid == "bob" and self.fail == "detail":
                raise WeComTransportError("详情分页失败")
            if userid == "bob" and self.fail == "cancel":
                raise asyncio.CancelledError()
            yield ExternalContactResponse.model_validate(
                {
                    "external_contact": {
                        "external_userid": external_userid,
                        "position": "职位",
                        "corp_name": "公司",
                        "corp_full_name": "完整公司",
                        "external_profile": {"external_attr": []},
                    },
                    "follow_user": [
                        {
                            "userid": userid,
                            "remark": "详情备注",
                            "remark_corp_name": "备注公司",
                            "remark_mobiles": ["123"],
                            "oper_userid": "alice",
                            "state": "渠道",
                            "wechat_channels": {"nickname": "视频号", "source": 1},
                            "tags": [
                                {
                                    "type": 1,
                                    "tag_id": "detail-tag",
                                    "tag_name": "企业",
                                    "group_name": "组",
                                },
                                {"type": 2, "tag_name": "个人", "group_name": "组"},
                            ],
                        }
                    ],
                }
            )

    async def iter_group_chat_summaries(self, owners):
        if not self.empty:
            yield GroupChatSummary(chat_id="group", status=0)

    async def get_group_chat_detail(self, chat_id):
        return GroupChatDetail.model_validate(
            {
                "chat_id": chat_id,
                "owner": "alice",
                "member_version": "version",
                "member_list": [
                    {
                        "userid": "visitor",
                        "type": 2,
                        "invitor": {"userid": "alice"},
                        "group_nickname": "昵称",
                    }
                ],
                "admin_list": [{"userid": "bob", "custom": "字段"}],
            }
        )


@pytest.fixture
async def directory(tmp_path):
    instance = CustomerContactDirectory(
        corp_id="corp",
        secret="secret",
        data_dir=tmp_path,
        request_concurrency=2,
    )
    await instance._client.aclose()
    instance._client = Client()
    async with instance:
        yield instance


async def test_sync_and_public_queries_cover_all_eight_tables(directory):
    customers, groups = await directory.sync_all_once()
    assert customers.seen_count == groups.seen_count == 1
    assert directory._client.detail_ids == ["customer"]
    users = await directory.list_contact_users(limit=1)
    assert [u.userid for u in users] == ["alice"]
    assert (await directory.list_contact_users(offset=1))[0].userid == "bob"
    assert (await directory.get_contact_user("alice")).first_seen_at
    assert await directory.get_contact_user("missing") is None
    customer = await directory.get_customer("customer")
    assert customer.position == "职位" and customer.corp_full_name == "完整公司"
    assert customer.external_profile == {"external_attr": []}
    assert customer.first_seen_at and customer.last_seen_run_id == customers.run_id
    assert len(customer.follows) == 2
    follow = customer.follows[0]
    assert follow.tags == ("detail-tag",)
    assert len(follow.tag_details) == 2 and follow.tag_details[1].tag_id is None
    assert follow.raw_batch_data["tag_id"] == ["old-batch-tag"]
    assert follow.raw_detail_data["remark"] == "详情备注"
    assert follow.remark_mobiles == ("123",) and follow.wechat_channels["source"] == 1
    assert follow.batch_fetched_at and follow.detail_fetched_at and follow.first_seen_at
    tags = await directory.get_customer_follow_tags("customer", "alice")
    assert tags == follow.tag_details
    # 返回对象与持久化数据隔离。
    tags[0].raw_data["tag_name"] = "本地修改"
    assert (await directory.get_customer_follow_tags("customer", "alice"))[0].tag_name == "企业"
    assert await directory.get_customer_follow_tags("missing", "alice") == ()
    group = await directory.get_group_chat("group")
    assert group.owner_userid == group.owner == "alice"
    assert group.follow_status == group.status == 0
    assert group.member_version == "version"
    assert group.members[0].invitor_userid == "alice"
    assert group.admins[0].raw_data["custom"] == "字段"
    assert group.raw_list_data["chat_id"] == "group"
    assert (await directory.get_sync_run(customers.run_id)).status == "succeeded"
    assert await directory.get_sync_run("missing") is None


@pytest.mark.parametrize("failure", ["partial", "detail", "cancel"])
async def test_failed_sync_preserves_old_visibility_and_records_failure(directory, failure):
    await directory.sync_customers_once()
    directory._client.fail = failure
    expected = asyncio.CancelledError if failure == "cancel" else WeComArchiveError
    with pytest.raises(expected):
        await directory.sync_customers_once()
    async with directory._repository._sessions() as session:
        failed = (await session.scalars(select(SyncRun).where(SyncRun.status == "failed"))).one()
        run_id = failed.id
    record = await directory.get_sync_run(run_id)
    assert record.finished_at is not None
    if failure == "partial":
        assert record.failure_details["fail_info"]["unlicensed_userid_list"] == ["bob"]
        assert record.failure_details["stage"] == "customer_batch"
    assert (await directory.get_contact_user("bob")).is_active
    assert (await directory.get_customer("customer")).is_active
    assert len(await directory.get_customer_follow_tags("customer", "bob")) == 2


async def test_successful_empty_scan_deactivates_members_and_customers(directory):
    await directory.sync_customers_once()
    directory._client.empty = True
    result = await directory.sync_customers_once()
    assert result.seen_count == 0
    assert await directory.list_contact_users() == ()
    assert len(await directory.list_contact_users(active_only=False)) == 2
    assert not (await directory.get_customer("customer")).is_active


async def test_local_queries_reject_closed_directory(directory):
    await directory.aclose()
    with pytest.raises(RuntimeError, match="关闭"):
        await directory.list_contact_users()
