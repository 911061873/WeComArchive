import re


class WeComArchiveError(Exception):
    """Base class for stable public exceptions."""


class ConfigurationError(WeComArchiveError):
    """Raised when public configuration is invalid."""


class WeComTransportError(WeComArchiveError):
    """Raised when WeCom cannot be reached or returns an invalid response."""


class WeComApiError(WeComArchiveError):
    """Raised when WeCom returns a non-zero errcode."""

    def __init__(self, errcode: int, errmsg: str) -> None:
        self.errcode = errcode
        self.errmsg = errmsg
        super().__init__(f"企业微信 API 错误 {errcode}：{self._localized_message(errcode, errmsg)}")

    @staticmethod
    def _localized_message(errcode: int, errmsg: str) -> str:
        if errcode == 48002:
            return (
                "当前自建应用没有调用客户联系 API 的权限。"
                "请确认使用的是该自建应用的 Secret，并在企业微信管理后台的"
                "“客户联系 → 客户 → API → 可调用应用”中添加该应用；"
                "同时确认应用可见范围包含已启用客户联系功能的测试成员。"
            )
        if errcode == 60020:
            ip_match = re.search(r"from ip:\s*([^,\s]+)", errmsg, flags=re.IGNORECASE)
            ip_message = f"企业微信识别的出口 IP 是 {ip_match.group(1)}。" if ip_match else ""
            return (
                "当前出口 IP 未加入企业微信可信 IP 白名单。"
                f"{ip_message}"
                "请在自建应用的“企业可信 IP”中添加该地址，"
                "或配置已加入白名单的固定出口代理。"
            )
        return errmsg
