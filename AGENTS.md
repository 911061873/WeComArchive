# 项目约定

- 注释、报错信息和 Git commit 消息，除专有名词外，默认使用中文。
- Git 提交消息遵循 Conventional Commits：`<type>[可选作用域][可选 !]: <中文描述>`，例如 `fix(service): 修复停止时事件循环为空的问题`。
- 提交类型使用 `feat`（功能）、`fix`（修复）、`docs`（文档）、`refactor`（重构）、`perf`（性能）、`test`（测试）、`build`（构建或依赖）、`ci`（持续集成）、`chore`（维护）、`style`（格式）或 `revert`（回退）；根据实际变更选择类型。
- 提交描述应简洁、准确地说明变更；不兼容变更使用 `!` 或正文中的 `BREAKING CHANGE:` 标记，正文说明及页脚说明默认使用中文。
