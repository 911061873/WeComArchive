from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator


class ArchiveConfig(BaseModel):
    """显式配置；修改后重新创建服务。配置时间单位为秒。"""

    model_config = ConfigDict(extra="forbid", frozen=True)
    corp_id: str = Field(min_length=1)
    archive_secret: SecretStr | str
    database_url: str = "sqlite:///./wecom_archive.db"
    poll_interval: float = Field(default=1.0, gt=0, allow_inf_nan=False)
    batch_size: int = Field(default=1000, ge=1, le=1000, strict=True)
    api_timeout: int = Field(default=5, ge=1, strict=True)
    proxy: str = ""
    consumption_window_seconds: float | None = Field(default=300, gt=0, allow_inf_nan=False)
    queue_capacity: int = Field(default=1000, ge=1, strict=True)
    consumer_workers: int = Field(default=4, ge=1, strict=True)
    shutdown_timeout: float = Field(default=30, gt=0, allow_inf_nan=False)

    @field_validator("corp_id", "database_url")
    @classmethod
    def not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("不能为空")
        return value

    @field_validator("archive_secret")
    @classmethod
    def secret_not_blank(cls, value: SecretStr | str) -> SecretStr:
        if isinstance(value, str):
            value = SecretStr(value)
        if not value.get_secret_value().strip():
            raise ValueError("不能为空")
        return value
