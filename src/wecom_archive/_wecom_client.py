from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncGenerator, AsyncIterator, Sequence
from typing import Any, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from ._schemas import (
    ApiResponse,
    CustomerDetailItem,
    CustomerPageResponse,
    FollowUsersResponse,
    GroupChatDetail,
    GroupChatPageResponse,
    GroupChatResponse,
    GroupChatSummary,
    TokenResponse,
)
from .exceptions import ConfigurationError, WeComApiError, WeComTransportError

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

    async def get_follow_users(self) -> list[str]:
        """返回已配置客户联系功能的成员用户 ID。"""

        response = await self._request(
            "GET", "/cgi-bin/externalcontact/get_follow_user_list", FollowUsersResponse
        )
        return response.follow_user

    async def iter_customer_details(
        self, userids: Sequence[str], *, limit: int = 100
    ) -> AsyncIterator[list[CustomerDetailItem]]:
        """按页返回一个成员分组所跟进客户的详情。"""

        if not 1 <= len(userids) <= 100:
            raise ConfigurationError("客户成员分组大小必须在 1 到 100 之间")
        if not 1 <= limit <= 100:
            raise ConfigurationError("客户分页大小必须在 1 到 100 之间")
        cursor = ""
        seen_cursors: set[str] = set()
        while True:
            body: dict[str, Any] = {"userid_list": list(userids), "limit": limit}
            if cursor:
                body["cursor"] = cursor
            response = await self._request(
                "POST",
                "/cgi-bin/externalcontact/batch/get_by_user",
                CustomerPageResponse,
                json=body,
            )
            yield response.external_contact_list
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

    async def get_group_chat(self, chat_id: str) -> GroupChatDetail:
        """返回指定客户群的详情，其中包含群成员名称。"""

        response = await self._request(
            "POST",
            "/cgi-bin/externalcontact/groupchat/get",
            GroupChatResponse,
            json={"chat_id": chat_id, "need_name": 1},
        )
        return response.group_chat

    async def _request(
        self,
        method: str,
        path: str,
        response_model: type[_ResponseModel],
        *,
        json: dict[str, Any] | None = None,
    ) -> _ResponseModel:
        for attempt in range(self._max_retries + 1):
            try:
                async with self._request_semaphore:
                    response = await self._http.request(method, path, json=json)
                if response.status_code in _RETRYABLE_STATUS_CODES and attempt < self._max_retries:
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
                if attempt < self._max_retries:
                    await self._backoff(attempt)
                    continue
                raise WeComTransportError(f"企业微信请求失败：{path}") from None
            except WeComTransportError:
                if attempt < self._max_retries:
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
