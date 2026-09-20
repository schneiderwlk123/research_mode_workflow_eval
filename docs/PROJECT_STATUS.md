# Project Status

更新时间：2026-09-20

## 评测样本（2026-09-17 换轨）

- 聊天模式最新评测样本：`data/raw/agent_trace_test_20260916-20260916_20260917_174050.json`，
  74 条 Trace（数据日期 2026-09-16），`canvas` 三键结构与旧样本一致。
- 研究模式最新评测样本：`data/raw/oss_jsonl` 下全部 51 个 JSONL 文件（16,100 事件），
  以 `turn.start` 的 `userMessageID` 为回合边界共 47 个回合、43 个完成（turn.end）、47 个
  query 可取；原 `data/oss_jsonl` 目录已迁移至 `data/raw/oss_jsonl`，内容同源。研究模式
  聚合必须按回合切分：25 个文件含多个回合（最多 10 个）、3 个回合跨多个文件（`_noquery`
  续跑段，时间区间互不重叠需按时间序合并去重）；回合数以 turn.start/turn.end 为准，
  不以 userMessageID 字段值去重数（121）为准。
- 新样本结构观察（未重算，先记录口径影响）：
  - 聊天 `meta.create_time` / `answer.create_time` 由秒级 Unix 时间戳变为
    `"YYYY-MM-DD HH:MM:SS"` 字符串（74/74）；`end_to_end_latency_ms` 聊天实现需兼容字符串
    解析，精度仍为秒级。
  - 聊天 `canvas.answer.usageTokens` 覆盖降为 72/74（2 条缺失该键）；Token 指标分母读
    message 层不受影响，缺失样本按既有 `no_observable_token_usage` 边界处理。
  - 聊天召回 payload 携带 `doc_id` / `chunk_index`（50/74 条），为单次检索 payload 内的
    局部计数；回答 `[n]` 引用（46 条、863 处）与 doc_id 对应关系成立，但多轮检索 Trace 的
    编号是模型侧累积重排，精确锚定需内容重建。
  - 研究模式新增确认：`llm.error` 事件 9 条（模型层失败信号）；工具终态存在
    `status='error'`（14 条）；chunkList 携带 `businessTime` 毫秒时间戳，数据日期级时效
    可算；检索工具输出自带 `id: [n] 标题:` 编号来源清单（单条日志实测 338 个编号文档），
    回答引用编号全部落在该范围内，引用可确定性锚定。
- 本文件"验证基线"及各指标文档中的历史数值均基于旧样本（聊天
  `agent_trace_pro_20260828-20260903` 336 条 Trace；研究模式早期单条日志），与新样本数值
  不得混比；新样本重算完成后在本节登记新基线。

## 当前里程碑

评测框架基础骨架、完全重复调用率、MCP 工具调用膨胀率、必填参数完整率、
Schema 合法率、
参数值准确率核心、路由 Precision、路由 Recall、子任务冗余率和意图识别准确率已经实现。
时间范围提取准确率和陈述支持率已验收。工具超时率已实现。
当前批次 10 项指标（工具调用、Token 成本、时间三阶段）已全部验收；数值锚定率、
MCP 工具调用膨胀率宽表接入与轮询排除修订已于 2026-09-20 验收确认。
松耦合指标宽表汇总器与 CSV 脚本已实现；每行对应一个 Trace，每个指标只占一个值列，当前
注册 `tool_calls_count`、`per_tool_repeat_call_count`、`per_tool_call_tokens`、
`total_token_consumption`、`max_tool_token_share`、`output_token_ratio`、
`cache_input_token_ratio`、`per_tool_call_latency_ms`、`end_to_end_latency_ms`、
`max_tool_latency_share`、`mcp_tool_call_inflation_rate` 和
`answer_evidence_grounding_rate`，后续指标通过 `MetricColumn` 逐项接入。
固定身份列已扩展为
`user_id`、`session_id`、`log_id`、`mode`；统一 `log_id` 在聊天模式取 Trace ID，在研究模式取
用户消息 ID。全量结果固定覆盖写入 `data/processed/metric_table.csv`，不再创建版本号副本。

已完成：

- Python 3.11+ 项目和 pytest、Ruff、mypy 工具链配置。
- `BaseEvaluator`、`EvaluationResult`、`EvaluationStatus` 公共契约。
- 公共契约及现有指标行为测试，共 677 项（含数值锚定率 49 项）；最近一次验证全部通过。
- `collectors`、`evaluators`、`reporters`、`storage` 包入口。
- 已依据根目录《初步入库评测指标.md》建立当前批次 10 项逐指标开发顺序：工具调用、Token 成本、
  时间三个阶段依次推进。
- MCP 工具手册已复制到项目，当前文件名为 `mcp_tool_manual.json`。
- 新版 canvas 与研究调用的安全摘要比较规范化，不持久化参数、响应或摘要值；新版聊天按
  `traceId + toolId` 关联跨消息结果并排除孤立 Agent 返回，旧版聊天解析已删除。
- `exact_duplicate_tool_call_rate` 评测器，版本 `1.1.0`。
- 新版 canvas/研究调用的 MCP 膨胀安全聚合规范化；聊天模式遍历跨 Agent/message 的普通
  `tool_call`，按 `traceId + toolId` 去重并排除子 Agent 调用，旧版 `tool_call_queue` 已删除。
- `mcp_tool_call_inflation_rate` 评测器，版本 `1.1.0`；只统计手册内 MCP，同任务内相同能力
  与规范化入参的后续调用计为膨胀，响应正文不参与比较。
- 版本化 MCP 必填参数目录，以及新版 canvas/研究调用的安全聚合规范化；聊天模式按
  `traceId + toolId` 跨 Agent 和消息去重并排除子 Agent 调用，旧版聊天解析已删除，参数值和
  摘要不进入结果。
- `required_parameter_completeness_rate` 评测器，版本 `1.2.0`；Schema 覆盖仅保留诊断计数。
- MCP 参数类型、枚举和嵌套结构的递归校验；新版聊天按 `traceId + toolId` 跨 Agent 和消息
  去重并排除子 Agent 调用，旧版聊天解析已删除；额外字段只诊断，不重复检查顶层必填字段。
- `tool_argument_schema_validity_rate` 评测器，版本 `1.2.0`。
- 参数值语义目录、新版 canvas/研究 Judge 候选提取，以及不持久化 query 和参数值的内存
  边界；聊天模式按 `traceId + toolId` 跨 Agent 和消息去重并排除子 Agent 调用，旧版聊天
  解析已删除。
- 可注入、版本化 Judge 契约与 `parameter_value_accuracy_rate` 评测器，版本 `1.4.0`；
  实际参数和遗漏候选由程序分配稳定字段 ID，Judge 只返回三态 verdict，避免字段路径扩张、
  数组路径展开和自由原因码导致的非法输出；支持部分失败隔离，最终结果不输出辅助率指标。
- OpenAI-compatible Judge 共享运行时与常用 dotenv 配置加载：远程端点强制 HTTPS，优先
  使用严格 JSON Schema，供应商拒绝时回退 JSON 对象模式；默认 512 KiB 请求上限、2 次
  瞬态重试、输出截断检测及稳定脱敏错误分类已覆盖五类 Judge 指标。
- LLM 结合整体意图、全部可见子任务和 MCP Schema 生成子任务路由，程序派生标准能力并集；
  动态 Schema 声明安全 snake_case 标签与目录内能力 ID 约束，业务输出非法时携带安全原因码重试
  一次，重试后仅映射元数据非法时跳过持久化但保留合法能力计数。日志明确声明但手册缺失的
  MCP 自动计为匹配 TP，框架工具继续排除；两个指标的新版 canvas 适配版本均为 `1.3.0`。
- 去实体化的意图签名、子任务类型与能力集合 JSONL 映射表，支持去重和 Jaccard 相似检索；
  原始意图、实体、参数和响应不得持久化。
- 聊天子任务的确定性 NFKC、约束词元和字符 bigram 冗余规则；不调用 LLM，任务正文和
  中间指纹不进入结果；新版 canvas 跨 Agent/message 扫描取代旧聊天队列解析，
  `subtask_redundancy_rate` 版本为 `1.1.0`。
- 聊天与研究模式的意图证据规范化、固定分项 Judge 契约和 OpenAI-compatible 适配器；
  程序按 45% 意图覆盖、20% 规划、15% MCP 合理性、20% 最终回答加权，主意图错误或冲突
  硬归零；研究模式补取最终回答，声明型手册外 MCP 作为行为证据，业务输出非法时安全重试
  一次；`intent_recognition_accuracy` 版本为 `1.3.0`，原始正文和 Judge 响应不落盘。
- 聊天与研究模式的时间需求、工具参数和可复现时间锚点规范化；时间指标升级为 query-only
  需求提取与带角色证据匹配两阶段契约，程序分配稳定字段 ID，并隔离 retrieval window 与
  business data period。显式年份、最新性和预测属性提供确定性补漏，业务结构非法时安全重试
  一次；`time_range_extraction_accuracy` evaluator 与 prompt 版本均为 `2.0.0`，query、日期
  原文、参数值和 Judge 响应不落盘。
- 陈述支持率共享显式 MCP 证据工具目录、聊天/研究成功响应关联、来源原生结构拆分和内存
  证据去重；新版 canvas 按 `traceId + toolId` 跨 Agent/message 关联请求和结果，隔离子 Agent、
  孤立结果及冲突生命周期，旧版聊天解析已删除；已登记证据工具的普通字符串响应保留为
  响应级证据，非文本标量仍不估算，query、证据正文和摘要不落盘；解析契约版本进入稳定
  `evidence_scope_version`。
- 聊天最终回答和研究模式 `turn.end` 前最后一个 assistant 文本提取、空证据零分入口、严格
  原子陈述结果契约及本地 Embedding 增强评测器；新版 canvas 从
  `meta.query/answer.answer` 提取聊天输入，旧版顶层字段不再支持；`statement_support_rate`
  版本为 `4.0.0`。默认路径不调用生成式 LLM 或远程推理接口，使用本地开源 Embedding 召回
  Top-K 证据，再执行规则分句、指标别名、时间、数值、单位、实体、否定和方向冲突检查；
  灰区保留为 `not_assessable`，模型不可用时安全返回 `embedding_model_unavailable`。输出自动
  判定覆盖率和安全召回计数，query、回答、原子陈述、证据正文和向量均不落盘。
- 版本化工具 SLA 与别名目录，以及新版 canvas/研究调用的显式超时、SLA 超限和缺失时间
  分类；聊天模式按 `traceId + toolId` 跨 Agent/message 关联请求与结果，排除子 Agent、孤立
  结果和旧版 `tool_call_queue`。`tool_timeout_rate` 版本为 `1.1.0`；工具 SLA 缺失时不可计算，
  工具已配置 SLA 但调用缺少完整时间时，按确认口径计为超时并单独诊断。
- 双模式端到端时延边界规范化及 `end_to_end_latency_ms` 评估器，版本 `1.0.0`；聊天模式
  使用 canvas 请求/回答创建时间差，研究模式优先使用 `turn.end.durationMs` 并交叉校验时间戳。
- 双模式平均模型步骤时延规范化及 `average_model_step_latency_ms` 评估器，版本 `1.0.0`；聊天
  模式读取 Agent message 自身耗时，研究模式按 message ID 配对步骤边界并诊断事件时间戳差异。
- 双模式 Token 消耗规范化及 `total_token_consumption` 评估器，版本 `1.0.0`；聊天模式累加
  Agent 模型步骤 input/output，研究模式按 message ID 去重 step-finish total，不处理 cache 分项。
- 双模式逐工具调用资源字段：聊天读取 `tool_result.durationSeconds`，研究按 `callID` 合并工具
  生命周期并计算 `tool_call_duration_ms`；缺失或非法耗时按调用隔离。两种模式均明确返回
  `tool_token_consumption = NOT_COMPUTABLE / no_observable_tool_token_usage`，不分摊模型 Token。
- 双模式逐工具平均时延聚合及 `per_tool_call_latency_ms` 评估器，版本 `1.0.0`；部分缺失只进入
  覆盖计数且不补零，全缺失工具项不可计算，非法、负时序或冲突耗时只隔离受影响工具项；
  宽表项只保留工具名和毫秒值，逐工具状态、原因、覆盖计数与 evaluator 版本进入表外元数据。
- 双模式最大工具时延占比规范化及 `max_tool_latency_share` 评估器，版本 `1.0.0`；聊天排除
  `respond` 和结构化子 Agent 调用，完整覆盖后按业务工具累计耗时计算，支持并列最大工具。
- 集中式指标规格 `docs/specs/metrics.md`，记录历史已实现指标与当前批次已确认口径。
- 已按新版聊天 JSON 数组结构重算十六个指标的字段映射、确定性基线和条件可计算范围；
  明确区分普通工具请求、子 Agent 调用与结果侧 Agent 返回，未把 Judge 候选覆盖率当成语义得分。

## 当前状态

- 已暂时跳过指标：调用成功率、异常率、重试成功率、重试率、平均重试尝试次数和
  实体映射准确率；相关实现、测试和专用规格已删除。
- 已删除指标：证据查准率、证据查全率、证据时效合格率、证据可追溯率、无依据陈述率、数值忠实度、
  最终数值与召回证据一致率、硬编码率、循环引用率、需求覆盖率、严格任务完成率、信息保留率、
  信息坍缩率和中间结果与最终结果一致率、公式计算准确率、公式单元格覆盖率、首次工具决策
  时延、工具调用时延 P50、工具调用时延 P95、工具调用时延 P99、工具间
  空窗时延；这些指标不再保留在开发顺序和原始指标范围中。其中证据查全率依赖当前项目尚未
  具备的高标准 Golden Benchmark。
- 已取消指标：每个成功会话的平均成本、模型工具循环深度、上下文膨胀率；已从开发顺序和
  原始指标范围删除。模型工具循环深度的取消原因为聊天模式缺少模型步骤和轮次边界，无法
  可靠计算；上下文膨胀率只能反映上下文规模增长，不能直接证明冗余，并与 Token 消耗诊断
  存在较强重叠。
- 最近完成并验收的新版聊天适配指标：陈述支持率。
- 最近完成的路由修正：Judge 只返回子任务并由程序派生能力并集；日志声明的手册外 MCP
  自动计为匹配 TP；动态 Schema、一次安全业务重试和映射元数据降级已完成真实日志复验。
- 最近完成的时间范围修正：前 30 条聊天 Trace 中 11 条可计算、15 条无时间要求、2 条前置
  失败、2 条证据不足；18 个可判定字段为 `9/18 = 0.5`。研究日志本次为 `0/2 = 0`。
  工具默认日期不再制造分母，单一年份不再漏提取，兼容端点省略空诊断数组时不再误判非法。
- 最近完成：MCP 工具调用膨胀率（`mcp_tool_call_inflation_rate`）接入统一宽表并完成轮询排除
  修订（evaluator `1.2.0`）。指标列经 `build_metric_columns(catalog)` 工厂构建，闭包持有仓库根
  `mcp_tool_manual.json`（v0.2.0，89 能力）构建的能力目录，CLI 新增 `--mcp-manual` 覆盖入口，
  手册缺失或非法时快速失败。2026-09-18 真实日志逐 Trace 归因复核发现唯一系统性假膨胀：
  一条聊天 Trace 的 10 次 `stock_analyze_result` 同 taskId 长轮询（每次返回"处理中"）按旧口径
  贡献 9 次"膨胀"（22.5%）；修订后入参恰为 taskId 单键的 result 类轮询调用不进入分子分母、
  计入 `polling_call_count`（保守判定：键更多或更少均不排除），空入参调用计入
  `empty_argument_call_count`，仅剩轮询时返回 `no_non_polling_mcp_calls`。修订后重算：
  聊天 54/74 computed、零膨胀 46 条、均值 1.82%、最高 33.33%（轮询排除 10 次，f0732 归零）；
  研究 43/47 computed、零膨胀 37 条、均值 2.64%、最高 42.86%（研究主膨胀源为空结果原样
  重发与跨 Agent 扇出复读，均属真膨胀；失败重试混计实测 0）。
- 口径来源收敛（2026-09-20）：根目录《初步入库评测指标.md》为唯一权威指标规格来源；
  `docs/specs/metrics_initial.md` 退役为历史存档（其中 2026-09-15 零埋点降级口径设计与取消
  记录与权威口径冲突处一律以权威口径为准）。恢复存档中任一降级口径设计（技术完成率、
  业务失败信号探测、重试代理口径）前，按仓库规则重新立项确认。
- 已验收（2026-09-20 确认）：单工具最大时延比率（`max_tool_latency_share`）、数值锚定率
  （`answer_evidence_grounding_rate` v2）、MCP 工具调用膨胀率（`mcp_tool_call_inflation_rate`
  v1.2.0）三项验收通过；首批 10 项指标与回答质量、Agent 解析维度的宽表接入批次就此收尾。
- 最近完成：回答质量维度首个指标数值锚定率（`answer_evidence_grounding_rate`）v2 匹配引擎
  定稿（evaluator `1.1.0`，规则版本 `grounding_units_v2` / `grounding_norm_v2`）。按
  2026-09-18 假未命中复核修复：数字边界守卫只约束键端为数字的方向（放行"76%2026年"式
  无缝拼接与 "vs.Rmb" 缩写句点）、证据侧顿号列举展开、个百分点↔% 互认、货币前缀与英文
  数量级渲染、双向量级舍入复述（含向下因子，覆盖证据完整元表格），命中数以
  `rounded_match_count` 诊断单独暴露。跨货币类永不换算与"不生成裸英文数量级键"的约束
  保持不变。研究模式回合切分入口修正为先按 `sessionID` 分组再切分（并行 session 回合
  时间重叠，全局切分会串回合）；干净切分下新基线：聊天 1,999/2,318 = 86.24%、研究
  1,674/1,810 = 92.49%。
- 已验证不可行并记录：纯代码"推导 vs 编造"三分类。对未锚定 span 做两操作数算术重构
  （差/和/均值/增长率/占比/倍数），半容差与精确容差下置换检验背景命中率均≈真实命中率
  （聊天 87% vs 背景 82-84%，研究 87.5% vs 背景 87-88%）——金融数值池过密，算术分解
  无区分力；且真推导普遍带二次取整（"约77亿" ← 722.01−645.08=76.93），固定容差接不住。
  推导正确性归属未来的数值一致性/Judge 方向，锚定率分母保持 extracted 不变。
- 进行中的具体指标：无（上述三项验收确认后批次收尾；数值锚定率宽表接入代码随本批提交）。
- 下一项指标：引用可溯率（《初步入库评测指标.md》回答质量维度候选顺序第一位）。实勘依据：
  研究模式检索输出自带 `id: [n] 标题:` 编号来源清单且回答 `[n]` 引用全部落在范围内，可
  确定性锚定；聊天模式回答 `[n]` 引用与召回 `doc_id/chunk_index` 对应关系成立，但多轮
  检索 Trace 编号为模型侧累积重排，需先确认降级口径。备选：4.2 `mcp_selection_accuracy`、
  4.3 `mcp_parameter_accuracy`（口径已评审、无埋点依赖）。
- 下一动作：产出引用可溯率的公式、双模式字段映射、异常边界与模拟计算，等待口径确认后
  再进编码。

## 已知缺口

- 新版聊天 JSON 数组与研究模式事件流的结构已形成架构文档；完全重复
  调用率、MCP 工具调用膨胀率、必填参数完整率、Schema 合法率、参数值准确率、路由
  Precision/Recall、子任务冗余率、意图识别准确率、时间范围提取准确率和陈述
  支持率 collectors 已支持新版 canvas，其余指标 collectors 仍主要按旧版字段读取，完整统一
  Trace 模型尚未实现。
- collectors、聚合器、报告器、配置加载器和可运行 CLI 尚未实现。
- Golden Benchmark 校准集和交付物读取能力尚未接入。
- 陈述支持率 Embedding 阈值尚无人工 Golden 样本校准；当前用确定性同义改写与冲突对照测试
  约束行为。生产部署还需固定模型 commit，并在运行节点预装模型缓存。
- 路由标准目前由 LLM + MCP Schema 生成，尚未经过人工校准集冻结；因此属于可追溯的
  Judge 代理标准，而非人工 Golden Benchmark。
- 严格 JSON Schema 只能约束输出形状；各指标仍需由 evaluator 二次校验字段间业务关系。
  意图识别与路由指标已实现携带安全校验反馈的一次业务修复重试，其他 Judge 指标回退到
  `json_object` 时仍可能直接产生 `invalid_judge_output`。
- 工具结果仅部分提供执行耗时；不同层的 Token 快照仍不能跨层直接求和，当前指标已固定聊天
  Agent message 与研究 step-finish 两个权威来源。
- 当前没有经服务方确认的生产工具 SLA 目录，因此聊天与研究真实日志的超时率暂时返回
  `missing_tool_sla`，不得使用示例阈值生成生产指标。

## 验证基线

```text
数值锚定率（answer_evidence_grounding_rate v2 引擎定稿, evaluator 1.1.0）聊天全量
  （2026-09-18 重算, 74 条新样本）: 47 条 computed、27 条 no_extractable_spans；
  2,318 span 中 1,999 命中，微平均 86.24%，Trace 中位 94.05%，p10 66.67%；
  舍入复述命中 350、孤立结果 176、失败信号载荷 2、无中生有信号 0
数值锚定率 v2 研究全量（47 回合, session 内切分）: 34 回合 computed、13 回合
  no_extractable_spans；1,810 span 中 1,674 命中，微平均 92.49%，回合中位 98.81%，
  p10 72.73%；舍入复述命中 38、证据 chunk（chunkId 去重）7,966、排除工具输出 731
数值锚定率 v2 研究通道分布: final_text 32 / write_artifact 14 / edit_artifact 1；
  write 通道微平均 98.21%，final_text 通道 86.49%
数值锚定率 v1 历史基线（2026-09-17，全局时间切分，供修复量级对照）: 聊天
  1,603/2,318 = 69.15%（中位 85.27%、p10 0.0）；研究 1,566/1,818 = 86.14%
  （中位 95.80%、p10 69.70%）；干净切分下 v1 重放 1,555/1,810 = 85.91%
数值锚定率测试: 66 项通过（collector 54 + evaluator 12）；pytest 全量 694 项通过
  （需 --basetemp 指定独立临时目录，系统 pytest-of-EDY 目录存在访问锁）；ruff check
  通过；mypy 通过；本批 4 个文件 ruff format --check 通过
MCP 工具调用膨胀率宽表接入与轮询排除修订（2026-09-18, evaluator 1.2.0, 手册 v0.2.0/89 能力）:
  pytest 全量 703 项通过、ruff check/format --check 与 mypy 通过；CLI 新增 --mcp-manual。
  归因复核: 15 条非零膨胀 Trace 的 47 个膨胀调用逐一核对原始入参与返回哈希——47 组重复
  入参全部逐字节相同（规范化无误合）、首次调用全部成功（失败重试混计 0）；唯一系统性
  假膨胀为 f0732 的 10 次 stock_analyze_result 同 taskId 长轮询（每次 ~25.8s 返回"处理中"）。
  修订后真实日志重算（74 条聊天 + 47 条研究回合）: 聊天 54/74 computed、20 条
  no_registered_mcp_calls、零膨胀 46 条、均值 1.82%、最高 33.33%、轮询排除 10 次
  （f0732 由 22.5% 归零）；研究 43/47 computed、4 条 no_registered_mcp_calls、零膨胀
  37 条、均值 2.64%、最高 42.86%。真膨胀主导: 跨 Agent 扇出复读（聊天 11 次）与研究
  空结果原样重发（14 次，返回均为空列表）。
  宽表重算 121 行（聊天 74 + 研究 47）、12 个指标列（含数值锚定率），膨胀率列空单元格仅
  对应 no_registered_mcp_calls 诊断。
工具调用总次数聊天全量重算: 336/336 条 Trace computed，合计 3541 次；中位数 6、平均
  10.53869、P90 25、P95 33、P99 60、最大 134；42 条为 0。结构化排除 1199 次子 Agent
  委派和 725 次 respond 返回，无 malformed 或重复调用 ID 观察
工具调用总次数研究全量重算: 380 事件合并为 30 次唯一调用，8 个工具名，computed；
  60 次生命周期重复观察被合并，无 malformed
指标宽表全量验证: 聊天 336 行加研究 1 行，共 337 行；当前字段为
  user_id、session_id、log_id、mode、tool_calls_count、per_tool_repeat_call_count，两个指标的
  空单元格均为 0，运行问题 0；聊天 336 条
  完整日志的 Trace ID 非空且唯一 336/336；研究 380 个事件组成 1 条完整日志，用户消息 ID
  观察 60 次、去重后 1 个，与日志数一致
单工具调用重复次数聊天全量重算: 336 条均 computed，211 条存在重复调用；共输出 578 个
  重复工具项，首次调用之后的额外调用合计 2313 次
单工具调用重复次数研究全量重算: 1 条日志 computed 且存在重复调用；共输出 2 个重复工具项，
  首次调用之后的额外调用合计 22 次
单工具 Token 消耗聊天全量重算: 294/336 条日志输出结果，42 条因无业务工具调用返回
  no_tool_calls；共输出 1228 个工具值，局部 Token 缺失项 0，范围 1745 至 213861.6，
  中位数 43322.5，平均 44138.00965
单工具 Token 消耗研究全量重算: 1/1 条日志输出结果，共 8 个工具值，局部 Token 缺失项 0，
  范围 597544 至 677477.3636363636，中位数 614672.25，平均 620797.73295
端到端总 Token 全量验证: 聊天 312/336 条可计算，24 条 no_observable_token_usage；合计
  253661221，中位数 534269，平均 813016.73397，范围 9059 至 6756848。研究 1/1 条可计算，
  总量 18064255；固定 `metric_table.csv` 已覆盖写入第四个指标列
单工具最大 Token 消耗比率聊天全量验证: 294/336 条可计算，42 条 no_tool_calls；范围
  0.0516185515 至 0.8159945666，中位数 0.2791390876，平均 0.31408132，合法零值 0 条
单工具最大 Token 消耗比率研究全量验证: 1/1 条可计算，结果 0.8250825733；无错误或缺失
输出 Token 比率聊天全量验证: 312/336 条可计算，24 条 no_observable_token_usage；范围
  0.00058775990008081693 至 0.11505385684503128，中位数 0.0060333224349133577，平均
  0.012974958009279032
输出 Token 比率研究全量验证: 1/1 条可计算，41695/18064255 = 0.0023081494365530158；固定
  `metric_table.csv` 已覆盖写入第六个指标列
缓存 Token 比率聊天全量验证: 312/336 条可计算，24 条 no_observable_cache_usage；范围 0 至
  0.999285997619992，中位数 0.5977799802035351，平均 0.5650148398166177，合法零值 7 条
缓存 Token 比率研究全量验证: 1/1 条可计算，18022506/18022560 = 0.9999970037552933；固定
  `metric_table.csv` 已覆盖写入第七个指标列
单工具调用时延聊天全量验证: 296/336 条 Trace 输出逐工具结果，40 条 no_tool_calls；共
  1469 个工具项，其中 1175 个可计算、294 个 no_observable_tool_duration，无错误项；有效
  工具平均时延范围 0 至 116686 ms，中位数 1270 ms，平均 2915.66383 ms；823 次调用缺少
  完整耗时且均未补零
单工具调用时延研究全量验证: 1/1 条日志输出 8 个可计算工具项，无缺失或错误；平均时延范围
  133 至 11536.5 ms，中位数 1149.5 ms，平均 3479.52273 ms；固定 `metric_table.csv` 已覆盖
  写入第八个指标列
新版聊天日志规格重算（336 条 Trace，仅结构与确定性指标；未运行真实 Judge）：
完全重复调用率: 26/4266 ≈ 0.00609；3389 次响应可比较，877 次缺少可比较响应正文
必填参数完整率: 3382/4015 ≈ 0.84234；2911/4266 次调用被手册覆盖
Schema 合法率: 2880/2911 ≈ 0.98935；31 次非法，含 27 次类型违规和 4 次枚举违规
参数值准确率候选: 2911 次手册覆盖调用，覆盖 271 条 Trace；未运行 Judge，无最终得分
路由 Precision/Recall 候选: 312/336 条 Trace 可提交 Judge，24 条 pre_routing_failure；
  候选内合计 1507 个去重意图片段、950 个实际 MCP 能力；全部规范化对象共提取 1531 个
  意图片段，其中 24 个属于前置失败且不会送 Judge；未运行 Judge，无最终得分
子任务冗余率: 253 条 Trace 可计算，81 条 no_subtasks，2 条 invalid_subtask_event；严格排除
  不可计算 Trace 后为 3/1194 ≈ 0.00251，含 2 次精确重复和 1 次高相似冗余
意图识别准确率候选: 312/336 条 Trace 可提交 Judge，24 条 pre_intent_failure；含 1505 个
  去重行为证据和 950 个实际 MCP 能力，另有 2 个无文本子 Agent 诊断；无最终得分
时间范围提取准确率候选: 312/336 条 Trace 可提交 Judge，24 条前置失败；336 条均使用
  canvas_meta 时间锚点，候选含 4266 次唯一调用，其中 2911 次被手册覆盖；无最终得分
陈述支持率 3.0.0 历史基线: 临时 17 工具目录下，聊天前 30 条微平均约 0.07646、自动判定
  覆盖率约 0.93606；研究日志约 0.09353、覆盖率约 0.96403；4.0.0 Embedding 版待本地模型
  安装后复跑，生产证据工具目录仍待管理员确认
MCP 工具调用膨胀率: 33/2911 ≈ 0.01134；2878 个不同请求签名，1355 次手册外调用被排除
超时率: 3443/4266 次调用有工具执行耗时，823 次缺失；缺生产 SLA，返回 missing_tool_sla
端到端时延聊天模式: 336/336 条可计算；最小 0 ms、中位数 84500 ms、平均
  99339.286 ms、最大 1181000 ms；秒级边界推导，0 ms 表示起止落在同一秒；固定
  `metric_table.csv` 已覆盖写入第九个指标列
端到端时延研究模式: 936405 ms，优先 turn.end.durationMs；时间戳差值 936403 ms，
  boundary_difference_ms = -2
单工具最大时延比率聊天全量验证: 261/336 条可计算，42 条 no_tool_calls，33 条
  incomplete_tool_duration_coverage；可计算结果范围 0.2296446795 至 1，中位数
  0.6539524599，平均 0.6920219090，无错误结果
单工具最大时延比率研究全量验证: 1/1 条可计算，最大耗时工具为 excel，
  240541/268980 = 0.8942709495；固定 `metric_table.csv` 已覆盖写入第十个指标列
平均模型步骤时延聊天模式: 336/336 条可计算；4946 个有效步骤、412 个缺失步骤，全量微平均
  6771.766 ms；单 Trace 平均值中位数 6307.155 ms
平均模型步骤时延研究模式: 27/27 个步骤可计算，总步骤耗时 746917 ms，平均
  27663.593 ms；27 个步骤完成 ts 交叉校验，最大绝对边界差异 439 ms
Token 消耗聊天模式: 312/336 条可计算，24 条 no_observable_token_usage；可计算 Trace 合计
  253661221 tokens，中位数 534269，平均 813016.734，范围 9059 至 6756848
Token 消耗研究模式: 27/27 个 step-finish Token 完整，合计 18064255 tokens
逐工具资源字段测试: 9 项通过；聊天与研究模式分别验证逐调用耗时，工具 Token 均明确返回
  no_observable_tool_token_usage；尚未对未纳入仓库的真实日志执行新增字段重算

以下为既有代码、旧版聊天日志与研究日志验证记录：
pytest: 528 passed
ruff check: passed
本批修改文件 ruff format --check: passed；仓库全局仍有 3 个既有文件未格式化，本批未触碰
mypy src tests: passed（110 个源文件）
Judge 共享运行时真实脱敏冒烟: 已配置供应商直接接受 `json_schema`，返回顶层 `fields`，
  prompt 版本 `1.1.0`；未触发兼容回退，未输出端点、密钥、请求正文或响应正文
现有研究日志结构验算: 30/30 = 1.0, computed
完全重复调用率聊天日志验算: 35/907 ≈ 0.03859, computed
完全重复调用率研究日志验算: 1/30 ≈ 0.03333, computed
MCP 工具调用膨胀率测试: 28 项通过；新版聊天按 `canvas.meta.traceId` 与跨 Agent/message 的
  `content.tool_call[]` 取数，排除子 Agent 和旧版 `tool_call_queue`，只比较手册内 MCP 能力与
  规范化入参，不读取响应
MCP 工具调用膨胀率新版聊天日志既有结构验算: 271/336 个 Trace 含已登记 MCP；2911 个 MCP
  调用中 33 个膨胀，微平均 33/2911 ≈ 0.01134；2878 个不同请求签名，1355 个手册外调用被排除
MCP 工具调用膨胀率研究日志验算: 5 个 MCP 调用、5 个不同请求签名，0/5 = 0.0, computed；
  25 个手册外调用被排除，生命周期和重复定义按 call ID 合并
必填参数完整率聊天日志验算: 696/832 ≈ 0.83654
必填参数完整率研究日志验算: 10/11 ≈ 0.90909
Schema 合法率聊天日志验算: 642/643 ≈ 0.99844
Schema 合法率研究日志验算: 5/5 = 1.0
参数值准确率聊天日志候选: 643/907，106 个 Trace 均成功规范化
参数值准确率研究日志候选: 5/30，30 个重复观测已按 call ID 去重
参数值准确率真实单调用冒烟: 1 个正确字段、1 个不可判定字段，准确率 1.0，Judge 失败 0
参数值准确率字段 ID 契约复验: 前 30 条新版聊天 Trace 共 359 个 Judge 候选全部成功，
  0 个非法输出；可判定字段 1,032 个，微平均 `868/1032 ≈ 0.84109`。研究日志 5 个 Judge
  候选全部成功，可判定字段 19 个，结果 `12/19 ≈ 0.63158`；Judge 模型为 `qwen-flash`，
  prompt 版本 `1.2.0`。
路由规范化 MCP 目录: 77 个能力，手册版本 0.1.0
路由指定批次预检: 聊天前 30 条加研究日志共 31 条，29 条可提交 Judge、2 条
  pre_routing_failure；识别 13 个唯一手册外 MCP 自动匹配路由、40 次相关调用，另有 119 次
  无法证明为 MCP 的框架或环境工具调用
路由 collector 与双指标定向测试: 34 项通过；Precision 与 Recall 版本均为 `1.3.0`，Judge
  prompt 版本为 `1.2.0`
路由双指标真实指定批次: Precision 26 条可计算，TP=50、FP=41，微平均约 0.54945；Recall
  27 条可计算，TP=50、FN=74，微平均约 0.40323；11 条触发一次业务校验重试，4 条映射标签
  仍非法但不阻断能力计分且不持久化，2 条因目录外且日志未声明的能力 ID 不可计算；研究
  日志 Precision=0.5、Recall=0.75，Judge 模型为 `qwen-flash`
安全意图路由映射表: 14 条去重记录，敏感字段与中文实体扫描命中 0
子任务冗余率测试: 23 项通过；evaluator 版本 `1.1.0`
子任务冗余率聊天日志验算: 336 条中 253 条可计算，严格分母为 1194 个有效子任务；规则命中
  3 个冗余，微平均约 0.00251；81 条无子任务，2 条因无文本多模态子任务不可计算
子任务冗余率研究日志: 当前缺少确认的结构化子任务契约，返回 missing_structured_subtasks
意图识别准确率定向测试: 41 项通过；聊天与研究模式均为 CONDITIONALLY_COMPUTABLE；
  evaluator 版本 `1.3.0`，Judge prompt `1.2.0`
意图识别准确率聊天模式: 缺少 query 返回 missing_query；无行为证据返回 no_intent_evidence；
  固定前置服务失败返回 pre_intent_failure
意图识别准确率研究模式: 提取 `turn.end` 前最后 assistant 文本；缺少 query 返回
  missing_query，缺少调用 ID 返回 missing_call_id，同 ID 不同工具返回
  conflicting_call_definition
意图识别准确率评分: 固定八字段 Schema；程序加权并处理 `not_present` 重归一化；主意图错误
  或意图冲突硬归零；首次业务输出非法时携带安全原因码重试一次
意图识别准确率指定批次实跑: 前 30 条聊天中 28 条可计算且均为 1.0，2 条
  pre_intent_failure；研究日志 1 条可计算且为 1.0；业务校验重试与 invalid_judge_output 均为 0
时间范围提取准确率定向测试: 35 项通过；聊天与研究模式均为 CONDITIONALLY_COMPUTABLE；
  evaluator 版本 `1.2.0`
时间范围提取准确率聊天模式: 支持显式 reference_time 和 `canvas.meta.create_time`；正常执行
  但未传递时间字段可由 Judge 计算为 0，固定前置服务失败返回 pre_time_extraction_failure
时间范围提取准确率研究模式: 支持事件 timestamp/create_time 或 part.state.time.start 锚点；
  缺少调用 ID、冲突定义和参数解析不完整分别显式返回安全原因码
时间范围提取准确率模拟计算: 正确字段 6、错误字段 1，结果 6/7 ≈ 0.85714, computed
时间范围提取准确率真实前 10 条聊天日志: 2 条可计算、1 条无时间需求、7 条前置模型服务
  失败；可计算日志正确字段 5、错误字段 2，微平均 5/7 ≈ 0.71429；Judge 失败 0
陈述支持率定向与共享回归测试: 58 项通过；聊天与研究模式均为 CONDITIONALLY_COMPUTABLE；
  evaluator 版本 `3.0.0`，确定性规则版本 `rules-1.0.0`
陈述支持率新版聊天结构验算: 使用临时 17 工具目录时，336 条中 192 条形成 Judge 候选，
  历史上共观察 18257 个稳定证据单元；普通字符串响应现已改为响应级证据，不再因缺少内部
  来源边界直接判为 incomplete_evidence_segmentation。没有证据或明确空结果仍可进入 Judge
  并得到 0 分；旧版顶层 query/answer/tool_call_queue 不再支持
陈述支持率研究模式: 通过 assistant message ID 选择 turn.end 前最后一个非空文本；成功产物
  无最终正文返回 missing_final_artifact_content，缺少证据终态和非文本标量响应不估算；普通
  字符串工具输出现作为响应级证据进入确定性横向比对
陈述支持率模拟计算: 2 个可验证原子陈述中 1 个 supported、1 个 unsupported，另有 1 个
  excluded 建议，结果 1/2 = 0.5, computed；聊天与研究 evaluator 路径均通过
陈述支持率真实前 30 条新版聊天日志: 使用临时 17 工具目录与无 LLM 规则版复跑，27 条
  computed，2 条 pre_answer_failure，1 条 missing_final_answer；3,832 条可验证陈述中
  293 条 supported、3,294 条 unsupported、245 条 not_assessable，微平均支持率约
  0.07646，自动判定覆盖率约 0.93606
陈述支持率研究日志: 1 条 computed；9 个证据单元，139 条可验证陈述中 13 条 supported、
  121 条 unsupported、5 条 not_assessable，支持率约 0.09353，自动判定覆盖率约 0.96403；
  聊天与研究共 31 条规则评测本地耗时约 20.4 秒，无网络或 Judge 依赖。因无法建立人工
  Golden 样本，该结果作为可复现运行基线，不声明规则语义准确率已获人工验收
超时率测试: 22 项通过；聊天与研究模式均为 CONDITIONALLY_COMPUTABLE；新版聊天按
  `canvas.meta.traceId` 与跨 Agent/message 的 `content.tool_call[]/tool_result[]` 关联唯一调用，
  排除子 Agent、孤立结果和旧版 `tool_call_queue`
超时率规则: 显式 timeout/timed_out、duration > 工具 SLA、SLA 已配置但缺少完整时间均计入分子；
  failed/error/cancelled 不自动等同超时，duration == SLA 不超时
超时率全量日志验算: 新版聊天文件 336 条 Trace 中，296 条含 4266 次唯一普通工具调用并返回
  missing_tool_sla，40 条返回 no_tool_calls；3443 次调用具有有效 durationSeconds，823 次缺少
  完整耗时，显式 timeout/timed_out 为 0，另有 411 个孤立结果 ID 被排除。研究模式单条日志含
  380 个事件、30 次唯一工具调用，30 次均有完整 start/end，显式 timeout/timed_out 为 0，因未
  配置生产 SLA 返回 missing_tool_sla。未使用示例阈值估算或输出伪 0%
完全重复调用率新版聊天实现验算: 4266 次唯一普通工具请求中 3389 次响应可比较，
  26/4266 ≈ 0.00609；877 次缺少可比较响应正文，411 条孤立 Agent 返回被排除，
  296 条 Trace computed，40 条 no_tool_calls，重复 ID、冲突和 malformed 均为 0
```
