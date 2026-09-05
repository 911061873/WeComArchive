from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncGenerator, AsyncIterator, Sequence
from typing import Any, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from .exceptions import ConfigurationError, WeComApiError, WeComTransportError
from .schemas import (
    ApiResponse,
    CustomerStrategy,
    CustomerStrategyCreateResponse,
    CustomerStrategyListResponse,
    CustomerStrategyPrivilege,
    CustomerStrategyRange,
    CustomerStrategyRangeResponse,
    CustomerStrategyResponse,
    CustomerStrategySummary,
    ExternalContactListResponse,
    ExternalContactPageResponse,
    ExternalContactResponse,
    FollowUsersResponse,
    GroupChatDetail,
    GroupChatPageResponse,
    GroupChatResponse,
    GroupChatSummary,
    TokenResponse,
)

_TOKEN_ERROR_CODES = {40014, 42001}
_RETRYABLE_STATUS_CODES = {408, 429, 500, 502, 503, 504}
_ResponseModel = TypeVar("_ResponseModel", bound=BaseModel)


def _chunks(values: Sequence[str], size: int) -> list[list[str]]:
    return [list(values[offset : offset + size]) for offset in range(0, len(values), size)]


class _AsyncRateLimiter:
    """均匀安排请求许可时间，以限制单个客户端的 QPS 上限。"""

    def __init__(self, qps: float) -> None:
        self._interval = 1.0 / qps
        self._next_allowed_at = 0.0
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            now = time.monotonic()
            delay = self._next_allowed_at - now
            if delay > 0:
                await asyncio.sleep(delay)
                now = time.monotonic()
            self._next_allowed_at = max(now, self._next_allowed_at) + self._interval


class _RateLimitedTransport(httpx.AsyncBaseTransport):
    """对每个实际发出的 HTTP 请求应用同一个限流器。"""

    def __init__(self, transport: httpx.AsyncBaseTransport, qps: float) -> None:
        self._transport = transport
        self._limiter = _AsyncRateLimiter(qps)

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        await self._limiter.acquire()
        return await self._transport.handle_async_request(request)

    async def aclose(self) -> None:
        await self._transport.aclose()


class WeComAuth(httpx.Auth):
    """获取仅存于内存的企业微信访问令牌，并将其注入请求。"""

    requires_response_body = True

    def __init__(self, *, corp_id: str, secret: str, base_url: str) -> None:
        if not corp_id.strip():
            raise ConfigurationError("corp_id 不能为空")
        if not secret.strip():
            raise ConfigurationError("secret 不能为空")
        self._corp_id = corp_id
        self._secret = secret
        self._token_url = httpx.URL(base_url).join("/cgi-bin/gettoken")
        self._token: str | None = None
        self._token_expires_at = 0.0
        self._lock = asyncio.Lock()

    async def async_auth_flow(
        self, request: httpx.Request
    ) -> AsyncGenerator[httpx.Request, httpx.Response]:
        token = self._cached_token()
        if token is None:
            async with self._lock:
                token = self._cached_token()
                if token is None:
                    response = yield self._build_token_request(request)
                    token = await self._read_token(response)
        request.url = request.url.copy_set_param("access_token", token)
        yield request

    def invalidate_token(self, expected_token: str | None = None) -> None:
        if expected_token is not None and self._token != expected_token:
            return
        self._token = None
        self._token_expires_at = 0.0

    def redact(self, value: str) -> str:
        for sensitive in (self._secret, self._token):
            if sensitive:
                value = value.replace(sensitive, "[已脱敏]")
        return value

    def _cached_token(self) -> str | None:
        if self._token and time.monotonic() < self._token_expires_at:
            return self._token
        return None

    def _build_token_request(self, original_request: httpx.Request) -> httpx.Request:
        extensions: dict[str, Any] = {}
        if "timeout" in original_request.extensions:
            extensions["timeout"] = original_request.extensions["timeout"]
        return httpx.Request(
            "GET",
            self._token_url,
            params={"corpid": self._corp_id, "corpsecret": self._secret},
            headers={"Accept": "application/json"},
            extensions=extensions,
        )

    async def _read_token(self, response: httpx.Response) -> str:
        try:
            await response.aread()
            response.raise_for_status()
            payload = TokenResponse.model_validate(response.json())
        except (httpx.HTTPError, ValueError, ValidationError):
            raise WeComTransportError("企业微信请求失败：/cgi-bin/gettoken") from None
        if payload.errcode != 0:
            raise WeComApiError(payload.errcode, self.redact(payload.errmsg or "未知错误"))
        if not payload.access_token:
            raise WeComTransportError("企业微信返回了无效的访问令牌响应")
        self._token = payload.access_token
        self._token_expires_at = time.monotonic() + max(0, payload.expires_in - 90)
        return payload.access_token


class WeComCustomerClient:
    """企业微信客户联系 REST API 的异步适配器。"""

    def __init__(
        self,
        *,
        corp_id: str,
        secret: str,
        base_url: str = "https://qyapi.weixin.qq.com",
        timeout: float = 20.0,
        proxy: str | None = None,
        qps: float = 50.0,
        request_concurrency: int = 8,
        max_retries: int = 2,
        retry_backoff: float = 0.5,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if timeout <= 0:
            raise ConfigurationError("timeout 必须大于零")
        if qps <= 0:
            raise ConfigurationError("qps 必须大于零")
        if request_concurrency < 1:
            raise ConfigurationError("request_concurrency 必须至少为 1")
        if max_retries < 0:
            raise ConfigurationError("max_retries 不能为负数")
        if retry_backoff < 0:
            raise ConfigurationError("retry_backoff 不能为负数")
        self._corp_id = corp_id
        self._proxy = proxy
        self._auth = WeComAuth(corp_id=corp_id, secret=secret, base_url=base_url)
        self._max_retries = max_retries
        self._retry_backoff = retry_backoff
        self._request_concurrency = request_concurrency
        self._request_semaphore = asyncio.Semaphore(request_concurrency)
        self._strategy_write_lock = asyncio.Lock()
        base_transport = transport or httpx.AsyncHTTPTransport(proxy=proxy)
        self._http = httpx.AsyncClient(
            base_url=base_url,
            timeout=timeout,
            auth=self._auth,
            transport=_RateLimitedTransport(base_transport, qps),
            headers={"Accept": "application/json"},
        )

    def __repr__(self) -> str:
        return f"{type(self).__name__}(corp_id={self._corp_id!r})"

    async def __aenter__(self) -> WeComCustomerClient:
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._http.aclose()

    async def get_follow_user_ids(self) -> list[str]:
        """返回已配置客户联系功能的成员用户 ID。"""

        response = await self._request(
            "GET", "/cgi-bin/externalcontact/get_follow_user_list", FollowUsersResponse
        )
        return response.follow_user

    async def get_customer_ids(self, userid: str) -> list[str]:
        """获取指定成员添加的客户 ID 列表。"""
        self._validate_id(userid, "userid")
        response = await self._request(
            "GET",
            "/cgi-bin/externalcontact/list",
            ExternalContactListResponse,
            params={"userid": userid},
        )
        return response.external_userid

    async def get_customer_detail_page(
        self, external_userid: str, *, cursor: str = ""
    ) -> ExternalContactResponse:
        """获取客户详情的一页跟进人；通过 next_cursor 继续获取后续跟进人。"""
        self._validate_id(external_userid, "external_userid")
        params = {"external_userid": external_userid}
        if cursor:
            params["cursor"] = cursor
        return await self._request(
            "GET", "/cgi-bin/externalcontact/get", ExternalContactResponse, params=params
        )

    async def iter_customer_detail_pages(
        self, external_userid: str, *, cursor: str = ""
    ) -> AsyncIterator[ExternalContactResponse]:
        """逐页获取单个客户的全部跟进人，保留每页原始响应字段。"""
        seen = {cursor} if cursor else set()
        while True:
            response = await self.get_customer_detail_page(external_userid, cursor=cursor)
            yield response
            cursor = self._next_cursor(response.next_cursor, seen)
            if not cursor:
                break

    async def get_customer_batch_page(
        self, userids: Sequence[str], *, cursor: str = "", limit: int = 100
    ) -> ExternalContactPageResponse:
        """批量获取客户详情的一页，保留游标和 fail_info 部分失败信息。"""
        self._validate_ids(userids, "客户成员分组", 100)
        self._validate_limit(limit, 100)
        body: dict[str, Any] = {"userid_list": list(userids), "limit": limit}
        if cursor:
            body["cursor"] = cursor
        return await self._request(
            "POST",
            "/cgi-bin/externalcontact/batch/get_by_user",
            ExternalContactPageResponse,
            json=body,
        )

    async def iter_customer_batch_pages(
        self, userids: Sequence[str], *, limit: int = 100
    ) -> AsyncIterator[ExternalContactPageResponse]:
        """按页返回完整客户响应，保留游标和部分失败信息供调用方处理。"""

        if not 1 <= len(userids) <= 100:
            raise ConfigurationError("客户成员分组大小必须在 1 到 100 之间")
        if not 1 <= limit <= 100:
            raise ConfigurationError("客户分页大小必须在 1 到 100 之间")
        cursor = ""
        seen_cursors: set[str] = set()
        while True:
            response = await self.get_customer_batch_page(userids, cursor=cursor, limit=limit)
            yield response
            cursor = response.next_cursor
            if not cursor:
                break
            if cursor in seen_cursors:
                raise WeComTransportError("企业微信返回了重复的客户分页游标")
            seen_cursors.add(cursor)

    async def iter_group_chat_summaries(
        self, owner_userids: Sequence[str], *, limit: int = 1000
    ) -> AsyncIterator[GroupChatSummary]:
        """逐个返回由指定成员担任群主的活跃客户群摘要。"""

        if not 1 <= limit <= 1000:
            raise ConfigurationError("客户群分页大小必须在 1 到 1000 之间")
        for userid_list in _chunks(owner_userids, 100):
            cursor = ""
            seen_cursors: set[str] = set()
            while True:
                body: dict[str, Any] = {
                    "status_filter": 0,
                    "owner_filter": {"userid_list": userid_list},
                    "limit": limit,
                }
                if cursor:
                    body["cursor"] = cursor
                response = await self._request(
                    "POST",
                    "/cgi-bin/externalcontact/groupchat/list",
                    GroupChatPageResponse,
                    json=body,
                )
                for item in response.group_chat_list:
                    yield item
                cursor = response.next_cursor
                if not cursor:
                    break
                if cursor in seen_cursors:
                    raise WeComTransportError("企业微信返回了重复的客户群分页游标")
                seen_cursors.add(cursor)

    async def get_group_chat_detail(self, chat_id: str) -> GroupChatDetail:
        """返回指定客户群的详情，其中包含群成员名称。"""

        response = await self._request(
            "POST",
            "/cgi-bin/externalcontact/groupchat/get",
            GroupChatResponse,
            json={"chat_id": chat_id, "need_name": 1},
        )
        return response.group_chat

    async def update_customer_remark(
        self,
        userid: str,
        external_userid: str,
        *,
        remark: str | None = None,
        description: str | None = None,
        remark_company: str | None = None,
        remark_mobiles: Sequence[str] | None = None,
        remark_pic_mediaid: str | None = None,
    ) -> None:
        """修改指定成员为客户设置的备注信息。"""

        self._validate_id(userid, "userid")
        self._validate_id(external_userid, "external_userid")
        if all(
            value is None
            for value in (
                remark,
                description,
                remark_company,
                remark_mobiles,
                remark_pic_mediaid,
            )
        ):
            raise ConfigurationError("至少需要提供一项客户备注信息")
        if remark is not None and len(remark) > 20:
            raise ConfigurationError("remark 最多包含 20 个字符")
        if description is not None and len(description) > 150:
            raise ConfigurationError("description 最多包含 150 个字符")
        if remark_company is not None and len(remark_company) > 20:
            raise ConfigurationError("remark_company 最多包含 20 个字符")
        if remark_mobiles is not None and (
            isinstance(remark_mobiles, (str, bytes))
            or any(not isinstance(value, str) for value in remark_mobiles)
        ):
            raise ConfigurationError("remark_mobiles 必须为字符串序列")

        body: dict[str, Any] = {
            "userid": userid,
            "external_userid": external_userid,
        }
        optional_fields: tuple[tuple[str, Any], ...] = (
            ("remark", remark),
            ("description", description),
            ("remark_company", remark_company),
            (
                "remark_mobiles",
                list(remark_mobiles) if remark_mobiles is not None else None,
            ),
            ("remark_pic_mediaid", remark_pic_mediaid),
        )
        body.update((name, value) for name, value in optional_fields if value is not None)

        await self._request(
            "POST",
            "/cgi-bin/externalcontact/remark",
            ApiResponse,
            json=body,
        )

    async def get_customer_strategy_list_page(
        self, *, cursor: str = "", limit: int = 1000
    ) -> CustomerStrategyListResponse:
        """获取规则组 ID 列表的一页。"""
        self._validate_limit(limit, 1000)
        body: dict[str, Any] = {"limit": limit}
        if cursor:
            body["cursor"] = cursor
        return await self._request(
            "POST",
            "/cgi-bin/externalcontact/customer_strategy/list",
            CustomerStrategyListResponse,
            json=body,
        )

    async def iter_customer_strategy_summaries(
        self, *, cursor: str = "", limit: int = 1000
    ) -> AsyncIterator[CustomerStrategySummary]:
        """遍历应用可管理的规则组 ID。"""
        seen = {cursor} if cursor else set()
        while True:
            response = await self.get_customer_strategy_list_page(cursor=cursor, limit=limit)
            for item in response.strategy:
                yield item
            cursor = self._next_cursor(response.next_cursor, seen)
            if not cursor:
                break

    async def get_customer_strategy_detail(self, strategy_id: int) -> CustomerStrategy:
        """获取规则组详情。"""
        self._validate_strategy_id(strategy_id)
        response = await self._request(
            "POST",
            "/cgi-bin/externalcontact/customer_strategy/get",
            CustomerStrategyResponse,
            json={"strategy_id": strategy_id},
        )
        return response.strategy

    async def get_customer_strategy_range_page(
        self, strategy_id: int, *, cursor: str = "", limit: int = 1000
    ) -> CustomerStrategyRangeResponse:
        """获取规则组管理范围的一页成员和部门节点。"""
        self._validate_strategy_id(strategy_id)
        self._validate_limit(limit, 1000)
        body: dict[str, Any] = {"strategy_id": strategy_id, "limit": limit}
        if cursor:
            body["cursor"] = cursor
        return await self._request(
            "POST",
            "/cgi-bin/externalcontact/customer_strategy/get_range",
            CustomerStrategyRangeResponse,
            json=body,
        )

    async def iter_customer_strategy_range_nodes(
        self, strategy_id: int, *, cursor: str = "", limit: int = 1000
    ) -> AsyncIterator[CustomerStrategyRange]:
        """遍历规则组管理的所有成员和部门节点。"""
        seen = {cursor} if cursor else set()
        while True:
            response = await self.get_customer_strategy_range_page(
                strategy_id, cursor=cursor, limit=limit
            )
            for item in response.range:
                yield item
            cursor = self._next_cursor(response.next_cursor, seen)
            if not cursor:
                break

    async def create_customer_strategy(
        self,
        strategy_name: str,
        admin_list: Sequence[str],
        *,
        range: Sequence[CustomerStrategyRange],
        parent_id: int | None = None,
        privilege: CustomerStrategyPrivilege | None = None,
    ) -> int:
        """串行创建规则组；结果不确定时不自动重发，成功返回规则组 ID。"""
        self._validate_id(strategy_name, "strategy_name")
        self._validate_ids(admin_list, "admin_list", 20)
        body: dict[str, Any] = {
            "strategy_name": strategy_name,
            "admin_list": list(admin_list),
            "range": self._serialize_strategy_range(range),
        }
        if parent_id is not None:
            self._validate_strategy_id(parent_id, allow_zero=True)
            body["parent_id"] = parent_id
        if privilege is not None:
            body["privilege"] = privilege.model_dump(exclude_none=True)
        async with self._strategy_write_lock:
            response = await self._request(
                "POST",
                "/cgi-bin/externalcontact/customer_strategy/create",
                CustomerStrategyCreateResponse,
                json=body,
                retry_safe=False,
            )
        return response.strategy_id

    async def update_customer_strategy(
        self,
        strategy_id: int,
        *,
        strategy_name: str | None = None,
        admin_list: Sequence[str] | None = None,
        privilege: CustomerStrategyPrivilege | None = None,
        range_add: Sequence[CustomerStrategyRange] | None = None,
        range_del: Sequence[CustomerStrategyRange] | None = None,
    ) -> None:
        """串行编辑规则组；管理员及权限按官方覆盖语义发送。"""
        self._validate_strategy_id(strategy_id)
        body: dict[str, Any] = {"strategy_id": strategy_id}
        if strategy_name is not None:
            self._validate_id(strategy_name, "strategy_name")
            body["strategy_name"] = strategy_name
        if admin_list is not None:
            self._validate_ids(admin_list, "admin_list", 20, allow_empty=True)
            body["admin_list"] = list(admin_list)
        if privilege is not None:
            body["privilege"] = privilege.model_dump(exclude_none=True)
        if range_add is not None:
            body["range_add"] = self._serialize_strategy_range(range_add)
        if range_del is not None:
            body["range_del"] = self._serialize_strategy_range(range_del)
        if len(body.get("range_add", [])) + len(body.get("range_del", [])) > 100:
            raise ConfigurationError("单次最多可配置 100 个管理节点")
        if len(body) == 1:
            raise ConfigurationError("至少需要提供一项规则组更新信息")
        async with self._strategy_write_lock:
            await self._request(
                "POST",
                "/cgi-bin/externalcontact/customer_strategy/edit",
                ApiResponse,
                json=body,
                retry_safe=False,
            )

    async def delete_customer_strategy(self, strategy_id: int) -> None:
        """删除规则组，成功时返回 None。"""
        self._validate_strategy_id(strategy_id)
        async with self._strategy_write_lock:
            await self._request(
                "POST",
                "/cgi-bin/externalcontact/customer_strategy/del",
                ApiResponse,
                json={"strategy_id": strategy_id},
                retry_safe=False,
            )

    @staticmethod
    def _validate_id(value: str, name: str) -> None:
        if not isinstance(value, str) or not value.strip():
            raise ConfigurationError(f"{name} 不能为空")

    @classmethod
    def _validate_ids(
        cls, values: Sequence[str], name: str, maximum: int, *, allow_empty: bool = False
    ) -> None:
        minimum = 0 if allow_empty else 1
        if isinstance(values, (str, bytes)) or not minimum <= len(values) <= maximum:
            raise ConfigurationError(f"{name}大小必须在 {minimum} 到 {maximum} 之间")
        for value in values:
            cls._validate_id(value, name)

    @staticmethod
    def _validate_limit(limit: int, maximum: int) -> None:
        if type(limit) is not int or not 1 <= limit <= maximum:
            raise ConfigurationError(f"分页大小必须为 1 到 {maximum} 之间的整数")

    @staticmethod
    def _validate_strategy_id(strategy_id: int, *, allow_zero: bool = False) -> None:
        if type(strategy_id) is not int or strategy_id < (0 if allow_zero else 1):
            raise ConfigurationError("规则组 ID 必须为有效整数")

    @classmethod
    def _serialize_strategy_range(
        cls, nodes: Sequence[CustomerStrategyRange]
    ) -> list[dict[str, Any]]:
        if len(nodes) > 100:
            raise ConfigurationError("单次最多可配置 100 个管理节点")
        result = []
        for node in nodes:
            if node.type == 1:
                cls._validate_id(node.userid, "userid")
            result.append(node.model_dump(exclude_none=True))
        return result

    @staticmethod
    def _next_cursor(cursor: str, seen: set[str]) -> str:
        if cursor:
            if cursor in seen:
                raise WeComTransportError("企业微信返回了重复的分页游标")
            seen.add(cursor)
        return cursor

    async def _request(
        self,
        method: str,
        path: str,
        response_model: type[_ResponseModel],
        *,
        json: dict[str, Any] | None = None,
        params: dict[str, str] | None = None,
        retry_safe: bool = True,
    ) -> _ResponseModel:
        for attempt in range(self._max_retries + 1):
            try:
                async with self._request_semaphore:
                    response = await self._http.request(method, path, json=json, params=params)
                if (
                    retry_safe
                    and response.status_code in _RETRYABLE_STATUS_CODES
                    and attempt < self._max_retries
                ):
                    await self._backoff(attempt)
                    continue
                response.raise_for_status()
                raw_payload = response.json()
                api_response = ApiResponse.model_validate(raw_payload)
                if api_response.errcode in _TOKEN_ERROR_CODES and attempt < self._max_retries:
                    self._auth.invalidate_token(response.request.url.params.get("access_token"))
                    await self._backoff(attempt)
                    continue
                self._raise_for_api_error(api_response)
                return response_model.model_validate(raw_payload)
            except httpx.TransportError:
                if retry_safe and attempt < self._max_retries:
                    await self._backoff(attempt)
                    continue
                raise WeComTransportError(f"企业微信请求失败：{path}") from None
            except WeComTransportError:
                if retry_safe and attempt < self._max_retries:
                    await self._backoff(attempt)
                    continue
                raise
            except (httpx.HTTPError, ValueError, ValidationError):
                raise WeComTransportError(f"企业微信请求失败：{path}") from None
        raise AssertionError("不应执行到此处")

    async def _backoff(self, attempt: int) -> None:
        if self._retry_backoff:
            await asyncio.sleep(self._retry_backoff * (2**attempt))

    def _raise_for_api_error(self, payload: ApiResponse) -> None:
        if payload.errcode == 0:
            return
        raw_message = self._auth.redact((payload.errmsg or "未知错误")[:300])
        if self._proxy:
            raw_message = raw_message.replace(self._proxy, "[已脱敏]")
        raise WeComApiError(payload.errcode, raw_message)
