# 客户联系数据库结构

数据库保存单企业最近观测到的资料，不维护历史版本，不保存规则组。ORM 定义位于 `src/wecom_archive/customer_sync/models.py`。

| 表 | 主键 | 内容 |
| --- | --- | --- |
| `contact_users` | `userid` | 配置客户联系功能的成员 ID |
| `customers` | `external_userid` | 客户基础信息、`external_profile`、原始字段 |
| `customer_follows` | `external_userid, userid` | 跟进关系、备注、手机号、视频号、批量及详情来源数据 |
| `customer_follow_tags` | `external_userid, userid, ordinal` | 跟进关系的当前标签集合，个人标签 ID 可以为空 |
| `group_chats` | `chat_id` | 群信息、`owner_userid`、`follow_status`、列表及详情原始数据 |
| `group_members` | `chat_id, userid, type` | 群成员资料、昵称、入群信息及邀请者 |
| `group_admins` | `chat_id, userid` | 管理员及完整原始字段 |
| `customer_directory_sync_runs` | `id` | 同步领域、状态、范围、计数及结构化失败信息 |

## 字段及关联约定

实体表通过 `ObservationMixin` 统一维护 `first_seen_at`、`last_seen_at`、`last_seen_run_id`、`is_active`。标签集合使用 `fetched_at`，同步记录使用开始及结束时间。应用写入本地时间时统一使用 UTC，SQLite 读回时间对象可能不带时区；接口时间戳列使用 `BigInteger`。

跟进关系外键关联客户，标签通过复合外键关联跟进关系，群成员及管理员关联客户群。成员、群主、操作人不强制关联 `contact_users`，群外部联系人也不强制关联 `customers`。索引支持按跟进成员、群主、群成员身份及标签 ID 查询。

客户扩展信息使用 JSON；跟进关系保存 `wechat_channels`、`remark_mobiles`。单客户详情和批量接口的数据分别保存在 `raw_detail_data`、`raw_batch_data`，并各自记录采集时间。群列表与详情分别保存在 `raw_list_data`、`raw_detail_data`。JSON 保存未知字段；Pydantic 序列化使用 `exclude_unset=True`，不把模型默认值伪装成上游实际返回的数据。

`customers.raw_data` 保留按实际返回字段合并的最近观测值。客户和跟进关系仅更新实际返回的字段；显式空值仍可清空。标签序号只区分当前集合中的记录，不是永久标签身份。详情中明确返回 `tags` 时，事务替换对应跟进关系的标签集合；未返回该字段时保留，空数组则清空。批量接口不改动详情标签表。

## 仓储入口与同步边界

- `upsert_customer_items()` 保留现有批量事务写入方式。
- `upsert_contact_users()` 保存观测到的成员，不隐式清理其他成员。
- `upsert_customer_detail_page()` 保存单页客户详情及该页跟进关系的标签；分页完整性由调用方负责。
- `upsert_group_chat()` 保存群列表条目、群详情、成员及管理员。
- `start_run(scope=...)` 和 `fail_run(failure_details=...)` 支持记录同步范围及部分失败信息。

自动客户同步先保存客户联系成员，再按成员分组批量采集客户并去重客户 ID，最后逐客户拉取全部详情页和标签。批量及详情阶段使用固定数量的工作协程，每页写入共享单写入锁。所有阶段成功后，客户、跟进关系和客户联系成员才会执行未见记录软失活。失败或取消保留已完成写入，不进行全局清理。批量响应的 `fail_info` 连同成员分组、失败阶段保存到 `failure_details`。

客户群同步仍按客户联系成员列表过滤群主，再保存群详情、成员和管理员；离职群主完整范围仍需后续接入，不能将当前范围称为企业全部群。`sync_all_once()` 保持客户、客户群依次同步，两个领域分别记录结果。

空 `scope` 表示现有完整可见范围扫描。全局收尾仅接受同领域、运行中且没有局部过滤范围的同步记录；局部范围及失败运行禁止执行全表软失活。局部范围收尾后续单独实现。`is_active` 表示同步可见性，不能用来确认删好友、退群或解散事件。

公开查询对象保留 `owner`、`status`、`tags`，同时增加与 ORM 一致的 `owner_userid`、`follow_status`。`tags` 从标签明细表读取非空标签 ID；`tag_details` 提供包含个人标签的完整集合，不再从批量原始 JSON 推导标签。客户和跟进对象补齐公司、扩展属性、手机号、渠道、视频号、两种来源数据和采集时间；群对象补齐成员版本、两种原始数据、管理员对象，群成员对象包含邀请者。

`get_customer()`、`get_group_chat()` 保留最后观测的非活跃客户/群及成员关系，关系本身带 `is_active`；群管理员列表保持仅返回活跃管理员。标签独立查询返回该关系最后保存的集合，是否仍有效请结合跟进关系的 `is_active` 判断。

## 初始化与验证

Alembic 开发期初始化从当前 ORM metadata 建表；不提供旧 schema 到本次结构的原地升级。本次未迁移、删除或重建已有数据库。使用本版本需要新建数据库，或由使用者在确认不需要保留旧开发数据后自行重建。

测试覆盖 SQLite 新库读写及外键、标签替换和缺失字段语义、原始数据保留、群内外成员、结构化失败和全局收尾保护。MySQL/PostgreSQL 仅验证 DDL 编译，尚未完成真实服务端读写联调。
