"""影刀队列 HTTP 集成示意：路径、鉴权头和业务字段按实际控制台接口适配。

此模块不定义或猜测影刀官方接口协议；build_payload 和 headers 由接入方明确提供。
安装 pip install 'wecomarchive[examples]' 后，将消费者通过 service.add_rule 注册。
"""

from collections.abc import Callable, Mapping
from typing import Any

import httpx

from wecomarchive import ConsumerMessage


class ShadowBotHttpConsumer:
    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        queue_url: str,
        headers: Mapping[str, str],
        build_payload: Callable[[ConsumerMessage], dict[str, Any]],
    ):
        self.client = client
        self.queue_url = queue_url
        self.headers = dict(headers)
        self.build_payload = build_payload

    async def consume(self, message: ConsumerMessage) -> None:
        response = await self.client.post(
            self.queue_url, headers=self.headers, json=self.build_payload(message), timeout=10
        )
        response.raise_for_status()
        # 若控制台使用 HTTP 200 携带业务错误码，应按实际协议在这里检查并抛出异常。
