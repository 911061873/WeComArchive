from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator


class ServiceConfig(BaseModel):
    """每个子服务独立配置，时间单位为秒。"""

    model_config = ConfigDict(extra="forbid", frozen=True)
    threads: int = Field(default=1, ge=1, strict=True)
    poll_interval: float = Field(default=1.0, gt=0, allow_inf_nan=False)


class PullServiceConfig(ServiceConfig):
    """拉取服务配置，固定使用单线程。"""

    @field_validator("threads")
    @classmethod
    def single_pull_thread(cls, value: int) -> int:
        if value != 1:
            raise ValueError("拉取服务固定单线程")
        return value


class DecryptServiceConfig(ServiceConfig):
    """解密服务配置。"""


class ParseServiceConfig(ServiceConfig):
    """解析服务配置。"""


class ConsumeServiceConfig(ServiceConfig):
    """消费服务配置，默认使用四个线程。"""

    threads: int = Field(default=4, ge=1, strict=True)


class WeComArchiveConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    corp_id: str = Field(min_length=1)
    archive_secret: SecretStr
    database_url: str = "sqlite:///./wecom_archive.db"
    api_timeout: int = Field(default=5, ge=1, strict=True)
    proxy: str = ""
    batch_size: int = Field(default=1000, ge=1, le=1000, strict=True)
    consumption_window_seconds: float = Field(default=300, gt=0, allow_inf_nan=False)
    pull: PullServiceConfig = Field(default_factory=PullServiceConfig)
    decrypt: DecryptServiceConfig = Field(default_factory=DecryptServiceConfig)
    parse: ParseServiceConfig = Field(default_factory=ParseServiceConfig)
    consume: ConsumeServiceConfig = Field(default_factory=ConsumeServiceConfig)

    @field_validator("corp_id", "database_url")
    @classmethod
    def not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("不能为空")
        return value

    @field_validator("archive_secret")
    @classmethod
    def secret_not_blank(cls, value: SecretStr) -> SecretStr:
        if not value.get_secret_value().strip():
            raise ValueError("不能为空")
        return value
