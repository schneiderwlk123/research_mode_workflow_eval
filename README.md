# Full Trace Audit

面向 Agent Workflow 的全链路评测项目。目标是覆盖 Agent 一次运行中的输入、规划、模型调用、工具调用、工具结果利用、异常恢复、最终输出和评测报告。

当前仓库已建立评测器公共契约、开发工具链与指标宽表流水线，已实现工具调用、成本、时间、
Agent 解析、容错纠错、回答质量和工作流稳定性七个维度的指标。

新会话开始时先阅读 [Project Status](docs/PROJECT_STATUS.md) 和 [Code Map](docs/arch/code-map.md)，再进入具体任务。

## 项目结构

```text
configs/                 评测配置样例
data/raw/                本地原始 trace（不提交）
data/processed/          本地规范化数据与指标宽表（不提交）
docs/arch/               架构设计、日志 schema 与代码地图
docs/runbooks/           日常作业手册（打捞日志 / 跑宽表 / 出报告）
scripts/                 OSS 取数、宽表汇总与归因回填入口
src/collectors/          数据采集与格式适配
src/evaluators/          评测器公共契约与逐指标实现
src/reporters/           指标宽表汇总与报告生成
src/storage/             存储访问
tests/                   与 src 结构对应的行为测试
artifacts/               本地运行产物（不提交）
reports/                 本地评测报告（不提交）
```

## 数据与安全

原始 trace 可能包含用户问题、模型输出、工具参数及外部文档内容。仓库默认忽略所有 JSONL 文件，仅允许显式提交 `tests/fixtures/` 下的脱敏数据。日志和报告中不得写入密钥、Cookie 或未脱敏的个人信息。

协作约定见 [AGENTS.md](AGENTS.md)。正式开发前应先在 `docs/arch/` 与根目录《初步入库评测指标.md》中确认架构、输入 schema、指标口径和验收标准；后者是全部评测口径的唯一来源，其文末附录含宽表列契约与已取消/暂缓指标登记。

## 开发命令

```powershell
# 安装项目和开发依赖（跑 OSS 脚本还需 oss 附加依赖）
.\.venv\Scripts\python.exe -m pip install -e ".[dev,oss]"

# 测试（系统临时目录存在访问锁，需指定仓库内 basetemp）
.\.venv\Scripts\python.exe -m pytest --basetemp=.pytest_tmp -q

# 静态检查与格式校验
.\.venv\Scripts\ruff.exe check .
.\.venv\Scripts\ruff.exe format --check .

# 类型检查
.\.venv\Scripts\mypy.exe src tests
```

## 日常作业

三段日常作业各有操作手册，含参数、断点续跑纪律与常见问题：

- [打捞日志](docs/runbooks/01-salvage-logs.md)：按 uid + 日期 + 问题开头从 OSS 捞单条日志定因。
- [全量跑宽表](docs/runbooks/02-run-wide-table.md)：按清单流式计算研究模式全量指标，产出
  `data/processed/metric_table_full.csv`（日志不落盘）。
- [生成报告](docs/runbooks/03-generate-report.md)：从宽表渲染自包含 HTML 指标报告。

宽表的列集由 `scripts/summarize_metric_table.py` 的 `build_metric_columns()` 单点定义，
结果 CSV 表头随注册表自动同步。注册表当前为 19 个指标列，另有 4 个身份列
（`user_id`、`session_id`、`log_id`、`mode`），每个指标只占一个值列，诊断统计不混入指标字段；
逐工具 Token / 时延与归因类单元格以紧凑 JSON 数组写入单个 CSV 单元格。
新增指标只需增加一个 `MetricColumn` 注册项。
