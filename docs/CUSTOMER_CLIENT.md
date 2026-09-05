# 客户管理 Client

`wecom_archive.customer_sync.client.WeComCustomerClient` 是独立可用的异步 HTTP 适配器，不需要初始化数据库。沿用现有 token 内存缓存、鉴权、QPS、并发控制及错误映射。

## 方法命名约定

- `get_*_ids` 返回 ID 列表；`get_*_detail` 返回单个完整详情对象。
- `get_*_page` 返回一页响应，包含数据及游标；`iter_*_pages` 自动翻页，每次返回一页完整响应。
- `iter_*_summaries` 和 `iter_*_nodes` 自动翻页，每次分别返回一条摘要或一个节点。
- `customer_detail` 表示单个客户及其跟进人；`customer_batch` 表示按成员列表批量查询的客户详情。
- `create_*`、`update_*`、`delete_*` 表示写入操作。

本次直接统一内部 client 方法名，不保留旧名称别名。`CustomerContactDirectory` 的本地查询方法 `get_customer()`、`get_group_chat()` 保持原名。

## 已封装范围

按企业微信[客户联系概述](https://developer.work.weixin.qq.com/document/path/92109)导航中的“客户管理”目录核对，共 10 个 HTTP 接口（2026-09-05）。其他目录，如客户标签、在职/离职继承、联系我、朋友圈等，不属于这份覆盖清单。

| 官方接口 | Client 方法 | 返回值 |
| --- | --- | --- |
| [获取客户列表](https://developer.work.weixin.qq.com/document/path/92113) | `get_customer_ids(userid)` | 客户 ID 列表 |
| [获取客户详情](https://developer.work.weixin.qq.com/document/path/92114) | `get_customer_detail_page(external_userid, cursor=...)` | `ExternalContactResponse` |
| [批量获取客户详情](https://developer.work.weixin.qq.com/document/path/92994) | `get_customer_batch_page(userids, cursor=..., limit=...)` | `ExternalContactPageResponse` |
| [修改客户备注信息](https://developer.work.weixin.qq.com/document/path/92115) | `update_customer_remark(...)` | `None` |
| [获取规则组列表](https://developer.work.weixin.qq.com/document/path/94883) | `get_customer_strategy_list_page(cursor=..., limit=...)` | `CustomerStrategyListResponse` |
| 获取规则组详情 | `get_customer_strategy_detail(strategy_id)` | `CustomerStrategy` |
| 获取规则组管理范围 | `get_customer_strategy_range_page(strategy_id, cursor=..., limit=...)` | `CustomerStrategyRangeResponse` |
| 创建新的规则组 | `create_customer_strategy(...)` | 规则组 ID（整数） |
| 编辑规则组及其管理范围 | `update_customer_strategy(...)` | `None` |
| 删除规则组 | `delete_customer_strategy(strategy_id)` | `None` |

规则组 6 个接口的参数定义均见[客户联系规则组管理](https://developer.work.weixin.qq.com/document/path/94883)。成员 ID 查询使用 `get_follow_user_ids()`；客户群摘要遍历使用 `iter_group_chat_summaries()`；客户群详情查询使用 `get_group_chat_detail()`。

## 读取及分页

```python
from wecom_archive.customer_sync.client import WeComCustomerClient

async with WeComCustomerClient(corp_id=corp_id, secret=secret) as client:
    customer_ids = await client.get_customer_ids("zhangsan")
    for customer_id in customer_ids:
        async for page in client.iter_customer_detail_pages(customer_id):
            # 单客户详情的分页对象是跟进人，超过 500 人时可能有下一页。
            for follow_user in page.follow_user:
                handle_follow_user(page.external_contact, follow_user)

    page = await client.get_customer_batch_page(["zhangsan"], limit=50)
    # 调用方可处理部分失败并保存 next_cursor，再显式请求下一页。
    if page.fail_info:
        handle_unlicensed_users(page.fail_info.unlicensed_userid_list)
```

- 单页方法保留 `next_cursor`，每次只发送一次业务请求（允许安全重试）。响应使用 Pydantic 模型，同时保留未知字段，可用 `model_dump()` 取出完整数据。
- `iter_customer_detail_pages()` 按页返回单客户详情，`iter_customer_batch_pages()` 按页返回完整的 `ExternalContactPageResponse`，保留 `external_contact_list`、`next_cursor` 和 `fail_info`，不会因部分失败中断遍历。`CustomerContactDirectory` 同步层遇到非空 `fail_info.unlicensed_userid_list` 时抛出 `WeComArchiveError`，记录本轮失败并禁止清理旧数据；client 不将部分失败伪装成非零 API 返回码。
- `iter_customer_strategy_summaries()`、`iter_customer_strategy_range_nodes()` 分别逐项返回规则组摘要和管理节点，支持 `cursor` 续查。
- 自动遍历在游标为空时结束，重复游标报 `WeComTransportError`；空数据页但有新游标时仍继续。
- 批量客户方法沿用项目默认 `limit=100`（官方默认 50），成员组和页大小均最多 100；规则组列表/范围默认及最大页大小均为 1000。

## 规则组写入

```python
from wecom_archive.customer_sync.schemas import (
    CustomerStrategyParty,
    CustomerStrategyPrivilege,
    CustomerStrategyUser,
)

strategy_id = await client.create_customer_strategy(
    "华东客户组",
    ["zhangsan"],
    parent_id=0,
    range=[CustomerStrategyUser(userid="lisi"), CustomerStrategyParty(partyid=2)],
    privilege=CustomerStrategyPrivilege(share_customer=False),
)
await client.update_customer_strategy(
    strategy_id,
    range_add=[CustomerStrategyUser(userid="wangwu")],
)
```

- `None` 可选参数不发送；权限中的 `False` 保留。基础权限不能设为 `False`。编辑时管理员列表与权限对象遵守官方覆盖语义，空管理员列表或空权限对象表示不编辑相应内容。
- 单次最多 20 个管理员、100 个管理节点；编辑新增和删除节点总数不超过 100。父规则组范围、权限继承、超级管理员限制及树层级由服务端验证。
- 创建、编辑、删除在同一个 client 实例内串行执行。多个实例或进程调用同一企业时，调用方还需统一协调串行操作。
- 规则组写入遇到超时或 HTTP 故障时不自动重发，避免创建重复规则组或重复执行结果不确定的操作；明确返回 token 无效时仍允许刷新后重试。
- 修改客户备注成功仍返回 `None`；空备注字符串和 `remark_mobiles=[""]` 的清除语义保持不变。

## 验证

离线协议测试覆盖 HTTP 方法/路径、GET 参数编码、分页和续查、响应校验、部分失败、规则组字段与串行写入、未知结果不重试。运行：

```powershell
.venv\Scripts\python.exe -m pytest -m "not integration" -q
```

真实接口测试使用非生产企业凭据单独运行；离线测试不证明真实账号的权限与服务端执行结果。
