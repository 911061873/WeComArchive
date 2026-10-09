"""影刀 HTTP 集成示意，路径、鉴权和字段需按业务接口适配。

每个回调内部创建异步客户端，避免跨工作线程共享事件循环资源。
安装 pip install 'wecomarchive[examples]'，通过 archive.add_consumer("影刀推送", "订单", False, consumer) 注册。
"""

from collections.abc import Callable, Mapping
from typing import Any

import httpx

from wecomarchive import ConsumerMessage


class ShadowBotHttpConsumer:
    def __init__(
        self,
        *,
        queue_url: str,
        headers: Mapping[str, str],
        build_payload: Callable[[ConsumerMessage], dict[str, Any]],
    ):
        self.queue_url = queue_url
        self.headers = dict(headers)
        self.build_payload = build_payload

    async def __call__(self, message: ConsumerMessage) -> None:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                self.queue_url, headers=self.headers, json=self.build_payload(message), timeout=10
            )
            response.raise_for_status()
            # 若 HTTP 200 携带业务错误码，应按实际协议检查并抛出异常。
