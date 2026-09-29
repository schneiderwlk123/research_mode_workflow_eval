# Project Status

更新时间：2026-09-28

本文件只保留**现行**里程碑与验证基线。逐指标定义、历史基线与宽表列契约见根目录
《初步入库评测指标.md》（其文末附录为原 `docs/specs/metrics.md` 的迁入内容，该目录已删除）；
每批变更的动机与过程见 git 提交历史。2026-09-24 及更早的追加日志、已删除旧表
`metric_table.csv` 的 121 行基线已移除——旧数值一律不得再引用或与新表混比。

## 评测样本

- 现行基表：`data/processed/metric_table_full.csv`——7,582 行 × 23 列（4 身份列 +
  19 指标列），833 用户 / 4,463 会话，`log_id` 唯一无重复，全部 research 模式。
  由 `scripts/stream_compute_metrics.py` 对 7,663 个 OSS 前缀流式计算产出
  （ok 7,582 / missing 75 / no_query_version 6 / error 0），逐单元格诊断落
  `data/processed/issues.jsonl`（37,754 条，含各指标 `not_computable` 原因）。
- 聊天模式样本：`data/raw/agent_trace_test_20260916-20260916_20260917_174050.json`
  （74 条 Trace，数据日期 2026-09-16）。聊天指标基线仍基于该样本，未并入全量宽表，
  两套总体数值禁止混比。
- 已退役：旧聊天样本 336 条 Trace、研究早期单条日志、旧表
  `data/processed/metric_table.csv`（121 行）。相关历史数值只在
  `初步入库评测指标.md` 留档，不再作为当前基线。

## 当前里程碑

- 流水线 `ingestion -> normalization -> evaluation -> aggregation -> reporting` 已跑通；
  19 个指标列注册进宽表，列集由 `scripts/summarize_metric_table.py` 的
  `build_metric_columns()` 单点定义，表头随注册表自动同步。
- 19 个注册指标列（按表头顺序）：`tool_calls_count`、`per_tool_call_tokens`、
  `total_token_consumption`、`max_tool_token_share`、`output_token_ratio`、
  `cache_input_token_ratio`、`per_tool_call_latency_ms`、`end_to_end_latency_ms`、
  `max_tool_latency_share`、`answer_evidence_grounding_rate`、`turn_completion_rate`、
  `continuation_restart_count`、`llm_error_count`、`tool_error_rate`、
  `tool_error_attribution`、`error_recovery_rate`、`compaction_event_count`、
  `api_retry_rate`、`api_retry_attribution`。每个指标只占一个值列，诊断统计不混入
  指标字段；逐工具与归因类单元格为紧凑 JSON 数组。
- 报告链路：`.agents/skills/metric-report/`（`SKILL.md` + `scripts/generate_report.py`），
  从全量宽表渲染自包含 HTML（图表内嵌 base64），零回源。`--data-range` 必填，
  数据日期强制标进报告表头。
- 作业手册：`docs/runbooks/`（索引 + 打捞日志 / 全量跑宽表 / 生成报告）。
- 不注册宽表的报告级项：bad case 高危口径。
- 已退役指标（2026-09-29）：`high_risk_turn_profile`（7.11）。冻结阈值
  `step>30 ∧ api_retry_rate>0.4` 的标定基础是已退役的 `request_id_suffix_v1` 口径，
  切到 `fingerprint_dup_v2` 后全量重试率上限 0.3333，`>0.4` 结构上不可达
  （7,582 行命中 1 条、死亡富集 0.00x）。代码、测试与 `__init__` 导出已删除，
  登记见 `初步入库评测指标.md` 附 2.1。
- 已退出 CSV 注册（评测器与测试保留）：`per_tool_repeat_call_count`、
  `mcp_tool_call_inflation_rate`、`citation_traceability_rate`。

## 验证基线

```text
测试与静态检查（2026-09-28 实跑）:
  pytest 全量 997 项通过（--basetemp=.pytest_tmp）
  ruff check . 全仓通过；mypy src tests 通过（171 源文件）
  ruff format --check src tests scripts: 9 个既有文件待格式化（169 已格式化），
    非本批引入，未触碰
  规模：src 81 / tests 90 / scripts 7 个 .py

全量研究模式宽表 data/processed/metric_table_full.csv（7,582 行 × 23 列）:
  非空单元格 / 7582（按列）:
    turn_completion_rate 7582 (100%)   continuation_restart_count 7582 (100%)
    llm_error_count 7582 (100%)        compaction_event_count 7582 (100%)
    tool_calls_count 7510 (99.1%)      total_token_consumption 7429 (98.0%)
    output_token_ratio 7429 (98.0%)    cache_input_token_ratio 7429 (98.0%)
    end_to_end_latency_ms 7325 (96.6%) per_tool_call_tokens 6659 (87.8%)
    per_tool_call_latency_ms 6659 (87.8%) tool_error_rate 6659 (87.8%)
    max_tool_latency_share 6545 (86.3%)   max_tool_token_share 5829 (76.9%)
    answer_evidence_grounding_rate 5358 (70.7%)  api_retry_rate 4125 (54.4%)
    error_recovery_rate 1727 (22.8%)   tool_error_attribution 1624 (21.4%)
    api_retry_attribution 1191 (15.7%)
  空单元格均由 issues.jsonl 中登记的 not_computable 原因解释，不补零。

工具调用: computed 7,510，合计 166,508 次，中位 10、p90 54、max 2,029；
  per_tool_call_latency_ms 覆盖 6,659 行、40,571 个逐工具项、236 种工具名
Token: total_token_consumption computed 7,429，合计 33,383,404,124，中位 1,623,139，
  max 121,732,276；output_token_ratio 均值 0.0043 / 中位 0.0026；
  cache_input_token_ratio 均值 0.8972 / 中位 0.9634
时延: end_to_end_latency_ms computed 7,325，均值 388,506.95 ms、中位 242,719 ms；
  max_tool_token_share 均值 0.4776 / 中位 0.4662；
  max_tool_latency_share 均值 0.7081 / 中位 0.7193
回合完成率: 7,582 computed，闭环 7,325，完成率 96.61%
数值锚定率: 5,358 computed，均值 0.5702、中位 0.7130
模型层错误: 178 行非零，错误合计 230；同列 model_calls 合计 117,130 次模型调用
工具错误率: 6,659 computed，微平均 4,316 / 166,355 = 2.59%
框架 API 重试率: 4,125 computed，微平均 3,182 / 61,530 = 5.17%
错误自恢复率: 1,727 computed（had_error=1），恢复 1,519 = 87.96%
上下文压缩: 7,582 computed，51 行非零，事件合计 59
续跑重启: 7,582 computed，重启次数与 turn_start_count 全部为 0/1（重启 0 次）；
  原因是 `stream_compute_metrics.select_query_version` 对每个前缀只取一个
  "带 query 版"文件（`_noquery_` 续跑段仅在与 query 版二选一时被跳过），
  单文件内只出现一个 turn.start，故本表的续跑重启不可观测。
  跨文件续跑只能按 2026-09-17 小样本的多段合并口径算（见 `初步入库评测指标.md` 附录 §18），
  两套口径不得混比
```
