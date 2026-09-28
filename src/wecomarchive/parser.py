"""只提取类型数据，不下载附件。基础明文保存不依赖解析成功。"""

TYPE_SECTIONS = {
    name: name
    for name in (
        "text",
        "image",
        "voice",
        "video",
        "emotion",
        "file",
        "mixed",
        "meeting_voice_call",
        "voip_doc_share",
        "chatrecord",
        "revoke",
        "agree",
        "disagree",
        "card",
        "location",
        "link",
        "weapp",
        "collect",
        "redpacket",
        "meeting",
        "meeting_notification",
        "docmsg",
        "markdown",
        "news",
        "calendar",
        "external_redpacket",
        "sphfeed",
        "voiptext",
        "qydiskfile",
        "solitaire",
    )
}
TYPE_SECTIONS["note"] = "info"
MEDIA_TYPES = frozenset(
    {"image", "voice", "video", "emotion", "file", "meeting_voice_call", "voip_doc_share"}
)


def parse_type(msgtype: str, data: dict) -> dict | None:
    key = TYPE_SECTIONS.get(msgtype)
    if key is None:
        return None
    payload = data.get(key)
    if not isinstance(payload, dict):
        raise ValueError(f"{key} 必须是对象")
    result = {"payload": payload}
    if msgtype == "text":
        content = payload.get("content")
        if not isinstance(content, str):
            raise ValueError("text.content 必须是字符串")
        result["content"] = content
    if msgtype in MEDIA_TYPES:
        sdkfileid = payload.get("sdkfileid")
        if not isinstance(sdkfileid, str) or not sdkfileid:
            raise ValueError(f"{key}.sdkfileid 必须是非空字符串")
        result["sdkfileid"] = sdkfileid
        for field in ("filename", "md5sum"):
            value = payload.get(field)
            if value is not None and not isinstance(value, str):
                raise ValueError(f"{key}.{field} 必须是字符串")
            result[field] = value
        size = payload.get({"voice": "voice_size", "emotion": "imagesize"}.get(msgtype, "filesize"))
        if size is not None and (type(size) is not int or not 0 <= size < 2**63):
            raise ValueError(f"{key} 文件大小无效")
        result["filesize"] = size
    if msgtype in {"mixed", "chatrecord", "note"}:
        items = payload.get("items" if msgtype == "note" else "item")
        if not isinstance(items, list) or any(not isinstance(item, dict) for item in items):
            raise ValueError(f"{key} 子项必须是对象数组")
    return result
