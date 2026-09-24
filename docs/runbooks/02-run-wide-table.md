# 02 按时间范围全量取流跑宽表

拿到"某段时间的研究模式清单"后，用这个流程在**不把原始日志下载到本地**的前提下，
边取流边算指标，产出一行一条日志的宽表 CSV。

脚本：`scripts/stream_compute_metrics.py`
入口：`python -X utf8 -m scripts.stream_compute_metrics`

## 输入：清单文件

平台导出的清单，至少含 `uid` / `session_id` / `msg_id` 三列（其余列忽略）：

```text
uid,session_id,msg_id,total_tokens,chat_mode,create_time
"4210151","ses_...","msg_...","986531","2","2026-09-20 11:19:59"
```

**必须是 `.xlsx`**：`read_rows()` 用 openpyxl 读，不接受 CSV（报
`InvalidFileException: openpyxl does not support .csv file format`）。
若手上是 CSV，先在 Excel 里另存为 xlsx。

时间范围由清单本身的 `create_time` 决定，脚本不做日期过滤——要限定范围就在导出清单时筛好。

## 运行

```powershell
.\.venv\Scripts\python.exe -X utf8 -m scripts.stream_compute_metrics `
  --excel data/raw/<清单>.xlsx `
  --output data/processed/metric_table_full.csv `
  --workers 8
```

`--output` 默认就是 `data/processed/metric_table_full.csv`，平时可省略。

### 建议先探规模

清单去重后可能有数千个 msg，对应日志总量可达数十 GB。先跑一次只 list 不下载的探针脚本
（`.tmp/list_research_prefixes.py`，一次性脚本，未进 `scripts/`）拿到总字节、单文件
最大值与 query/noquery 版分布，再决定并发数：

- `--workers` 过大 + 单个日志文件很大 → 工作线程内存被挤爆（整条日志的事件列表驻留内存）。
- 探针输出里的"仅有 noquery 版"会被本脚本跳过，是预期内的损耗。

## 运行机制（决定续跑行为）

对清单里每个 `(uid, session_id, msg_id)`：

1. 按 `<根前缀>/<uid>/<session_id>/<msg_id>` list OSS 对象；
2. 从"带 query 版"（文件名不含 `_noquery_`）里取时间戳最新的一个；只有 noquery 版则跳过并记
   `no_query_version`；
3. `get_object` 取流，内存里逐行解析事件，复用 `summarize_metric_table` 的
   `METRIC_COLUMNS` 算出一行，追加写结果 CSV——**原始日志不写本地磁盘**；
4. 单条失败（list / 取流 / 解析 / 计算）隔离成 `issues.jsonl` 里一条诊断，不中断整批。

结果 CSV 表头 == 身份列（`user_id` / `session_id` / `log_id` / `mode`）+ 当前
`METRIC_COLUMNS` 的全部指标列，随注册表自动同步。`log_id` 取用户消息 ID。

## 断点续跑（重要）

**续跑只按 `log_id` 跳过，不校验表头。** 这带来两条纪律：

- 跑到一半失败，直接重跑即可，已完成的行会被跳过，只补未完成部分。
- **列集变更（增列 / 删列 / 改列含义）后，必须先删除旧结果 CSV 再重跑**，
  否则新旧列集混拼成一张表，且不会报错。

driver 脚本（`--excel` 清单路径、环境变量、并发数）记在本目录或提交信息里，
便于复现同一批结果。一次性驱动脚本放 `.tmp/`（gitignored）；要长期复用的才移进 `scripts/`。

## 产出

| 路径 | 内容 |
|---|---|
| `data/processed/metric_table_full.csv` | 一行一条日志；列为 4 个身份列 + `build_metric_columns()` 注册的全部指标列（当前 19 个指标列，共 23 列） |
| `data/processed/issues.jsonl` | 逐条失败诊断；正常收尾时也要看一眼计数 |

跑完脚本会打印 `完成: {ok, skipped_done, missing, no_query_version, error}, ...`；
若 `ok + skipped_done != total`，进程以退出码 1 结束——这不是崩溃，是要你看 issues。

### 归因列的历史遗留

`api_retry_attribution`（框架层 API 重试归因）与 `tool_error_attribution`（工具错误归因）
已注册进 `build_metric_columns()`，**新的全量重跑会直接产出这两列**，无需额外步骤。

`scripts/backfill_*.py` 只用于补那些在列注册**之前**就已经算好的旧表：只回 OSS 取流读
对应归因、不重算其它列、按新列是否非空断点续跑、temp + rename 原子替换。如果 CSV 已有
这两列且非空，就不要重复 backfill。

## 验收看点

跑完先做三项自查，再进报告：

```powershell
# 1. 行数与 log_id 唯一性（每条日志一行，且无重复）
# 2. 列数与 build_metric_columns() 注册表一致
#    = 4 身份列 + 注册的指标列数；任何一列缺失都说明列集漂移
# 3. 两列归因的非空行数是否等于对应分母行数
#    api_retry_attribution 非空 == api_retry_rate > 0 的行数
#    tool_error_attribution 非空 == tool_error_rate 的 error_call_count > 0 的行数
```

## 常见问题

| 现象 | 原因 / 处理 |
|---|---|
| `openpyxl does not support .csv` | 清单是 CSV，另存为 xlsx |
| `Excel 中未解析到有效行` | 三个必需列有空值，或表头不在前三列 |
| 结果是空的 / 行数远少于预期 | 清单过的是聊天模式导出，本脚本只处理研究模式事件流 |
| 内存暴涨 | `--workers` 偏高，降到 4~8 |
| 报缺少 OSS 配置 | `.env` 没配 `OSS_ENDPOINT` / `OSS_BUCKET` |

## 下一步

宽表就绪后走 [03-generate-report.md](03-generate-report.md) 出报告。
