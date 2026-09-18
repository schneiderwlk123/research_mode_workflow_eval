# Full Trace Audit

面向 Agent Workflow 的全链路评测项目。目标是覆盖 Agent 一次运行中的输入、规划、模型调用、工具调用、工具结果利用、异常恢复、最终输出和评测报告。

当前仓库已建立评测器公共契约与开发工具链，具体指标将按确认后的顺序逐项引入。

新会话开始时先阅读 [Start Here](docs/START_HERE.md) 和 [Project Status](docs/PROJECT_STATUS.md)，再进入具体任务。

## 项目结构

```text
configs/                 评测配置样例
data/raw/                本地原始 trace（不提交）
data/processed/          本地规范化数据（不提交）
docs/arch/               架构设计
docs/specs/              评测规范和指标定义
src/collectors/          数据采集与格式适配（待实现）
src/evaluators/          评测器公共契约与逐指标实现
src/reporters/           指标宽表汇总与报告生成
src/storage/             存储访问（待实现）
tests/                   与 src 结构对应的行为测试
artifacts/               本地运行产物（不提交）
reports/                 本地评测报告（不提交）
```

## 数据与安全

原始 trace 可能包含用户问题、模型输出、工具参数及外部文档内容。仓库默认忽略所有 JSONL 文件，仅允许显式提交 `tests/fixtures/` 下的脱敏数据。日志和报告中不得写入密钥、Cookie 或未脱敏的个人信息。

协作约定见 [AGENTS.md](AGENTS.md)。正式开发前应先在 `docs/arch/` 和 `docs/specs/` 中确认架构、输入 schema、指标口径和验收标准。

## 开发命令

```powershell
# 安装项目和开发依赖
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"

# 测试
.\.venv\Scripts\python.exe -m pytest

# 静态检查与格式校验
.\.venv\Scripts\ruff.exe check .
.\.venv\Scripts\ruff.exe format --check .

# 类型检查
.\.venv\Scripts\mypy.exe src tests

# 把聊天与研究日志汇总为一行一个 Trace 的指标宽表
.\.venv\Scripts\python.exe -m scripts.summarize_metric_table `
  --chat-log data/raw/<chat-log>.json `
  --research-log data/raw/<research-log>.json `
  --output data/processed/metric_table.csv
```

宽表当前注册 `tool_calls_count`、`per_tool_repeat_call_count`、`per_tool_call_tokens`、
`total_token_consumption`、`max_tool_token_share`、`output_token_ratio`、
`cache_input_token_ratio`、`per_tool_call_latency_ms`、`end_to_end_latency_ms`、
`max_tool_latency_share` 和 `mcp_tool_call_inflation_rate`。膨胀率列依赖仓库根
`mcp_tool_manual.json` 构建的能力目录（可用 `--mcp-manual` 覆盖，手册缺失或非法时直接
报错），以 `[0,1)` 区间内的比率数值写入，零膨胀输出 `0`；手册外工具调用与异步任务
轮询调用（入参恰为 `taskId` 单键）不进入分子和分母。后续
指标实现后，只需增加一个 `MetricColumn` 注册项；CSV 中
固定输出 `user_id`、`session_id`、`log_id`、`mode` 标识列，每个指标只占一个值列，诊断统计
不会混入指标字段。逐工具重复次数和逐工具 Token 均以紧凑 JSON 数组写入各自的单个 CSV
单元格；最大工具 Token 与最大工具时延占比均以包含 `tool_name` 和比率的紧凑 JSON 数组写入
单个单元格；输出
Token 比率直接写入数值。逐工具 Token 或时延缺失时保留 `null`，安全原因、状态、覆盖计数和
evaluator 版本仅进入表外诊断。`log_id` 在聊天模式
读取 `canvas.meta.traceId`，在研究模式读取用户级
`userMessageID`（兼容 `body.messageID`）；不会使用研究模型步骤的 `part.messageID`。完整评测
流水线入口仍待后续实现。
