# 03 从宽表生成指标报告

宽表跑完后，用这个流程渲染成一份自包含 HTML 报告（图表内嵌 base64 PNG，浏览器直接打开，
无外部依赖、零回源）。

脚本：`.agents/skills/metric-report/scripts/generate_report.py`
技能说明：`.agents/skills/metric-report/SKILL.md`

> **技能发现路径提示**：`.agents/skills/` 不是 Claude Code 的技能发现目录（它只扫描
> `.claude/skills/` 与 `~/.claude/skills/`），所以本技能不会作为 `/metric-report` 斜杠命令
> 出现。实际用法是：按本手册直接跑脚本，或让 agent 用 Read 工具读 `SKILL.md`
> （`AGENTS.md` 里写的也是这种读法）。若要让 Claude Code 能按名调用，需要额外做符号链接
> `.claude/skills/metric-report` → `.agents/skills/metric-report`。

## 前置

**必须用 anaconda python，不能用项目 venv** —— venv 未装 `matplotlib` / `numpy`：

```powershell
C:\Users\EDY\anaconda3\python.exe -c "import matplotlib, numpy; print(matplotlib.__version__, numpy.__version__)"
# 3.11.0 2.4.6
```

中文字体按 `Microsoft YaHei` 优先（`SimHei` 无 bold 字重，加粗会静默回退到 400，
分位数标签会看不出来）。这台机器已具备，无需额外安装。

## 数据源

单数据源 `data/processed/metric_table_full.csv`，由
[02-run-wide-table.md](02-run-wide-table.md) 产出。当前 22 列 = 4 身份列 + 18 指标列。

## 运行

```powershell
"C:/Users/EDY/anaconda3/python.exe" -X utf8 `
  .agents/skills/metric-report/scripts/generate_report.py `
  --csv data/processed/metric_table_full.csv `
  --output reports/metric_report.html `
  --data-range 20260915-20260922
```

产出 `reports/metric_report.html`（gitignored），浏览器直接打开。

**`--data-range` 必填**（2026-09-24 需求方要求）：报告表头必须标明本批次日志的数据
日期区间，格式 `YYYYMMDD-YYYYMMDD`。宽表只有身份列 + 指标列、不存日期字段，无法从
CSV 推导，须按本次清单（02 步导出的 Excel）的实际日期范围显式传入。缺失或格式不符
脚本会直接报错退出——**没有数据日期的报告不允许产出**。

## 报告结构

| 节 | 内容 |
|---|---|
| 0 | 样本与可计算性：日志总数 / 用户数 / 会话数 / 模式 + 逐指标可计算日志数表 |
| 1 | 两版指标横向对比（保留 / 删除 / 新增）+ 新增指标的"含义与取值举例" |
| 2 | 宽表指标统计总结：分位数表（p50~p99）+ 总 Token / 总时延四宫图 + 稳定性信号分布 |
| 3 | 分布与排名：工具耗时、工具频率、工具错误归因、模型层错误分类与路由、框架 API 重试归因 |
| 4 | bad case 洞察：死亡日志 / 高危日志双主体归因 + 工具热点 + 步数分档风险曲线 |
| 5 | Agent 迭代建议：止血 / 降本 / 提效三层，数字全部由脚本实时计算注入 |

## 关键口径（不得擅改）

- **计数单位为"日志"**，不用"回合"。例外：指标名保留权威口径原名"回合完成率"。
- **死亡日志** = `turn_completion_rate == 0`（未闭环）。
- **高危日志** = 闭环 ∧ 业务错误数 `be >= 3`（`api_retry_rate` 单元格的
  `api_business_error_count`）。2026-09-24 需求方确认，取代旧口径"压缩>0 ∨ 重试>0.6"。
- **死亡富集** = P(未闭环 | 命中信号) ÷ P(未闭环 | 全样本)；基线由 `base_death_rate`
  单点计算贯穿全脚本。富集与频次是两个维度——低频信号可能富集最高，排查优先级看富集。
- **"重试次数"指框架 / 工具传输层 API 重试**（`fingerprint_dup_v2`：同一
  `(callID, endpoint+入参指纹)` 重复），**不是模型级重试**；模型级重试属埋点 D 当前不可观测。
- 第 3.4 节的 `api_retry_attribution` 归因单元格取 Top5；`tool_error_attribution` 不截断——
  两者口径刻意不同，不可互相解释。

## 改报告时的维护点

改列集或改指标目录时，以下必须同批同步，否则报告会漏类或错列：

| 改动 | 同步位置 |
|---|---|
| 增 / 删标量指标列 | `SCALAR_COLS`，并按需同步第 1 节 `EVOLUTION_KEPT` / `EVOLUTION_DELETED` / `EVOLUTION_ADDED` |
| 增 / 删逐工具 JSON 列 | `PER_TOOL_COLS`（当前只有时延与 Token 两列） |
| 增 / 删单元素 JSON 标量列 | `JSON_SCALAR`（用于第 0 节可计算性清单） |
| 新增指标 | 必须补 `ADDED_NOTES` 的"含义与取值举例"一行 |
| 模型错误分类目录 | `LLM_ERROR_CLASS_CATALOG` 必须与 `src/collectors/llm_error_count.py` 的 `_ERROR_CLASS_RULES_V1` 一致；第 3.3 节按该目录**穷举**（零命中类也列出） |

其余约定：

- 洞察文案里的数字**一律实时计算**，禁止硬编码；阈值（如 20min、p90）可写死但要在注释里说明。
- 柱状图数值标签放条尾、**不加白底**（白底框会在柱尾压出留白遮住柱体），留白靠 `xlim` 余量。
- 写盘前 `_cn_commas` 把 `</style>` 之后的半角逗号转中文逗号（千分位如 `1,234` 受保护）；
  图内文字是内嵌 PNG，须在绘图字符串里直接写中文逗号。

## 常见问题

| 现象 | 原因 / 处理 |
|---|---|
| `ModuleNotFoundError: matplotlib` | 用了 venv python，改 anaconda |
| 图表中文变方块 | 系统缺 `Microsoft YaHei`，改 `plt.rcParams["font.sans-serif"]` |
| 某列为空 / 报 KeyError | 宽表列集与脚本 `*_COLS` 不一致，按上表同步 |
| 第 3.3 节少一类 | `LLM_ERROR_CLASS_CATALOG` 与 collector 目录漂移 |
| 报告数字与预期不符 | 先确认宽表本身是否为最新列集（见 02 的断点续跑纪律） |
