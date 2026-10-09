import pytest
from pydantic import ValidationError

from wecomarchive import ServiceConfig, WeComArchiveConfig
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


def test_secret_is_hidden():
    config = WeComArchiveConfig(corp_id="企业", archive_secret="秘密值")
    assert "秘密值" not in repr(config)


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
