# Project Status

更新时间：2026-09-02

## 当前里程碑

评测框架基础骨架已经建立，尚未实现任何具体指标。

已完成：

- Python 3.11+ 项目和 pytest、Ruff、mypy 工具链配置。
- `BaseEvaluator`、`EvaluationResult`、`EvaluationStatus` 公共契约。
- 公共契约测试，共 11 项；最近一次验证全部通过。
- `collectors`、`evaluators`、`reporters`、`storage` 包入口。
- 代码地图和 48 项逐指标开发顺序。
- MCP 工具手册已复制到项目，当前文件名为 `mcp_tool_manual.json`。

## 当前状态

- 进行中的具体指标：无。
- 下一指标：调用事件完整率。
- 下一动作：先提交该指标的数学公式、聊天/研究日志字段映射、异常边界和模拟计算，等待用户确认；确认前不得创建指标实现代码。

## 已知缺口

- 聊天模式 JSONL 与研究模式 JSON 的统一规范化 Trace schema 尚未定义。
- collectors、聚合器、报告器、配置加载器和可运行 CLI 尚未实现。
- Golden Benchmark、Judge、价格表和交付物读取能力尚未接入。
- 聊天模式缺少完整 E2E 时间与 Token 字段，部分指标只能返回不可计算。

## 验证基线

```text
pytest: 11 passed
ruff check: passed
ruff format --check: passed
mypy src tests: passed
```
