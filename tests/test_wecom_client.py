from __future__ import annotations

import json
import time

import httpx
import pytest

from wecom_archive.customer_sync.client import WeComCustomerClient
from wecom_archive.customer_sync.exceptions import ConfigurationError, WeComTransportError


@pytest.mark.asyncio
async def test_qps_limit_counts_token_and_business_requests() -> None:
    request_times: list[float] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        request_times.append(time.monotonic())
        if request.url.path == "/cgi-bin/gettoken":
            return httpx.Response(
                200, json={"errcode": 0, "access_token": "token", "expires_in": 7200}
            )
        return httpx.Response(200, json={"errcode": 0, "follow_user": []})

    async with WeComCustomerClient(
        corp_id="corp",
        secret="secret",
        qps=20,
        transport=httpx.MockTransport(handler),
    ) as client:
        await client.get_follow_users()

    assert len(request_times) == 2
    assert request_times[1] - request_times[0] >= 0.045


@pytest.mark.asyncio
async def test_client_refreshes_token_and_retries_business_request() -> None:
    token_calls = 0
    business_tokens: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal token_calls
        if request.url.path == "/cgi-bin/gettoken":
            token_calls += 1
            return httpx.Response(
                200,
                json={"errcode": 0, "access_token": f"token-{token_calls}", "expires_in": 7200},
            )
        business_tokens.append(request.url.params["access_token"])
        if len(business_tokens) == 1:
            return httpx.Response(200, json={"errcode": 42001, "errmsg": "expired"})
        return httpx.Response(200, json={"errcode": 0, "follow_user": ["alice"]})

    async with WeComCustomerClient(
        corp_id="corp",
        secret="secret",
        transport=httpx.MockTransport(handler),
        retry_backoff=0,
    ) as client:
        assert await client.get_follow_users() == ["alice"]

    assert token_calls == 2
    assert business_tokens == ["token-1", "token-2"]


@pytest.mark.asyncio
async def test_client_retries_retryable_http_status() -> None:
    business_calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal business_calls
        if request.url.path == "/cgi-bin/gettoken":
            return httpx.Response(
                200, json={"errcode": 0, "access_token": "token", "expires_in": 7200}
            )
        business_calls += 1
        if business_calls == 1:
            return httpx.Response(503, json={"errcode": 0})
        return httpx.Response(200, json={"errcode": 0, "follow_user": []})

    async with WeComCustomerClient(
        corp_id="corp",
        secret="secret",
        transport=httpx.MockTransport(handler),
        retry_backoff=0,
    ) as client:
        assert await client.get_follow_users() == []

    assert business_calls == 2


@pytest.mark.asyncio
async def test_client_rejects_invalid_response_shape() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/cgi-bin/gettoken":
            return httpx.Response(
                200, json={"errcode": 0, "access_token": "token", "expires_in": 7200}
            )
        return httpx.Response(200, json={"errcode": 0, "follow_user": "not-a-list"})

    async with WeComCustomerClient(
        corp_id="corp",
        secret="secret",
        transport=httpx.MockTransport(handler),
        retry_backoff=0,
    ) as client:
        with pytest.raises(WeComTransportError):
            await client.get_follow_users()


@pytest.mark.asyncio
async def test_customer_details_are_yielded_one_page_at_a_time() -> None:
    request_bodies: list[dict[str, object]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/cgi-bin/gettoken":
            return httpx.Response(
                200, json={"errcode": 0, "access_token": "token", "expires_in": 7200}
            )
        body = json.loads(request.content)
        request_bodies.append(body)
        page = len(request_bodies)
        return httpx.Response(
            200,
            json={
                "errcode": 0,
                "external_contact_list": [
                    {
                        "external_contact": {"external_userid": f"customer-{page}"},
                        "follow_info": {"userid": "alice"},
                    }
                ],
                "next_cursor": "next-page" if page == 1 else "",
            },
        )

    async with WeComCustomerClient(
        corp_id="corp",
        secret="secret",
        qps=10_000,
        transport=httpx.MockTransport(handler),
    ) as client:
        pages = [page async for page in client.iter_customer_details(["alice"])]

    assert [page[0].external_contact.external_userid for page in pages] == [
        "customer-1",
        "customer-2",
    ]
    assert request_bodies == [
        {"userid_list": ["alice"], "limit": 100},
        {"userid_list": ["alice"], "limit": 100, "cursor": "next-page"},
    ]


@pytest.mark.asyncio
async def test_customer_details_reject_invalid_user_group_size() -> None:
    async with WeComCustomerClient(
        corp_id="corp",
        secret="secret",
        transport=httpx.MockTransport(lambda _: httpx.Response(500)),
    ) as client:
        with pytest.raises(ConfigurationError):
            _ = [page async for page in client.iter_customer_details([])]
        with pytest.raises(ConfigurationError):
            _ = [page async for page in client.iter_customer_details([str(i) for i in range(101)])]


@pytest.mark.asyncio
async def test_update_customer_remark_sends_all_supported_fields() -> None:
    request_body: dict[str, object] | None = None

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal request_body
        if request.url.path == "/cgi-bin/gettoken":
            return httpx.Response(
                200, json={"errcode": 0, "access_token": "token", "expires_in": 7200}
            )
        assert request.method == "POST"
        assert request.url.path == "/cgi-bin/externalcontact/remark"
        request_body = json.loads(request.content)
        return httpx.Response(200, json={"errcode": 0, "errmsg": "ok"})

    async with WeComCustomerClient(
        corp_id="corp",
        secret="secret",
        qps=10_000,
        transport=httpx.MockTransport(handler),
    ) as client:
        result = await client.update_customer_remark(
            "alice",
            "customer-1",
            remark="重要客户",
            description="来自官网",
            remark_company="示例科技",
            remark_mobiles=["13800000001", "13800000002"],
            remark_pic_mediaid="media-id",
        )

    assert result is None
    assert request_body == {
        "userid": "alice",
        "external_userid": "customer-1",
        "remark": "重要客户",
        "description": "来自官网",
        "remark_company": "示例科技",
        "remark_mobiles": ["13800000001", "13800000002"],
        "remark_pic_mediaid": "media-id",
    }


@pytest.mark.asyncio
async def test_update_customer_remark_preserves_empty_values_for_clearing() -> None:
    request_body: dict[str, object] | None = None

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal request_body
        if request.url.path == "/cgi-bin/gettoken":
            return httpx.Response(
                200, json={"errcode": 0, "access_token": "token", "expires_in": 7200}
            )
        request_body = json.loads(request.content)
        return httpx.Response(200, json={"errcode": 0})

    async with WeComCustomerClient(
        corp_id="corp",
        secret="secret",
        qps=10_000,
        transport=httpx.MockTransport(handler),
    ) as client:
        await client.update_customer_remark(
            "alice",
            "customer-1",
            remark="",
            remark_mobiles=[""],
        )

    assert request_body == {
        "userid": "alice",
        "external_userid": "customer-1",
        "remark": "",
        "remark_mobiles": [""],
    }


@pytest.mark.asyncio
async def test_update_customer_remark_validates_required_updates_and_lengths() -> None:
    async with WeComCustomerClient(
        corp_id="corp",
        secret="secret",
        transport=httpx.MockTransport(lambda _: httpx.Response(500)),
    ) as client:
        with pytest.raises(ConfigurationError, match="至少需要提供"):
            await client.update_customer_remark("alice", "customer-1")
        with pytest.raises(ConfigurationError, match="remark 最多"):
            await client.update_customer_remark("alice", "customer-1", remark="x" * 21)
        with pytest.raises(ConfigurationError, match="description 最多"):
            await client.update_customer_remark("alice", "customer-1", description="x" * 151)
        with pytest.raises(ConfigurationError, match="remark_company 最多"):
            await client.update_customer_remark("alice", "customer-1", remark_company="x" * 21)
