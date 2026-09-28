# WeComArchive

Python 3.10+ 企业微信会话存档框架：轮询拉取、解密、数据库存档，以及基于文本规则的异步消费。
单企业、单进程采集；开发者在自己的 Python 项目中配置和运行。

## 安装与运行

本仓库安装（尚未承诺已发布到 PyPI）：

```shell
python -m pip install .
# 按数据库选择驱动
python -m pip install '.[mysql]'
python -m pip install '.[postgres]'
```

使用 `pyweworkfinance`，需配置企业微信会话存档、访问权限与对应 RSA 私钥，
并满足 SDK 的本机运行要求。框架不会下载附件。

```python
import asyncio
from pathlib import Path
from wecomarchive import ArchiveConfig, ConsumerMessage, MessageArchiveService, TextRule

async def consume(message: ConsumerMessage):
    print(message.msgid, message.text, message.data)

async def main():
    service = MessageArchiveService(ArchiveConfig(
        corp_id="你的企业 ID",
        archive_secret="你的会话存档 Secret",
    ))
    service.set_private_key(Path("private_key.pem").read_bytes(), version=1)
    service.add_rule(TextRule(contains="订单"), consume)
    service.add_rule(TextRule(regex=r"工单\s*\d+"), consume)
    await service.run()

asyncio.run(main())
```

也可传入实现 `async def consume(self, message)` 的对象。每条规则只能设置 `contains` 或 `regex` 之一，
只匹配 `text.content`。同一消息命中两条规则就调用两次，即使消费者相同。
每次调用收到独立的 Pydantic `ConsumerMessage` 深拷贝：`msgid`、`seq`、`msgtype`、毫秒时间戳 `msgtime`、完整明文 `data`，以及便捷属性 `text`。

完整示例：

- [basic.py](examples/basic.py)：实际 SDK、系统环境变量、私钥、消费者与跨平台信号关闭。
- [offline.py](examples/offline.py)：无凭据演示，运行 `python examples/offline.py`。
- [http_consumer.py](examples/http_consumer.py)：自定义影刀控制台队列 HTTP 消费者，需接入方提供真实接口协议。

HTTP 示例的注册方式（HTTP 客户端应覆盖整个服务生命周期）：

```python
import httpx
from examples.http_consumer import ShadowBotHttpConsumer

async def run_http(service, queue_url, headers, build_payload):
    async with httpx.AsyncClient() as client:
        consumer = ShadowBotHttpConsumer(
            client, queue_url=queue_url, headers=headers, build_payload=build_payload)
        service.add_rule(TextRule(contains="订单"), consumer)
        await service.run()
```

`examples` 位于源代码仓库，不属于框架安装包；使用时复制需要的消费者到自己的项目。
安装 `.[examples]` 获取 HTTP 示例依赖。框架不内置业务推送、重试或幂等保证。

## 配置

配置通过 `ArchiveConfig` 显式传入，修改后重建服务。框架不读取 `.env` 或独立配置文件，
不修改应用日志配置。建议调用方使用 `logging.basicConfig(level=logging.INFO)`。

| 字段 | 默认值 | 含义 |
| --- | --- | --- |
| `corp_id` | 必填 | 企业 ID |
| `archive_secret` | 必填 | Pydantic SecretStr，不在配置 repr 中显示 |
| `database_url` | `sqlite:///./wecom_archive.db` | 当前工作目录下的 SQLite 文件 |
| `poll_interval` | `1.0` | 每批结束后的轮询等待秒数，失败同样等待 |
| `batch_size` | `1000` | 每次最多拉取 1–1000 条 |
| `api_timeout` | `5` | SDK 请求超时秒数 |
| `proxy` | 空字符串 | SDK 代理 |
| `consumption_window_seconds` | `300` | 发送时间窗口；`None` 关闭限制 |
| `queue_capacity` | `1000` | 待消费规则命中的最大队列长度 |
| `consumer_workers` | `4` | 并发消费者工作任务数；1 为串行 |
| `shutdown_timeout` | `30` | 停止后等待分发与消费排空的秒数 |

数据库 URL 示例：

```text
sqlite:///./wecom_archive.db
mysql+pymysql://user:password@127.0.0.1:3306/archive?charset=utf8mb4
postgresql+psycopg://user:password@127.0.0.1:5432/archive
```

先创建 MySQL/PostgreSQL 数据库，再交给框架初始化表。MySQL 使用 InnoDB 与 utf8mb4；
消息 ID 列使用区分大小写的排序规则。每个数据库只运行一个采集实例。
私钥可注册多个版本；版本 0 是未匹配版本时的回退密钥。密钥只留在内存，不写入数据库。

## 存储与消费语义

1. 首次从游标 0 获取，随后使用数据库持久化游标。同步 SDK 与数据库工作在专用线程执行。
2. 所有新密文、成功明文、类型分表和本批最大游标在同一事务提交。失败全部回滚。
3. 解密失败保留密文并记录错误；类型解析失败保留密文和完整明文；未知类型跳过类型分表。
4. 以消息 ID 去重，重复拉取不再解密或消费。本批内部重复同样去重。`switch` 明文也保留。
5. 成功提交后，以消息发送时间检查消费窗口，随后匹配规则并进入进程内有界队列。

开启时间窗口时，缺失/无效发送时间、未来时间或超出窗口的消息只存档。窗口边界包含恰好等于上限的消息。
一条消息开始分发时只检查一次，已经符合窗口的规则命中不会因入队等待或排队而被丢弃。
关闭窗口也只处理本次新入库消息，不重放数据库已有消息。

消费者异常只记录消息 ID 和异常类型，不自动重试，不影响后续任务。业务需要详细错误时请在消费者内部记录。
队列满时等待，停止继续拉取；内存同时容纳至多一个拉取批次、一个有界队列和工作任务中的消息。
并发消费不保证完成顺序，消费者应使用异步 I/O 并配合取消，不应阻塞事件循环或吞掉外部取消。

调用 `service.stop()` 请求正常关闭，再等待 `run()` 返回。关闭先停止后续拉取，限时等待当前分发和队列；
主线程运行 `run()` 时默认接管 Ctrl+C/SIGTERM，将其转换为正常关闭请求；关闭期间重复信号不会打断清理，
返回或异常退出后恢复原信号处理器。嵌入已有应用时可用 `run(handle_signals=False)` 由宿主处理信号；
后台线程运行时不会修改进程信号处理器，应由调用方调用 `stop()`。
超时取消工作任务，丢弃剩余消费。不恢复消费任务，不承诺消费必达或业务副作用恰好执行一次。
取消 `run()` 同样会清理资源。原生 SDK/数据库线程无法安全强杀，销毁资源前仍须等当前调用返回；
`shutdown_timeout` 限制消费排空，底层连接/请求超时应按运行环境另行设置。强制结束进程会丢失未完成消费。

表结构、支持类型、重构取舍见 [设计说明](doc/重构设计.md)。
数据库初始化、显式升级和旧开发库处理见 [迁移说明](doc/数据库迁移.md)。

## 开发与验证

```shell
uv sync --all-extras
uv run pytest
uv build
```

默认测试使用独立临时 SQLite 文件。真实 MySQL/PostgreSQL 测试通过环境变量
`WECOM_TEST_DATABASE_URL` 指定**独立空库**后运行 `pytest`；测试拒绝启动时已经有表的外部库。
测试会清理自己创建的表，禁止指向业务库。CI 矩阵见 [.github/workflows/tests.yml](.github/workflows/tests.yml)，
覆盖 Python 3.10/3.12、SQLite、MySQL 8.4 和 PostgreSQL 17。
SDK 测试使用替身与真实 RSA 运算，不会请求企业微信或发送影刀消息。
本次三种数据库的实际运行结果见 [验证记录](doc/验证记录.md)。

## 日志持久化

创建 `MessageArchiveService` 时自动在当前用户家目录的 `wecomarchive` 文件夹中配置 UTF-8 文件日志：

- `wecomarchive.log`：INFO、WARNING 日志，每个文件 10 MiB，保留 5 个轮转备份。
- `error.log`：ERROR、CRITICAL 日志，持续追加，不限制大小、不自动清理；包含采集、存储、解密、类型解析和消费失败。

同时保存 `pyweworkfinance` 的 WARNING 及以上日志。重复创建服务不会重复添加文件处理器，调用方已有的控制台日志配置继续生效。
