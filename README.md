# WeComArchive

Python 3.10+ 企业微信会话存档框架。第二版以 [第二版规划](doc/第二版规划.md) 为准：
拉取加密消息 → 解密保存原始明文 → 解析文本消息 → 多消费者执行业务。

## 使用

当前源码采用第二版 API，与已发布的第一版 API 不兼容。安装源码及所需数据库驱动：

```shell
python -m pip install .
python -m pip install ".[mysql]"
python -m pip install ".[postgres]"
```

实际运行需配置企业微信会话内容存档、对应 RSA 私钥，以及 pyweworkfinance 原生 SDK 的运行环境。

```python
from pathlib import Path
from wecomarchive import WeComArchive, ServiceConfig, ConsumerMessage

async def consume(message: ConsumerMessage):
    if "订单" in message.text:
        print(message.msgid, message.text)

with WeComArchive(
    corp_id="你的企业 ID",
    archive_secret="你的会话存档 Secret",
    decrypt=ServiceConfig(threads=2, poll_interval=1),
    parse=ServiceConfig(threads=2, poll_interval=1),
    consume=ServiceConfig(threads=4, poll_interval=1),
) as archive:
    archive.set_private_key(Path("private_key.pem").read_bytes(), version=1)
    archive.add_consumer("订单处理", "订单", False, consume)
    archive.start()
```

构造函数完成参数校验、数据库初始化、独占锁、SDK 初始化及四个子服务创建；失败立即抛出错误并清理资源。
密钥与消费者只能在启动前设置。消费者名称必须唯一，并在跨重启时保持稳定。
主服务 `start()` 必须在主线程调用，阻塞运行；没有公共 `stop()`。Ctrl+C/SIGTERM 请求停止，
所有子服务停止领取新任务，等待已执行任务结束，显示各服务剩余数量和总体退出进度，释放资源后返回。
重复信号不打断清理；同步消费者耗时会延迟退出。实例只运行一次。未启动的实例可用 `close()` 释放资源。

消费者可为同步函数、异步函数或异步可调用对象，注册时识别类型。每次调用收到独立消息副本。
异步消费者在工作线程的事件循环中运行，应在回调内管理异步资源，避免跨工作线程共享绑定事件循环的客户端。
注册接口为 `add_consumer(name, match_text, is_regex, callback)`：
`is_regex=False` 时对 `message.text` 做区分大小写的子串匹配，`True` 时使用正则 search。
正则在注册时校验并编译；空匹配文本匹配所有文本消息。只有命中条件才调用处理函数。
未命中的消息按消费者持久化为 `skipped`，重启不重复检查；修改过滤条件时如需重新处理，请使用新消费者名称。
不保证消费者执行顺序。

## 配置

| 构造参数 | 默认值 | 含义 |
| --- | --- | --- |
| `corp_id` / `archive_secret` | 必填 | 企业 ID / 会话存档 Secret |
| `database_url` | `sqlite:///./wecom_archive.db` | 数据库 URL |
| `proxy` | 空字符串 | SDK 请求代理 |
| `timeout` | `5` | SDK 请求超时秒数 |
| `batch_size` | `1000` | 单次拉取数量，1–1000 |
| `consumption_window_seconds` | `300` | 消息发送时间窗口秒数，必须为正数 |
| `pull` / `decrypt` / `parse` | `ServiceConfig()` | 各服务线程数及空闲轮询间隔 |
| `consume` | `ServiceConfig(threads=4)` | 消费服务线程数及空闲轮询间隔 |
| `client` | 内置 FinanceClient | 可注入实现 ArchiveClient 协议的替身 |

`ServiceConfig(threads=1, poll_interval=1.0)` 为各服务独立配置；拉取固定单线程。
数据库支持 SQLite、MySQL、PostgreSQL；同一数据库仅允许一个主服务实例。
SQLite 使用文件锁，MySQL/PostgreSQL 使用数据库会话锁。实例锁在构造时获取，直到关闭后释放。
同一原生 SDK 句柄的调用使用锁保护；增加解密线程不会使共享原生 SDK 同时执行。
密钥按版本精确匹配，没有版本 0 回退；私钥只保留在内存。

## 数据与处理语义

拉取消息保存及游标推进使用同一事务。拉取或保存失败保留游标，下轮重新读取已持久化游标继续拉取。
解密保存原始字符串，不解析结构；解析当前仅支持文本，媒体类型标记 `unsupported`，不下载附件。
统一消息对象包含 `msgid`、`seq`、`msgtype`、毫秒时间戳 `msgtime`、`sender`、`tolist`、`roomid`、
`action`、完整明文 `data` 和便捷属性 `text`。`TextMessage` 对文本内容和公共字段进行 Pydantic 校验。

各阶段通过数据库状态衔接，多线程先提交任务领取再执行：

- 解密：`pending` → `processing` → `success` / `failed` / `waiting_key`。
- 解析：`pending` → `processing` → `success` / `failed` / `unsupported`。
- 每个消息 × 消费者：`pending` → `processing` → `success` / `failed` / `expired` / `skipped`。

每次启动只恢复 `processing` 为 `pending`；失败、成功及暂不支持状态保留，不自动重试。
已配置对应密钥版本的 `waiting_key` 消息在启动时单独恢复为待解密，其余继续等待。
新增消息类型支持后，用户手动将相应 `archive_decrypted.status` 重置为 `pending` 才会重新解析。

消费领取时及准备调用前均检查消息时间窗口：只消费最近 5 分钟内的消息，未来消息等待进入窗口。
新增消费者只处理窗口内且该消费者尚未成功、失败或过期的消息；其他消费者的状态不影响它。
已经开始的调用不会因超出窗口被中断。窗口外消息保留解析结果。一个消费者失败不影响其他消费者。

**重复消费风险：**消费者已完成业务操作，但进程在保存成功状态前退出，重启后可能再次调用。
请按业务需要以消息 ID 和消费者名称自行保证幂等。

## 迁移与验证

空库自动初始化；旧版数据库已废弃，请备份后另建空库，见 [数据库迁移](doc/数据库迁移.md)。
完整架构见 [重构设计](doc/重构设计.md)。

```shell
python -m pytest
python -m ruff check src tests examples scripts
python -m ruff format --check src tests examples scripts
python -m build --wheel
```

测试默认使用独立 SQLite 文件；`WECOM_TEST_DATABASE_URL` 可指定独立空 MySQL/PostgreSQL 测试库。
测试拒绝清理已有业务库。SDK 使用替身及真实 RSA 运算，不访问企业微信。
[basic.py](examples/basic.py) 为真实 SDK 示例；[offline.py](examples/offline.py) 无需凭据；
[http_consumer.py](examples/http_consumer.py) 提供自定义 HTTP 消费者，需自行适配业务接口。

自动构建保留现有 Nuitka wheel 工作流；迁移脚本随包分发，`scripts/verify_wheel.py` 验证编译模块和迁移。

## 编译包的类型提示

类型信息通过包内的 `.pyi` 和空的 `py.typed` 文件分发，IDE 读取存根，运行时加载 Nuitka 编译模块。
修改公开接口后，在安装开发依赖的环境中，从源码重新生成存根，再构建 wheel：

```shell
stubgen --no-import --include-docstrings src/wecomarchive -o src
python -m build --wheel
```

生成后核对顶层导出、函数签名及 `Any` / `Incomplete` 类型；存根应随源码一起提交。
自动生成的存根是初稿，Pydantic 模型等动态接口需要人工核对。
命令会覆盖现有存根，手工补充的类型需要在重新生成后保留。
`pyproject.toml` 的 `package-data` 显式包含根目录、`db` 和 `services` 的存根；新增子包时也要更新此列表。
禁用 Nuitka 自带的入口 `.pyi` 生成，统一使用这套完整的包内存根。
发布前检查 wheel 内包含 `wecomarchive/py.typed`、`wecomarchive/__init__.pyi` 以及各子模块存根，
并在源码目录外安装 wheel，验证 IDE 补全和类型检查。

## 日志

构造主服务时在用户家目录 `wecomarchive` 中配置 UTF-8 文件日志。
`wecomarchive.log` 保存 INFO/WARNING，单文件 10 MiB，保留 5 个轮转备份；
`error.log` 追加 ERROR/CRITICAL，包含各阶段失败；同时保存 pyweworkfinance 的 WARNING 及以上日志。
重复配置不重复添加处理器，已有应用日志配置继续生效。错误只记录消息 ID、消费者名称及异常类型，不记录明文或私钥。
