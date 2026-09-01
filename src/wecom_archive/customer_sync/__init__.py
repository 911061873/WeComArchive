"""客户、客户群及群成员资料同步。"""

from .directory import CustomerContactDirectory
from .exceptions import (
    ConfigurationError,
    WeComApiError,
    WeComArchiveError,
    WeComTransportError,
)
from .types import (
    CustomerRecord,
    FollowRecord,
    GroupChatRecord,
    GroupMemberRecord,
    SyncResult,
)

__all__ = [
    "ConfigurationError",
    "CustomerContactDirectory",
    "CustomerRecord",
    "FollowRecord",
    "GroupChatRecord",
    "GroupMemberRecord",
    "SyncResult",
    "WeComApiError",
    "WeComArchiveError",
    "WeComTransportError",
]
