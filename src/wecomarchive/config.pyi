from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

class ServiceConfig(BaseModel):
    """每个子服务独立配置，时间单位为秒。"""

    model_config = ConfigDict(extra="forbid", frozen=True)
    threads: int = Field(default=1, ge=1, strict=True)
    poll_interval: float = Field(default=1.0, gt=0, allow_inf_nan=False)

class WeComArchiveConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    corp_id: str = Field(min_length=1)
    archive_secret: SecretStr
    database_url: str = "sqlite:///./wecom_archive.db"
    api_timeout: int = Field(default=5, ge=1, strict=True)
    proxy: str = ""
    batch_size: int = Field(default=1000, ge=1, le=1000, strict=True)
    consumption_window_seconds: float = Field(default=300, gt=0, allow_inf_nan=False)
    pull: ServiceConfig = Field(default_factory=ServiceConfig)
    decrypt: ServiceConfig = Field(default_factory=ServiceConfig)
    parse: ServiceConfig = Field(default_factory=ServiceConfig)
    consume: ServiceConfig = Field(default_factory=lambda: ServiceConfig(threads=4))

    @field_validator("corp_id", "database_url")
    @classmethod
    def not_blank(cls, value: str) -> str: ...
    @field_validator("archive_secret")
    @classmethod
    def secret_not_blank(cls, value: SecretStr) -> SecretStr: ...
    @field_validator("pull")
    @classmethod
    def single_pull_thread(cls, value: ServiceConfig) -> ServiceConfig: ...
