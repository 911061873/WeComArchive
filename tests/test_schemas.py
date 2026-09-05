from wecom_archive.customer_sync.schemas import ExternalContactResponse


def test_external_contact_response_validates_nested_fields() -> None:
    response = ExternalContactResponse.model_validate(
        {
            "errcode": 0,
            "errmsg": "ok",
            "external_contact": {
                "external_userid": "customer-1",
                "name": "李四",
                "external_profile": {
                    "external_attr": [
                        {"type": 0, "name": "文本名称", "text": {"value": "文本"}},
                        {
                            "type": 1,
                            "name": "网页名称",
                            "web": {"url": "https://www.test.com", "title": "标题"},
                        },
                        {
                            "type": 2,
                            "name": "测试app",
                            "miniprogram": {
                                "appid": "wx8bd80126147df384",
                                "pagepath": "/index",
                                "title": "my miniprogram",
                            },
                        },
                    ]
                },
            },
            "follow_user": [
                {
                    "userid": "rocky",
                    "tags": [
                        {
                            "group_name": "标签分组名称",
                            "tag_name": "标签名称",
                            "tag_id": "tag-1",
                            "type": 1,
                        }
                    ],
                    "remark_mobiles": ["13800000001"],
                    "wechat_channels": {"nickname": "视频号名称", "source": 1},
                },
                {"userid": "tommy", "state": "外联二维码1", "add_way": 3},
            ],
            "next_cursor": "NEXT_CURSOR",
        }
    )

    attributes = response.external_contact.external_profile
    assert attributes is not None
    assert attributes.external_attr[0].text is not None
    assert attributes.external_attr[0].text.value == "文本"
    assert attributes.external_attr[1].web is not None
    assert attributes.external_attr[1].web.url == "https://www.test.com"
    assert attributes.external_attr[2].miniprogram is not None
    assert attributes.external_attr[2].miniprogram.appid == "wx8bd80126147df384"
    assert response.follow_user[0].tags[0].tag_id == "tag-1"
    assert response.follow_user[0].wechat_channels is not None
    assert response.follow_user[0].wechat_channels.nickname == "视频号名称"
    assert response.follow_user[1].tags == []
    assert response.next_cursor == "NEXT_CURSOR"
