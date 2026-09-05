from __future__ import annotations

import asyncio
import json

import httpx
import pytest
from pydantic import ValidationError

from wecom_archive.customer_sync.client import WeComCustomerClient
from wecom_archive.customer_sync.exceptions import (
    ConfigurationError,
    WeComApiError,
    WeComTransportError,
)
from wecom_archive.customer_sync.schemas import (
    CustomerStrategyParty,
    CustomerStrategyPrivilege,
    CustomerStrategyUser,
)


def make_client(handler):
    async def authenticated(request):
        if request.url.path == "/cgi-bin/gettoken":
            return httpx.Response(200, json={"access_token": "test-token"})
        assert request.url.params["access_token"] == "test-token"
        return await handler(request)

    return WeComCustomerClient(
        corp_id="test-corp",
        secret="test-secret",
        qps=100_000,
        retry_backoff=0,
        transport=httpx.MockTransport(authenticated),
    )


async def test_customer_get_query_encoding_and_follow_pagination():
    requests = []

    async def handler(request):
        requests.append(request)
        assert request.method == "GET"
        assert not request.content
        if request.url.path.endswith("/list"):
            assert request.url.params["userid"] == "alice&中文"
            return httpx.Response(200, json={"external_userid": ["customer&1"]})
        assert request.url.path == "/cgi-bin/externalcontact/get"
        assert request.url.params["external_userid"] == "customer&1"
        page = 2 if "cursor" in request.url.params else 1
        if page == 2:
            assert request.url.params["cursor"] == "next+/=&"
        return httpx.Response(
            200,
            json={
                "external_contact": {"external_userid": "customer&1"},
                "follow_user": [{"userid": f"user-{page}"}],
                "next_cursor": "next+/=&" if page == 1 else "",
                "future_field": "保留",
            },
        )

    async with make_client(handler) as client:
        assert await client.get_customer_ids("alice&中文") == ["customer&1"]
        pages = [p async for p in client.iter_customer_detail_pages("customer&1")]
    assert [p.follow_user[0].userid for p in pages] == ["user-1", "user-2"]
    assert pages[0].model_dump()["future_field"] == "保留"
    assert len(requests) == 3


async def test_batch_iterator_preserves_partial_failure_and_continues_pagination():
    async def handler(request):
        assert request.url.path == "/cgi-bin/externalcontact/batch/get_by_user"
        assert json.loads(request.content)["userid_list"] == ["alice"]
        return httpx.Response(
            200,
            json={
                "external_contact_list": [
                    {
                        "external_contact": {"external_userid": "customer"},
                        "follow_info": {
                            "userid": "alice",
                            "wechat_channels": {
                                "nickname": "视频号",
                                "source": 1,
                            },
                        },
                    }
                ],
                "fail_info": {"unlicensed_userid_list": ["alice"]},
                "next_cursor": ""
                if json.loads(request.content).get("cursor") == "next"
                else "next",
            },
        )

    async with make_client(handler) as client:
        response = await client.get_customer_batch_page(["alice"], cursor="resume", limit=50)
        assert response.fail_info.unlicensed_userid_list == ["alice"]
        assert response.external_contact_list[0].follow_info.wechat_channels.source == 1
        pages = [p async for p in client.iter_customer_batch_pages(["alice"])]
        assert len(pages) == 2
        assert pages[0].next_cursor == "next"
        assert pages[1].next_cursor == ""
        assert all(p.fail_info.unlicensed_userid_list == ["alice"] for p in pages)
        assert all(
            p.external_contact_list[0].external_contact.external_userid == "customer" for p in pages
        )


async def test_strategy_endpoints_and_optional_field_semantics():
    requests = []

    async def handler(request):
        assert request.method == "POST"
        path = request.url.path
        body = json.loads(request.content)
        requests.append((path.rsplit("/", 1)[-1], body))
        if path.endswith("/list"):
            return httpx.Response(200, json={"strategy": [{"strategy_id": 7}]})
        if path.endswith("/get_range"):
            return httpx.Response(
                200,
                json={
                    "range": [
                        {"type": 1, "userid": "alice"},
                        {"type": 2, "partyid": 2},
                    ]
                },
            )
        if path.endswith("/get"):
            return httpx.Response(
                200,
                json={
                    "strategy": {
                        "strategy_id": 7,
                        "strategy_name": "组",
                        "create_time": 1,
                        "privilege": {"share_customer": False},
                    }
                },
            )
        return httpx.Response(200, json={"errcode": 0, "strategy_id": 7})

    async with make_client(handler) as client:
        assert (await client.get_customer_strategy_list_page(cursor="next", limit=10)).strategy[
            0
        ].strategy_id == 7
        assert (await client.get_customer_strategy_detail(7)).privilege.share_customer is False
        nodes = (await client.get_customer_strategy_range_page(7, cursor="next", limit=10)).range
        assert isinstance(nodes[0], CustomerStrategyUser)
        assert isinstance(nodes[1], CustomerStrategyParty)
        assert (
            await client.create_customer_strategy(
                "组",
                ["alice"],
                range=nodes,
                parent_id=0,
                privilege=CustomerStrategyPrivilege(share_customer=False),
            )
            == 7
        )
        assert (
            await client.update_customer_strategy(
                7,
                strategy_name="新组",
                admin_list=[],
                privilege=CustomerStrategyPrivilege(send_group_msg=False),
                range_add=[nodes[0]],
                range_del=[nodes[1]],
            )
            is None
        )
        assert await client.delete_customer_strategy(7) is None
    assert requests == [
        ("list", {"cursor": "next", "limit": 10}),
        ("get", {"strategy_id": 7}),
        ("get_range", {"strategy_id": 7, "cursor": "next", "limit": 10}),
        (
            "create",
            {
                "strategy_name": "组",
                "admin_list": ["alice"],
                "range": [
                    {"type": 1, "userid": "alice"},
                    {"type": 2, "partyid": 2},
                ],
                "parent_id": 0,
                "privilege": {"share_customer": False},
            },
        ),
        (
            "edit",
            {
                "strategy_id": 7,
                "strategy_name": "新组",
                "admin_list": [],
                "privilege": {"send_group_msg": False},
                "range_add": [{"type": 1, "userid": "alice"}],
                "range_del": [{"type": 2, "partyid": 2}],
            },
        ),
        ("del", {"strategy_id": 7}),
    ]


@pytest.mark.parametrize("kind", ["follow", "list", "range", "batch"])
async def test_iterators_reject_repeated_cursors(kind):
    calls = 0

    async def handler(request):
        nonlocal calls
        calls += 1
        return httpx.Response(
            200,
            json={
                "external_contact": {"external_userid": "customer"},
                "next_cursor": "repeated",
            },
        )

    async with make_client(handler) as client:
        iterator = {
            "follow": lambda: client.iter_customer_detail_pages("customer"),
            "list": lambda: client.iter_customer_strategy_summaries(),
            "range": lambda: client.iter_customer_strategy_range_nodes(7),
            "batch": lambda: client.iter_customer_batch_pages(["alice"]),
        }[kind]()
        with pytest.raises(WeComTransportError, match="重复"):
            _ = [item async for item in iterator]
    assert calls == 2


@pytest.mark.parametrize("kind", ["list", "range"])
async def test_strategy_iterators_resume_and_finish(kind):
    bodies = []

    async def handler(request):
        bodies.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "strategy": [{"strategy_id": len(bodies)}],
                "range": [{"type": 2, "partyid": len(bodies)}],
                "next_cursor": "last" if len(bodies) == 1 else "",
            },
        )

    async with make_client(handler) as client:
        iterator = (
            client.iter_customer_strategy_summaries(cursor="resume", limit=1)
            if kind == "list"
            else client.iter_customer_strategy_range_nodes(7, cursor="resume", limit=1)
        )
        items = [item async for item in iterator]
    assert len(items) == 2
    assert [body["cursor"] for body in bodies] == ["resume", "last"]


@pytest.mark.parametrize("failure", ["timeout", "http", "shape"])
async def test_strategy_creation_does_not_retry_uncertain_outcome(failure):
    calls = 0

    async def handler(request):
        nonlocal calls
        calls += 1
        if failure == "timeout":
            raise httpx.ReadTimeout("结果不确定", request=request)
        return httpx.Response(503 if failure == "http" else 200, json={"errcode": 0})

    async with make_client(handler) as client:
        with pytest.raises(WeComTransportError):
            await client.create_customer_strategy("组", ["alice"], range=[])
    assert calls == 1


async def test_strategy_creation_and_edit_are_serialized():
    active = peak = 0

    async def handler(request):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.01)
        active -= 1
        return httpx.Response(200, json={"strategy_id": 7})

    async with make_client(handler) as client:
        await asyncio.gather(
            client.create_customer_strategy("组", ["alice"], range=[]),
            client.update_customer_strategy(7, strategy_name="组2"),
            client.create_customer_strategy("组3", ["alice"], range=[]),
        )
    assert peak == 1


@pytest.mark.parametrize(
    "method,args,kwargs",
    [
        ("get_customer_ids", (" ",), {}),
        ("get_customer_detail_page", ("",), {}),
        ("get_customer_batch_page", ("alice",), {}),
        ("get_customer_batch_page", ([" "],), {}),
        ("get_customer_batch_page", (["alice"],), {"limit": 1.5}),
        ("get_customer_batch_page", (["alice"] * 101,), {}),
        ("get_customer_strategy_list_page", (), {"limit": 1001}),
        ("get_customer_strategy_range_page", (1,), {"limit": 0}),
        ("get_customer_strategy_detail", (True,), {}),
        ("delete_customer_strategy", (-1,), {}),
        ("update_customer_strategy", (1,), {}),
        ("update_customer_strategy", (1,), {"admin_list": ["a"] * 21}),
        ("create_customer_strategy", ("", ["a"]), {"range": []}),
        ("create_customer_strategy", ("组", []), {"range": []}),
        ("create_customer_strategy", ("组", ["a"]), {"range": [], "parent_id": -1}),
        (
            "create_customer_strategy",
            ("组", ["a"]),
            {
                "range": [CustomerStrategyParty(partyid=1)] * 101,
            },
        ),
        (
            "update_customer_strategy",
            (1,),
            {
                "range_add": [CustomerStrategyParty(partyid=1)] * 60,
                "range_del": [CustomerStrategyParty(partyid=2)] * 60,
            },
        ),
    ],
)
async def test_invalid_input_fails_before_network(method, args, kwargs):
    async def handler(request):
        pytest.fail("无效参数不应发出网络请求")

    async with make_client(handler) as client:
        with pytest.raises(ConfigurationError):
            await getattr(client, method)(*args, **kwargs)


def test_strategy_models_validate_nodes_and_basic_privileges():
    with pytest.raises(ValidationError):
        CustomerStrategyParty(partyid=0)
    with pytest.raises(ValidationError):
        CustomerStrategyUser(userid="")
    with pytest.raises(ValidationError):
        CustomerStrategyPrivilege(view_customer_list=False)
    assert CustomerStrategyPrivilege(share_customer=False).model_dump(exclude_none=True) == {
        "share_customer": False
    }


@pytest.mark.parametrize("payload", [{"errcode": 40003}, {"external_contact": []}])
async def test_customer_detail_maps_api_and_schema_errors(payload):
    async def handler(request):
        return httpx.Response(200, json=payload)

    async with make_client(handler) as client:
        expected = WeComApiError if "errcode" in payload else WeComTransportError
        with pytest.raises(expected):
            await client.get_customer_detail_page("customer")


async def test_get_query_is_preserved_when_token_expires():
    calls = []

    async def handler(request):
        calls.append(dict(request.url.params))
        if len(calls) == 1:
            return httpx.Response(200, json={"errcode": 42001})
        return httpx.Response(200, json={"external_contact": {"external_userid": "c&1"}})

    async with make_client(handler) as client:
        await client.get_customer_detail_page("c&1", cursor="+/=&")
    assert len(calls) == 2
    assert all(c["external_userid"] == "c&1" and c["cursor"] == "+/=&" for c in calls)


async def test_remark_rejects_phone_string_instead_of_sequence():
    async def handler(request):
        pytest.fail("手机号字符串不应拆分为单个字符发送")

    async with make_client(handler) as client:
        with pytest.raises(ConfigurationError):
            await client.update_customer_remark("alice", "customer", remark_mobiles="123")
