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
    """Space acquisitions evenly to enforce a per-client QPS ceiling."""

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
    """Apply one shared limit to every actual outbound HTTP request."""

    def __init__(self, transport: httpx.AsyncBaseTransport, qps: float) -> None:
        self._transport = transport
        self._limiter = _AsyncRateLimiter(qps)

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        await self._limiter.acquire()
        return await self._transport.handle_async_request(request)

    async def aclose(self) -> None:
        await self._transport.aclose()


class WeComAuth(httpx.Auth):
    """Acquire and inject an in-memory WeCom access token."""

    requires_response_body = True

    def __init__(self, *, corp_id: str, secret: str, base_url: str) -> None:
        if not corp_id.strip():
            raise ConfigurationError("corp_id must not be empty")
        if not secret.strip():
            raise ConfigurationError("secret must not be empty")
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
                value = value.replace(sensitive, "[REDACTED]")
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
            raise WeComTransportError("WeCom request failed for /cgi-bin/gettoken") from None
        if payload.errcode != 0:
            raise WeComApiError(payload.errcode, self.redact(payload.errmsg or "unknown error"))
        if not payload.access_token:
            raise WeComTransportError("WeCom returned an invalid access-token response")
        self._token = payload.access_token
        self._token_expires_at = time.monotonic() + max(0, payload.expires_in - 90)
        return payload.access_token


class WeComCustomerClient:
    """Asynchronous adapter around the WeCom customer-contact REST API."""

    def __init__(
        self,
        *,
        corp_id: str,
        secret: str,
        base_url: str = "https://qyapi.weixin.qq.com",
        timeout: float = 20.0,
        proxy: str | None = None,
        qps: float = 50.0,
        max_retries: int = 2,
        retry_backoff: float = 0.5,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if timeout <= 0:
            raise ConfigurationError("timeout must be greater than zero")
        if qps <= 0:
            raise ConfigurationError("qps must be greater than zero")
        if max_retries < 0:
            raise ConfigurationError("max_retries must not be negative")
        if retry_backoff < 0:
            raise ConfigurationError("retry_backoff must not be negative")
        self._corp_id = corp_id
        self._proxy = proxy
        self._auth = WeComAuth(corp_id=corp_id, secret=secret, base_url=base_url)
        self._max_retries = max_retries
        self._retry_backoff = retry_backoff
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
        response = await self._request(
            "GET", "/cgi-bin/externalcontact/get_follow_user_list", FollowUsersResponse
        )
        return response.follow_user

    async def iter_customer_details(
        self, userids: Sequence[str], *, limit: int = 100
    ) -> AsyncIterator[CustomerDetailItem]:
        if not 1 <= limit <= 100:
            raise ConfigurationError("customer page limit must be between 1 and 100")
        for userid_list in _chunks(userids, 100):
            cursor = ""
            seen_cursors: set[str] = set()
            while True:
                body: dict[str, Any] = {"userid_list": userid_list, "limit": limit}
                if cursor:
                    body["cursor"] = cursor
                response = await self._request(
                    "POST",
                    "/cgi-bin/externalcontact/batch/get_by_user",
                    CustomerPageResponse,
                    json=body,
                )
                for item in response.external_contact_list:
                    yield item
                cursor = response.next_cursor
                if not cursor:
                    break
                if cursor in seen_cursors:
                    raise WeComTransportError("WeCom returned a repeated customer cursor")
                seen_cursors.add(cursor)

    async def iter_group_chat_summaries(
        self, owner_userids: Sequence[str], *, limit: int = 1000
    ) -> AsyncIterator[GroupChatSummary]:
        if not 1 <= limit <= 1000:
            raise ConfigurationError("group-chat page limit must be between 1 and 1000")
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
                    raise WeComTransportError("WeCom returned a repeated group-chat cursor")
                seen_cursors.add(cursor)

    async def get_group_chat(self, chat_id: str) -> GroupChatDetail:
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
                raise WeComTransportError(f"WeCom request failed for {path}") from None
            except WeComTransportError:
                if attempt < self._max_retries:
                    await self._backoff(attempt)
                    continue
                raise
            except (httpx.HTTPError, ValueError, ValidationError):
                raise WeComTransportError(f"WeCom request failed for {path}") from None
        raise AssertionError("unreachable")

    async def _backoff(self, attempt: int) -> None:
        if self._retry_backoff:
            await asyncio.sleep(self._retry_backoff * (2**attempt))

    def _raise_for_api_error(self, payload: ApiResponse) -> None:
        if payload.errcode == 0:
            return
        raw_message = self._auth.redact((payload.errmsg or "unknown error")[:300])
        if self._proxy:
            raw_message = raw_message.replace(self._proxy, "[REDACTED]")
        raise WeComApiError(payload.errcode, raw_message)
