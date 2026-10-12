import pytest
from pydantic import SecretStr, ValidationError

from wecomarchive import (
    ConsumeServiceConfig,
    DecryptServiceConfig,
    ParseServiceConfig,
    PullServiceConfig,
    ServiceConfig,
    WeComArchiveConfig,
)
from wecomarchive.services.consume import ConsumeService


@pytest.mark.parametrize(
    "changes",
    [
        {"corp_id": " "},
        {"archive_secret": " "},
        {"batch_size": 1001},
        {"consumption_window_seconds": 0},
        {"pull": {"threads": 2}},
        {"decrypt": {"threads": 0}},
        {"parse": {"poll_interval": float("inf")}},
        {"api_timeout": True},
    ],
)
def test_invalid_configuration(changes):
    with pytest.raises(ValidationError):
        WeComArchiveConfig(**({"corp_id": "企业", "archive_secret": "密钥"} | changes))


@pytest.mark.parametrize("secret", ["秘密值", SecretStr("秘密值")])
def test_secret_is_hidden(secret):
    config = WeComArchiveConfig(corp_id="企业", archive_secret=secret)
    assert isinstance(config.archive_secret, SecretStr)
    assert config.archive_secret.get_secret_value() == "秘密值"
    assert "秘密值" not in repr(config)


def test_service_configuration_defaults_are_independent():
    first = WeComArchiveConfig(corp_id="企业", archive_secret="密钥")
    second = WeComArchiveConfig(corp_id="企业", archive_secret="密钥")
    for name, model, threads in (
        ("pull", PullServiceConfig, 1),
        ("decrypt", DecryptServiceConfig, 1),
        ("parse", ParseServiceConfig, 1),
        ("consume", ConsumeServiceConfig, 4),
    ):
        config = getattr(first, name)
        assert isinstance(config, model)
        assert isinstance(config, ServiceConfig)
        assert config.threads == threads
        assert config.poll_interval == 1.0
        assert config is not getattr(second, name)


def test_pull_configuration_rejects_multiple_threads():
    with pytest.raises(ValidationError, match="拉取服务固定单线程"):
        PullServiceConfig(threads=2)


def test_registration_validation_and_async_callable():
    service = ConsumeService(ServiceConfig(), None, 300)
    service.register("A", "", False, lambda message: None)
    with pytest.raises(ValueError):
        service.register("A", "", False, lambda message: None)
    with pytest.raises(ValueError):
        service.register(" ", "", False, lambda message: None)
    with pytest.raises(TypeError):
        service.register("B", "", False, None)

    class Consumer:
        async def __call__(self, message):
            pass

    service.register("异步对象", "", False, Consumer())
    assert service.registrations["异步对象"].asynchronous
    service._started = True
    with pytest.raises(RuntimeError):
        service.register("C", "", False, lambda message: None)


@pytest.mark.parametrize(
    "match_text,is_regex,error",
    [
        (None, False, TypeError),
        ("订单", 1, TypeError),
        ("[", True, ValueError),
    ],
)
def test_invalid_consumer_filter_is_rejected_at_registration(match_text, is_regex, error):
    service = ConsumeService(ServiceConfig(), None, 300)
    with pytest.raises(error):
        service.register("消费者", match_text, is_regex, lambda _: None)
    assert service.registrations == {}
