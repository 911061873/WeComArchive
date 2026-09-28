from typing import Protocol

from .models import ConsumerMessage


class MessageConsumer(Protocol):
    async def consume(self, message: ConsumerMessage) -> None: ...
