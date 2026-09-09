# Start Here

本文件是新会话的稳定入口。每次开始项目工作时，按以下顺序恢复上下文：

1. `AGENTS.md`：仓库级开发、安全、测试、提交和逐指标确认规则。
2. `docs/PROJECT_STATUS.md`：当前完成度、下一步和已知缺口。
3. `docs/arch/code-map.md`：模块、入口、调用方向和测试位置。
4. `docs/specs/metric-development-order.md`：16 项指标的唯一开发顺序。
5. 当前任务直接相关的规格、测试和指标章节。

## 按需阅读

- `共性指标.md`：原始指标口径总文档。进入某个指标时只读取对应章节及统一统计约定。
- `docs/specs/metrics.md`：已确认指标的定义与字段映射汇总；按开发顺序持续追加。
- `mcp_tool_manual.json`：MCP 工具名称、参数和 schema 参考。只有字段映射涉及工具时才读取，避免每次加载大型文件。

## 不可破坏的项目约束

- 流水线固定为 `ingestion -> normalization -> evaluation -> aggregation -> reporting`。
- 原始 Trace 只读；不得把真实用户数据、密钥、原始 Trace 或生成报告提交到仓库。
- 一次只开发一个指标，不得并行实现多个具体指标。
- 指标编码前必须展示公式、字段映射、异常边界和模拟计算，并等待明确确认。
- 指标实现后必须展示测试和计算结果，等待二次确认后才能进入下一指标。
- 缺失输入必须显式标记为不可计算，不能用零值代替。
- 每个结果必须保留稳定指标名和 `evaluator_version`，保证可追溯和可复现。

## 启动检查

```powershell
git status --short --branch
.\.venv\Scripts\python.exe --version
```

只有开始修改代码或配置时，才需要按 `AGENTS.md` 宣布对应 creed-lite 路线。
