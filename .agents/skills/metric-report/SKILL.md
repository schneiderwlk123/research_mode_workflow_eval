---
name: metric-report
description: "Generate a self-contained HTML metrics report (charts embedded) from the wide-table CSV data/processed/metric_table_full.csv. Use when the user asks for a metrics/指标 analysis report, tool ranking charts, percentile tables, or bad-case insights from the research-mode wide table."
---

# Metric Report

生成研究模式指标宽表的自包含 HTML 报告(图表内嵌 base64 PNG),单数据源为
`data/processed/metric_table_full.csv`,零回源(不访问 OSS)。

## 何时使用

用户要求:出指标报告、两版指标对比、工具耗时/Token 占比排名、分位数统计、
bad case 洞察、Agent 迭代建议——且已有 `metric_table_full.csv` 宽表。

## 前置

- 数据源:`data/processed/metric_table_full.csv`(4 身份列 + 注册表全部指标列)。
  若列集变了,同步 `scripts/generate_report.py` 里的列集常量:
  `SCALAR_COLS`(标量)、`PER_TOOL_COLS`(逐工具 JSON 列表)、`JSON_SCALAR`(参与稳定性统计的
  单元素 JSON 列)、`JSON_EXTRA_COLS`(只进第 0 节可计算性清单的其余 JSON 列)。
- 绘图依赖 matplotlib + numpy,**项目 venv 未装,须用 anaconda python**:
  `C:/Users/EDY/anaconda3/python.exe`(已验证含 matplotlib 3.11 / numpy 2.4)。
- 中文绘图字体配置为 Microsoft YaHei 优先(SimHei 无 bold 字重,加粗会静默回退)。

## 运行

```
"C:/Users/EDY/anaconda3/python.exe" -X utf8 \
  .agents/skills/metric-report/scripts/generate_report.py \
  --csv data/processed/metric_table_full.csv \
  --output reports/metric_report.html
```

产出 `reports/metric_report.html`,浏览器直接打开,图表内嵌、无外部依赖。

## 报告结构(六节)

0. 样本与可计算性 —— 日志总数/用户数/会话数/模式 + 逐指标可计算日志数表(2026-09-23 需求方确认新增)。
1. 两版指标横向对比 —— 三列表格(保留 10 / 删除 3 / 新增 7,只写中文指标名),下方附
   新增指标的"含义与取值举例"注释表(2026-09-23 需求方确认改为表格,原来的柱状演进图已移除)。
2. 宽表指标统计总结 —— 标量指标 p50/p80/p90/p95/p99 表(比率列按百分比两位小数,整数列
   保持整数;工具调用总次数按 2026-09-23 需求方要求不进该表)+ 2×2 四宫图:每行一个指标
   (总 Token / 总时延),左列等宽分箱频次直方图(纵轴上界截断到 p99 屏蔽离群值,未入图条数
   红字标注),右列只画 p50/p80/p95/p99 虚线刻度线 + 加粗数值标签(白底、不画条形),
   两列共用同一线性数值纵轴;下方接稳定性信号分布图/表(2026-09-23 需求方确认:剔除
   原「API重试率>0.4」与「出错未恢复」两项,保留 5 个信号;2026-09-24 新增
   `其中未闭环` / `死亡富集` 两列并按富集倍数写明读法)。
3. 分布与排名 —— 3.1 工具耗时排名、3.2 工具频率排名、3.3 模型层错误分类与路由、
   3.4 框架 API 重试归因(`api_retry_attribution` 的 by_tool / by_endpoint / retry_after_success)。
   (3.3「框架 API 重试次数排名(Top20)」于 2026-09-23 应需求方要求整节移除;
   逐工具表列集同日改为 `出现日志数 + p50/p80/p90/p95/p99 + 均值`,移除原「中位/最大」;
   3.3 于 2026-09-24 补「占比高 ≠ 稳定性风险高」读法注。)
4. bad case 洞察 —— 死亡日志(故障) vs 高危日志(消耗)双主体归因 + 工具热点 +
   4.5 步数分档风险曲线(2026-09-24 新增,是 5.3 刹车建议的依据)。
5. Agent 迭代建议 —— 止血 / 降本 / 提效三层递进,数字由脚本实时计算注入。
   (5.3 于 2026-09-24 由「给检索配台账」改题为「给长日志装刹车」:原主题的前提
   `research_query 在框架层重复里排前列`经复核为 0 次、结论自相矛盾,且框架层重复
   实际由 mkdir/rm/write 等文件操作主导,故整条建议按实测数据重写。)

## 待补

- `tool_error_attribution`(失败工具名 + 归因分类)目前只进第 0 节可计算性清单,
  没有专属展示节——`ToolErrorRateEvaluator` 早已在 metadata 产出 `error_calls`,
  但直到 2026-09-24 才注册为宽表列。第 4.3 节「工具侧热点」现在仍只用
  `per_tool_call_latency_ms` 按耗时定位热点,尚未按失败工具名做归因排名;
  补该节时数字同样必须实时计算。

## 术语约定(2026-09-23 需求方确认)

- 全报告统一以**日志**为计数单位,**不使用"回合"计数**:文案写"X 条日志 / 日志数",
  表头写"出现日志数 / 日志数"。
- 例外:指标名保留权威口径原名"回合完成率"(`初步入库评测指标.md` 7.1),不因本约定改名。
- 第 4/5 节的洞察数字必须由脚本实时计算,禁止硬编码常量(阈值如 20min/p90 除外)。

## 关键口径(与 PROJECT_STATUS.md 对齐,不得擅改)

- 高危日志(报告第 4 节口径)= 闭环 ∧ 业务错误数 be≥3(即 `api_retry_rate` 单元格的
  `api_business_error_count >= 3`),由 2026-09-24 需求方确认(取代旧「压缩>0 ∨ 重试>0.6」);
  死亡日志 = turn_completion_rate==0。即使新版 `api_retry_rate` 口径下"重试率"对死亡
  几乎无判别力,be≥3 仍对高消耗(3.6x)与死亡(2.4x)双重富集,故门槛取 be 而非 rate。
- **死亡富集**(2.3 表与各节倍数)= P(未闭环 | 命中该信号) ÷ P(未闭环 | 全样本),
  基线由 `base_death_rate` 单点计算后贯穿全脚本,禁止各节各算一份。富集与频次是两个
  维度:低频信号(如压缩>0)可能富集最高,排查优先级按富集而非频次。
- "重试次数"指框架/工具传输层 API 重试(同一 `(callID, endpoint+入参指纹)` 重复,
  fingerprint_dup_v2),**不是模型级(LLM)重试**;模型级重试属埋点 D 当前不可观测,
  禁止混称。同理 `requestId` 尾序号是"调用内第 N 个请求"的工作流深度信号,不是重试次数。
- 指标列集以 `scripts/summarize_metric_table.py` 的 `build_metric_columns()` 为唯一事实来源;
  `per_tool_repeat_call_count` / `mcp_tool_call_inflation_rate` / `citation_traceability_rate`
  已退出宽表(评测器保留)。报告改列集时按下方「维护」同步四个列集常量,不要按记忆写死列数。
- 模型出错率的分母来自 `llm_error_count` 单元格的 `total_call_count` / `model_calls`
  (评测器 1.3.0 起),**不是**逐工具 JSON 那类可推导的分母;旧表缺该键时报告如实标注
  "调用数待补",不得用错误数硬算。
- 两列归因的**留空含义不同**,文案不得混用:零错误 / 零重试的日志在
  `tool_error_attribution` / `api_retry_attribution` 上留空,留空表示"没有该类事件",
  **不等于**该列指标为 0 或不可计算。
- 第 4/5 节的**所有**数字必须实时计算(含工具名清单与均值),禁止写死;需要新数字时
  在 `main()` 里先算好变量再插值,不要直接把样本结论敲进 f-string。

## 维护

- 新增/删除指标列:同步 `SCALAR_COLS`(标量)、`PER_TOOL_COLS`(逐工具 JSON 列表)、
  `JSON_SCALAR`(参与稳定性统计的单元素 JSON 列)、`JSON_EXTRA_COLS`(其余只进第 0 节
  可计算性清单的 JSON 列),并按列集变化同步第 1 节的 `EVOLUTION_KEPT` /
  `EVOLUTION_DELETED` / `EVOLUTION_ADDED` 与 `ADDED_NOTES`(新增指标必须补
  "含义与取值举例"一行)。漏同步的后果:第 0 节漏列该指标,或第 1 节列数与宽表不符。
- 模型错误分类目录:`.agents/skills/metric-report/scripts/generate_report.py` 的
  `LLM_ERROR_CLASS_CATALOG` 必须与 `src/collectors/llm_error_count.py` 的
  `_ERROR_CLASS_RULES_V1` 同步——第 3.3 节按该目录**穷举**分类(零命中类也列出),
  目录升版时两处一起改,否则报告会漏类或出现目录外类目。
- 柱状图数值标签一律放条尾、**不加白底**(2.3/3.1/3.2/3.3/3.4 同口径;白底框会在
  柱尾压出一块白色留白遮住柱体),留白由 `xlim` 余量提供;改标签格式或字号后需
  重新确认余量够放下最长标签。
- 正文逗号:写盘前 `_cn_commas` 把 `</style>` 之后的半角逗号统一为中文逗号
  (数字千分位如 1,234 受保护);图内文字是内嵌 PNG,须在绘图字符串里直接写中文逗号。
- 洞察文案是模板化的(数字实时算),文案措辞需人工复核后归档。
- 本技能无自动化测试(报告是可视化产物,断言成本高于收益),故**改完必须用真实宽表
  跑一次**并肉眼核对:第 0 节列数 == 宽表列数、第 1 节列集条数与宽表一致、第 3.3 节
  分类数与 `LLM_ERROR_CLASS_CATALOG` 一致。
