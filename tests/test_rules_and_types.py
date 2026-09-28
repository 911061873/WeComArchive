import pytest
from conftest import chat, plaintext
from pydantic import ValidationError

from wecomarchive import ArchiveConfig, TextRule
from wecomarchive.parser import MEDIA_TYPES, TYPE_SECTIONS, parse_type
from wecomarchive.storage import prepare


@pytest.mark.parametrize(
    "kwargs", [{}, {"contains": "a", "regex": "b"}, {"contains": ""}, {"regex": "["}]
)
def test_invalid_rules(kwargs):
    with pytest.raises(ValidationError):
        TextRule(**kwargs)


def test_only_text_body_matches():
    message = prepare(chat(), plaintext()).message
    assert TextRule(contains="订单").matches(message)
    assert TextRule(regex=r"订单\s+\d+").matches(message)
    assert not TextRule(contains="sender").matches(message)
    assert not TextRule(contains="订单").matches(
        prepare(chat(), plaintext(msgtype="markdown")).message
    )
    assert not TextRule(contains="订单").matches(prepare(chat(), plaintext(text={})).message)


@pytest.mark.parametrize("kind", list(TYPE_SECTIONS))
def test_supported_sections(kind):
    section = {"unknown_future_field": "保留"}
    if kind == "text":
        section["content"] = "hello"
    if kind in MEDIA_TYPES:
        section.update(sdkfileid="id", filename="文件", md5sum="md5")
    if kind in {"mixed", "chatrecord", "note"}:
        section["items" if kind == "note" else "item"] = [{"type": "future", "content": "opaque"}]
    result = parse_type(kind, {TYPE_SECTIONS[kind]: section})
    assert result["payload"] == section
    assert parse_type("unknown", {}) is None


@pytest.mark.parametrize(
    "kwargs",
    [
        {"poll_interval": 0},
        {"poll_interval": float("nan")},
        {"batch_size": 1001},
        {"queue_capacity": 0},
        {"consumer_workers": 0},
        {"shutdown_timeout": 0},
        {"consumption_window_seconds": -1},
        {"corp_id": " "},
        {"archive_secret": ""},
        {"queue_capacity": True},
        {"obsolete_setting": 1},
    ],
)
def test_config_validation(kwargs):
    values = {"corp_id": "corp", "archive_secret": "secret", **kwargs}
    with pytest.raises(ValidationError):
        ArchiveConfig(**values)


def test_secret_is_redacted_and_window_can_be_disabled():
    config = ArchiveConfig(
        corp_id="corp", archive_secret="do-not-print", consumption_window_seconds=None
    )
    assert "do-not-print" not in repr(config)
    assert config.consumption_window_seconds is None
    assert config.database_url.startswith("sqlite:")
