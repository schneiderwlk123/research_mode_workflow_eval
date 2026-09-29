# 04 生成 bad case Excel

宽表跑完后，用这个流程产出 bad case 打捞表（`data/processed/badcase_top200.xlsx`），
供人工复查与派单。

脚本：`scripts/build_badcase_workbook.py`
上游：[02-run-wide-table.md](02-run-wide-table.md) 产出的 `metric_table_full.csv`

## 前置

无额外依赖，项目 venv 已装 `openpyxl`：

```powershell
.\.venv\Scripts\python.exe -c "import openpyxl; print(openpyxl.__version__)"
# 3.1.5
```

不需要 OSS 凭据——本脚本只读宽表 CSV，零回源。

## 运行

```powershell
.\.venv\Scripts\python.exe -X utf8 -m scripts.build_badcase_workbook `
  --csv data/processed/metric_table_full.csv `
  --output data/processed/badcase_top200.xlsx
```

两个参数都有默认值，等价于直接跑 `-m scripts.build_badcase_workbook`。
输出摘要形如：

```json
{"dead_count": 257, "high_count": 426, "row_count": 7582, "sheet_count": 4}
```

## 产出的 4 个 sheet

| sheet | 行数 | 内容 |
|---|---|---|
| `token_top200` | 201（含表头） | `total_token_consumption` 从高到低 Top200 |
| `cache_miss_top200` | 201 | `cache_input_token_ratio` 从低到高 Top200（缓存命中最差在前） |
| `latency_top200` | 201 | `end_to_end_latency_ms` 从高到低 Top200 |
| `badcase` | 683 + 表头 | 死亡回合 + 高危回合，各带中文归因原因 |

三张 top200 表各 4 列（`uid` / `session_id` / `msg_id` / 主指标值）；`badcase` 8 列
（`分类` / `uid` / `session_id` / `msg_id` / `工具调用数` / `token消耗` / `重试率` /
`具体原因(中文)`）。两个集合各自按 token 消耗降序排列。

## 关键口径（不得擅改）

- **死亡回合** = `turn_completion_rate == 0`（未闭环）。
- **高危回合** = 闭环 ∧（业务错误数 `be >= 3` ∨ 工具错误数 `te > 10`）。
  阈值常量 `HIGH_BE_THRESHOLD` / `HIGH_TOOL_ERROR_THRESHOLD` 定义在本脚本内，
  与 `.agents/skills/metric-report/scripts/generate_report.py` 的同名常量**必须一致**——
  报告第 4 节与 bad case sheet 的高危口径不允许分叉，改一处必须同批改另一处。
- **判定顺序：死亡优先**。已判为死亡的回合不再进高危集合，两类互斥、同一 `msg_id`
  只出现一行。这是 2026-09-22 需求方确认的既有顺序。
- **原因列**由宽表单元格实时推导，不引入宽表外数据。死亡侧按 `工具错误 > 模型错误 >
  重试率 > 无信号` 依次判定；高危侧命中 `be` 与/或 `te` 时合并为单列陈述（用 `;` 连接），
  不拆成多行。
- **留空含义**：`重试率` 列为空表示该日志的 `api_retry_rate` 单元格为空（无该事件或
  不可计算），**不等于**重试率为 0。

## 与历史 `.tmp` 脚本的关系

本脚本取代 `.tmp/` 下的四个接力脚本：

| 旧脚本 | 原职责 | 现状 |
|---|---|---|
| `make_badcase_top200.py` | 建三张 top200（`Workbook()` 从零新建） | 已被取代 |
| `append_badcase_3dim.py` | 追加 `badcase_3dim` 中间表 | 已被取代，且该表早已被下游删除 |
| `append_insight_sheets.py` | 追加 `deadturn_attribution` / `highrisk_cost_profile` | 同上 |
| `rebuild_badcase_sheet.py` | 读现有文件追加 `badcase` | 已被取代 |

旧链路的隐患：三张 top200 由 `Workbook()` **从零新建**，而 `badcase` 由
`load_workbook()` **读现有文件追加**——重建必须先跑前者再跑后者，顺序错了不报错，
只会静默留下"新 top200 + 旧 badcase"的混合文件。新脚本一次构建整个工作簿，消除该依赖。

## 改脚本时的维护点

- 改判定口径（阈值、死亡/高危定义、判定顺序）→ 同批改
  `generate_report.py` 的同名常量与 `.agents/skills/metric-report/SKILL.md` 的「关键口径」。
- 改原因文案模板 → 产出与历史文件不再逐字一致，重建后需重新人工复核一次文案。
- 改任何列集 / 列宽 / 行数 → 同批更新本文件的「产出的 4 个 sheet」表。

## 验证（无自动化测试，改完必须跑）

本脚本无单元测试（产出是可视化工作簿，断言成本高于收益）。改完必须做**横向比对**：
用基线文件与新产出逐单元格比对，要求零差异。

```powershell
# 1. 备份当前文件作为基线
Copy-Item data\processed\badcase_top200.xlsx .tmp\badcase_baseline.xlsx
# 2. 跑新脚本到临时路径，不要覆盖基线
.\.venv\Scripts\python.exe -X utf8 -m scripts.build_badcase_workbook --output .tmp\badcase_new.xlsx
# 3. 逐 sheet 逐 cell 比对（值 / 类型 / 表头加粗 / 列宽 / 对齐）
```

比对必须覆盖：sheet 名单与顺序、每个 sheet 的行列尺寸、**每个单元格的值与 Python 类型**、
表头加粗标志、`badcase` 的 A–H 列宽与 H 列 `wrap_text`/`vertical` 对齐。

> **字节级 sha256 比对不足以判断一致性。** openpyxl 写出的是 zip 包，其中的
> `docProps/core.xml` 含构建时间戳（每次运行都不同），`xl/workbook.xml` 等部件的
> 命名空间声明位置也随序列化路径变化——这些都不影响 Excel 打开后的内容。
> 判断"数据是否一致"必须看到单元格层；字节不同只说明序列化路径不同，不是缺陷。

## 常见问题

| 现象 | 原因 / 处理 |
|---|---|
| `KeyError: 'turn_completion_rate'` | 宽表列集与脚本不一致，先确认宽表是最新列集（见 02 的断点续跑纪律） |
| `dead_count` / `high_count` 与预期不符 | 先确认宽表本身是否为最新；高危数同时受两个阈值影响 |
| 原因列出现半角 `>=` | 应为全角 `≥`（与历史文件一致），检查 `_reason_high` 的格式化分支 |
| Excel 打开提示"格式不一致" | 忽略即可，openpyxl 写出的 xlsx 无 Excel 原生元数据 |
