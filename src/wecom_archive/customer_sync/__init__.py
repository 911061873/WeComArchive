"""客户、客户群及群成员资料同步。"""

from .directory import CustomerContactDirectory
from .exceptions import (
    ConfigurationError,
    WeComApiError,
    WeComArchiveError,
    WeComTransportError,
)
from .types import (
    ContactUserRecord,
    CustomerRecord,
    FollowRecord,
    FollowTagRecord,
    GroupAdminRecord,
    GroupChatRecord,
    GroupMemberRecord,
    SyncResult,
    SyncRunRecord,
)

__all__ = [
    "ContactUserRecord",
    "FollowTagRecord",
    "GroupAdminRecord",
    "SyncRunRecord",
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
