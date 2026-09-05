from __future__ import annotations

import os
from pathlib import Path

import pytest

from wecom_archive import CustomerContactDirectory
from wecom_archive.customer_sync.client import WeComCustomerClient


def _required_environment() -> tuple[str, str]:
    corp_id = os.environ.get("WECOM_CORP_ID")
    secret = os.environ.get("WECOM_CUSTOMER_CONTACT_SECRET")
    if not corp_id or not secret:
        pytest.skip("企业微信真实集成测试需要配置 WECOM_CORP_ID 和 WECOM_CUSTOMER_CONTACT_SECRET")
    return corp_id, secret


def _open_directory(data_dir: Path) -> CustomerContactDirectory:
    corp_id, secret = _required_environment()
    return CustomerContactDirectory(
        corp_id=corp_id,
        secret=secret,
        proxy=os.environ.get("WECOM_HTTP_PROXY"),
        data_dir=data_dir,
    )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_real_customer_and_group_chat_sync_is_repeatable() -> None:
    """针对已配置的非生产企业执行两次完整扫描。"""
    data_dir = Path.cwd() / ".integration-test-data"
    async with _open_directory(data_dir) as directory:
        first_customers, first_groups = await directory.sync_all_once()
        second_customers, second_groups = await directory.sync_all_once()

        assert first_customers.domain == second_customers.domain == "customers"
        assert first_groups.domain == second_groups.domain == "group_chats"
        assert first_customers.seen_count >= 0
        assert first_groups.seen_count >= 0
        assert second_customers.seen_count >= 0
        assert second_groups.seen_count >= 0

        expected_customer = os.environ.get("WECOM_TEST_EXTERNAL_USERID")
        if expected_customer:
            customer = await directory.get_customer(expected_customer)
            assert customer is not None
            assert customer.is_active is True

        expected_group = os.environ.get("WECOM_TEST_GROUP_CHAT_ID")
        if expected_group:
            group_chat = await directory.get_group_chat(expected_group)
            assert group_chat is not None
            assert group_chat.is_active is True

    # 重新打开资料目录，以验证包内 Alembic 迁移可在同一数据库上重复执行。
    async with _open_directory(data_dir) as reopened:
        expected_customer = os.environ.get("WECOM_TEST_EXTERNAL_USERID")
        if expected_customer:
            assert await reopened.get_customer(expected_customer) is not None


@pytest.mark.integration
@pytest.mark.asyncio
async def test_real_customer_client_supports_async_requests() -> None:
    """验证 AsyncClient 鉴权以及一个客户联系接口。"""
    corp_id, secret = _required_environment()

    async with WeComCustomerClient(
        corp_id=corp_id,
        secret=secret,
        proxy=os.environ.get("WECOM_HTTP_PROXY"),
    ) as client:
        follow_users = await client.get_follow_user_ids()
        assert all(isinstance(userid, str) for userid in follow_users)
