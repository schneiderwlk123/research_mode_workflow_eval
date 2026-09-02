# Project Status

更新时间：2026-09-02

## 当前里程碑

评测框架基础骨架和首个确定性指标“调用事件完整率”已经实现。

已完成：

- Python 3.11+ 项目和 pytest、Ruff、mypy 工具链配置。
- `BaseEvaluator`、`EvaluationResult`、`EvaluationStatus` 公共契约。
- 公共契约及首个指标行为测试，共 23 项；最近一次验证全部通过。
- `collectors`、`evaluators`、`reporters`、`storage` 包入口。
- 代码地图和 43 项逐指标开发顺序；时延、Token 消耗及成本类指标统一置于最后阶段。
- MCP 工具手册已复制到项目，当前文件名为 `mcp_tool_manual.json`。
- 聊天与研究工具调用事件的最小规范化切片，不保留工具参数或响应正文。
- `tool_call_event_completeness_rate` 评测器，版本 `1.0.0`。
- 集中式指标规格 `docs/specs/metrics.md`，当前记录调用事件完整率定义与字段映射。

## 当前状态

- 最近完成的具体指标：调用事件完整率已实现并通过二次验收。
- 已暂时跳过指标：调用成功率、异常率、重试成功率、重试率、平均重试尝试次数；
  相关实现、测试和专用规格已按用户要求删除。
- 下一指标：无效重复调用率。
- 下一动作：展示无效重复调用率的公式、字段映射、异常边界和模拟计算，收到明确确认后再实现。

## 已知缺口

- 聊天模式 JSONL 与研究模式 JSON 的完整统一 Trace schema 尚未定义；当前只完成工具
  调用事件完整率所需的最小规范化切片。
- collectors、聚合器、报告器、配置加载器和可运行 CLI 尚未实现。
- Golden Benchmark、Judge、价格表和交付物读取能力尚未接入。
- 聊天模式缺少完整 E2E 时间与 Token 字段，部分指标只能返回不可计算。

## 验证基线

```text
pytest: 23 passed
ruff check: passed
ruff format --check: passed
mypy src tests: passed
现有研究日志结构验算: 30/30 = 1.0, computed
```
