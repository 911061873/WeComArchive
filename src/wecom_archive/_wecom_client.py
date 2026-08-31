from __future__ import annotations

import asyncio
import threading
import time
from collections.abc import AsyncGenerator, AsyncIterator, Generator, Iterator, Sequence
from typing import Any

import httpx

from .exceptions import ConfigurationError, WeComApiError, WeComTransportError

_TOKEN_ERROR_CODES = {40014, 42001}


def _chunks(values: Sequence[str], size: int) -> Iterator[list[str]]:
    for offset in range(0, len(values), size):
        yield list(values[offset : offset + size])


def _errcode(payload: dict[str, Any]) -> int:
    try:
        return int(payload.get("errcode", 0))
    except (TypeError, ValueError) as exc:
        raise WeComTransportError("WeCom returned an invalid errcode") from exc


class WeComAuth(httpx.Auth):
    """Authenticate sync and async WeCom requests with a cached access token."""

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
        self._token_generation = 0
        self._token_lock = threading.Lock()
        self._async_token_lock = asyncio.Lock()

    def sync_auth_flow(
        self, request: httpx.Request
    ) -> Generator[httpx.Request, httpx.Response, None]:
        request.read()
        cached = self._cached_token()
        if cached is None:
            with self._token_lock:
                cached = self._cached_token()
                if cached is None:
                    token_response = yield self._build_token_request(request)
                    cached = self._store_token(token_response)

        token, generation = cached
        request.url = request.url.copy_set_param("access_token", token)
        response = yield request
        response.read()
        if not self._is_token_error(response):
            return

        with self._token_lock:
            cached = self._cached_token()
            if cached is None or cached[1] == generation:
                token_response = yield self._build_token_request(request)
                cached = self._store_token(token_response)

        request.url = request.url.copy_set_param("access_token", cached[0])
        yield request

    async def async_auth_flow(
        self, request: httpx.Request
    ) -> AsyncGenerator[httpx.Request, httpx.Response]:
        await request.aread()
        cached = self._cached_token()
        if cached is None:
            async with self._async_token_lock:
                cached = self._cached_token()
                if cached is None:
                    token_response = yield self._build_token_request(request)
                    cached = await self._store_token_async(token_response)

        token, generation = cached
        request.url = request.url.copy_set_param("access_token", token)
        response = yield request
        await response.aread()
        if not self._is_token_error(response):
            return

        async with self._async_token_lock:
            cached = self._cached_token()
            if cached is None or cached[1] == generation:
                token_response = yield self._build_token_request(request)
                cached = await self._store_token_async(token_response)

        request.url = request.url.copy_set_param("access_token", cached[0])
        yield request

    def redact(self, value: str) -> str:
        for sensitive in (self._secret, self._token):
            if sensitive:
                value = value.replace(sensitive, "[REDACTED]")
        return value

    def _cached_token(self) -> tuple[str, int] | None:
        if self._token and time.monotonic() < self._token_expires_at:
            return self._token, self._token_generation
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

    def _store_token(self, response: httpx.Response) -> tuple[str, int]:
        try:
            response.read()
        except httpx.HTTPError:
            raise WeComTransportError("WeCom request failed for /cgi-bin/gettoken") from None
        return self._parse_token_response(response)

    async def _store_token_async(self, response: httpx.Response) -> tuple[str, int]:
        try:
            await response.aread()
        except httpx.HTTPError:
            raise WeComTransportError("WeCom request failed for /cgi-bin/gettoken") from None
        return self._parse_token_response(response)

    def _parse_token_response(self, response: httpx.Response) -> tuple[str, int]:
        try:
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError):
            raise WeComTransportError("WeCom request failed for /cgi-bin/gettoken") from None
        if not isinstance(payload, dict):
            raise WeComTransportError(
                "WeCom returned an invalid response for /cgi-bin/gettoken"
            )

        errcode = _errcode(payload)
        if errcode != 0:
            raw_message = str(payload.get("errmsg") or "unknown error")[:300]
            raise WeComApiError(errcode, self.redact(raw_message))

        token = payload.get("access_token")
        if not isinstance(token, str) or not token:
            raise WeComTransportError("WeCom returned an invalid access-token response")
        expires_in = payload.get("expires_in", 7200)
        try:
            lifetime = max(0, int(expires_in) - 90)
        except (TypeError, ValueError) as exc:
            raise WeComTransportError("WeCom returned an invalid token lifetime") from exc

        self._token = token
        self._token_expires_at = time.monotonic() + lifetime
        self._token_generation += 1
        return token, self._token_generation

    @staticmethod
    def _is_token_error(response: httpx.Response) -> bool:
        try:
            payload = response.json()
            errcode = int(payload.get("errcode", 0)) if isinstance(payload, dict) else 0
        except (TypeError, ValueError):
            return False
        return errcode in _TOKEN_ERROR_CODES


class WeComCustomerClient:
    """Sync and async adapter around the WeCom customer-contact REST API."""

    def __init__(
        self,
        *,
        corp_id: str,
        secret: str,
        base_url: str = "https://qyapi.weixin.qq.com",
        timeout: float = 20.0,
        proxy: str | None = None,
    ) -> None:
        if timeout <= 0:
            raise ConfigurationError("timeout must be greater than zero")

        self._corp_id = corp_id
        self._proxy = proxy
        self._base_url = base_url
        self._timeout = timeout
        self._auth = WeComAuth(corp_id=corp_id, secret=secret, base_url=base_url)
        self._async_auth = WeComAuth(corp_id=corp_id, secret=secret, base_url=base_url)
        self._http = httpx.Client(
            base_url=base_url,
            timeout=timeout,
            proxy=proxy,
            auth=self._auth,
            headers={"Accept": "application/json"},
        )
        self._async_http: httpx.AsyncClient | None = None

    def __repr__(self) -> str:
        return f"{type(self).__name__}(corp_id={self._corp_id!r})"

    def __enter__(self) -> WeComCustomerClient:
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()

    async def __aenter__(self) -> WeComCustomerClient:
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.aclose()

    def close(self) -> None:
        if self._async_http is not None and not self._async_http.is_closed:
            raise RuntimeError("Use 'await client.aclose()' after asynchronous requests")
        self._http.close()

    async def aclose(self) -> None:
        self._http.close()
        if self._async_http is not None:
            await self._async_http.aclose()

    def get_follow_users(self) -> list[str]:
        payload = self._request("GET", "/cgi-bin/externalcontact/get_follow_user_list")
        return [str(value) for value in payload.get("follow_user", [])]

    async def async_get_follow_users(self) -> list[str]:
        payload = await self._request_async(
            "GET", "/cgi-bin/externalcontact/get_follow_user_list"
        )
        return [str(value) for value in payload.get("follow_user", [])]

    def iter_customer_details(
        self, userids: Sequence[str], *, limit: int = 100
    ) -> Iterator[dict[str, Any]]:
        if not 1 <= limit <= 100:
            raise ConfigurationError("customer page limit must be between 1 and 100")

        for userid_list in _chunks(userids, 100):
            cursor = ""
            seen_cursors: set[str] = set()
            while True:
                body: dict[str, Any] = {"userid_list": userid_list, "limit": limit}
                if cursor:
                    body["cursor"] = cursor
                payload = self._request(
                    "POST", "/cgi-bin/externalcontact/batch/get_by_user", json=body
                )
                for item in payload.get("external_contact_list", []):
                    if isinstance(item, dict):
                        yield item
                cursor = str(payload.get("next_cursor") or "")
                if not cursor:
                    break
                if cursor in seen_cursors:
                    raise WeComTransportError("WeCom returned a repeated customer cursor")
                seen_cursors.add(cursor)

    async def async_iter_customer_details(
        self, userids: Sequence[str], *, limit: int = 100
    ) -> AsyncIterator[dict[str, Any]]:
        if not 1 <= limit <= 100:
            raise ConfigurationError("customer page limit must be between 1 and 100")

        for userid_list in _chunks(userids, 100):
            cursor = ""
            seen_cursors: set[str] = set()
            while True:
                body: dict[str, Any] = {"userid_list": userid_list, "limit": limit}
                if cursor:
                    body["cursor"] = cursor
                payload = await self._request_async(
                    "POST", "/cgi-bin/externalcontact/batch/get_by_user", json=body
                )
                for item in payload.get("external_contact_list", []):
                    if isinstance(item, dict):
                        yield item
                cursor = str(payload.get("next_cursor") or "")
                if not cursor:
                    break
                if cursor in seen_cursors:
                    raise WeComTransportError("WeCom returned a repeated customer cursor")
                seen_cursors.add(cursor)

    def iter_group_chat_summaries(
        self, owner_userids: Sequence[str], *, limit: int = 1000
    ) -> Iterator[dict[str, Any]]:
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
                payload = self._request(
                    "POST", "/cgi-bin/externalcontact/groupchat/list", json=body
                )
                for item in payload.get("group_chat_list", []):
                    if isinstance(item, dict):
                        yield item
                cursor = str(payload.get("next_cursor") or "")
                if not cursor:
                    break
                if cursor in seen_cursors:
                    raise WeComTransportError("WeCom returned a repeated group-chat cursor")
                seen_cursors.add(cursor)

    async def async_iter_group_chat_summaries(
        self, owner_userids: Sequence[str], *, limit: int = 1000
    ) -> AsyncIterator[dict[str, Any]]:
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
                payload = await self._request_async(
                    "POST", "/cgi-bin/externalcontact/groupchat/list", json=body
                )
                for item in payload.get("group_chat_list", []):
                    if isinstance(item, dict):
                        yield item
                cursor = str(payload.get("next_cursor") or "")
                if not cursor:
                    break
                if cursor in seen_cursors:
                    raise WeComTransportError(
                        "WeCom returned a repeated group-chat cursor"
                    )
                seen_cursors.add(cursor)

    def get_group_chat(self, chat_id: str) -> dict[str, Any]:
        payload = self._request(
            "POST",
            "/cgi-bin/externalcontact/groupchat/get",
            json={"chat_id": chat_id, "need_name": 1},
        )
        group_chat = payload.get("group_chat")
        if not isinstance(group_chat, dict):
            raise WeComTransportError("WeCom returned an invalid group-chat detail response")
        return group_chat

    async def async_get_group_chat(self, chat_id: str) -> dict[str, Any]:
        payload = await self._request_async(
            "POST",
            "/cgi-bin/externalcontact/groupchat/get",
            json={"chat_id": chat_id, "need_name": 1},
        )
        group_chat = payload.get("group_chat")
        if not isinstance(group_chat, dict):
            raise WeComTransportError("WeCom returned an invalid group-chat detail response")
        return group_chat

    def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        try:
            response = self._http.request(method, path, json=json)
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError):
            # httpx exceptions may include the full query string. Suppress the
            # cause so access_token, corpsecret, or proxy credentials cannot
            # leak through a traceback.
            raise WeComTransportError(f"WeCom request failed for {path}") from None
        if not isinstance(payload, dict):
            raise WeComTransportError(f"WeCom returned an invalid response for {path}")
        self._raise_for_api_error(payload)
        return payload

    async def _request_async(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        client = self._get_async_http()
        try:
            response = await client.request(method, path, json=json)
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError):
            raise WeComTransportError(f"WeCom request failed for {path}") from None
        if not isinstance(payload, dict):
            raise WeComTransportError(f"WeCom returned an invalid response for {path}")
        self._raise_for_api_error(payload)
        return payload

    def _get_async_http(self) -> httpx.AsyncClient:
        if self._http.is_closed:
            raise RuntimeError("WeComCustomerClient is closed")
        if self._async_http is None:
            self._async_http = httpx.AsyncClient(
                base_url=self._base_url,
                timeout=self._timeout,
                proxy=self._proxy,
                auth=self._async_auth,
                headers={"Accept": "application/json"},
            )
        return self._async_http

    def _raise_for_api_error(self, payload: dict[str, Any]) -> None:
        errcode = _errcode(payload)
        if errcode == 0:
            return
        raw_message = str(payload.get("errmsg") or "unknown error")[:300]
        raw_message = self._auth.redact(raw_message)
        raw_message = self._async_auth.redact(raw_message)
        if self._proxy:
            raw_message = raw_message.replace(self._proxy, "[REDACTED]")
        raise WeComApiError(errcode, raw_message)
