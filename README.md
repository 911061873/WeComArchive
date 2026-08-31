# WeComArchive

一个使用 Python 实现的企业微信会话内容存档项目。

## 当前状态

客户联系资料目录的首个可运行版本已经完成，支持企业内部自建应用的客户、客户群及成员同步。会话存档模块仍处于规划和需求梳理阶段。

## 项目目标

- 独立同步客户、客户群及群成员资料，为会话数据提供可查询的联系人上下文。
- 稳定获取企业微信授权范围内的会话存档数据。
- 解密并标准化所支持的消息类型。
- 按照约定的数据保留和访问策略存储或转发存档数据。
- 保证同步过程可观测、可恢复并且能够安全运行。

项目的具体功能范围仍在梳理中。

## 项目文档

- [项目计划](docs/PLAN.md)
- [系统架构](docs/ARCHITECTURE.md)
- [决策记录](docs/DECISIONS.md)
- [项目记忆](docs/MEMORY.md)

## 技术栈

- 开发语言与版本：Python 3.10
- 项目与依赖管理：uv
- 客户联系 REST API 客户端：httpx（异步）
- 数据校验：Pydantic 2
- ORM 与数据库访问：SQLAlchemy 2.x（异步）
- 应用框架、部署方式和基础设施：待确定

## 客户联系资料同步

首个交付模块只支持企业内部自建应用。需要企业 ID（`corp_id`）以及管理后台“客户联系”范围的 Secret：

```python
import asyncio

from wecom_archive import CustomerContactDirectory


async def main() -> None:
    async with CustomerContactDirectory(
        corp_id="your-corp-id",
        secret="your-customer-contact-secret",
    ) as directory:
        customer_result, group_result = await directory.sync_all_once()
        customer = await directory.get_customer("external-user-id")
        group_chat = await directory.get_group_chat("group-chat-id")


asyncio.run(main())
```

未传入 `database_url` 时会在操作系统用户数据目录中创建 SQLite 数据库。也可以显式传入 SQLAlchemy 数据库 URL，组件会为 SQLite、MySQL 和 PostgreSQL 选择异步驱动；MySQL 和 PostgreSQL 分别需要安装 `mysql`、`postgresql` 可选依赖。可通过 `proxy` 为客户联系 REST API 配置 HTTP 代理，通过 `qps` 调整单个客户端实例的平滑请求速率上限，默认值为 `50`。token 获取、业务请求和重试都会计入 QPS。Secret、仅保存在进程内存中的访问令牌及带认证信息的代理地址不得写入日志。

### 真实接口测试

测试套件不模拟企业微信响应。运行前在当前 PowerShell 会话中设置非生产企业的凭据：

```powershell
$env:WECOM_CORP_ID = "非生产企业ID"
$env:WECOM_CUSTOMER_CONTACT_SECRET = "非生产客户联系Secret"
uv run pytest -m integration -q
```

如果必须通过 HTTP 代理访问，可以设置 `WECOM_HTTP_PROXY`。还可以设置 `WECOM_TEST_EXTERNAL_USERID` 和 `WECOM_TEST_GROUP_CHAT_ID`，让测试额外验证指定客户和客户群已同步。没有设置必需凭据时，真实接口测试会跳过；测试代码不会输出凭据。测试数据库保存在仓库的 `.integration-test-data/database/archive.sqlite3`，重复运行时会复用该数据库并确保其达到当前 schema，不依赖 pytest 的临时目录。项目处于开发阶段，不承诺旧开发数据库的原地升级兼容性；发生不兼容的 schema 变更时可以重建该测试数据库。

企业微信返回 `60020` 时，公开异常会用中文提示当前出口 IP 未加入“企业可信 IP”，并在响应包含该信息时显示企业微信识别到的出口 IP。原始 Secret、访问令牌和代理认证信息不会写入异常文本。

企业微信返回 `48002` 时，通常表示自建应用没有客户联系 API 权限。确认测试使用的是该自建应用的 Secret，并在“客户联系 → 客户 → API → 可调用应用”中添加该应用，同时检查应用可见范围。

## 安全要求

不得提交企业微信凭据、私钥、密钥或已解密的真实会话数据。开始实现前，需要先明确配置管理和数据处理规则。
