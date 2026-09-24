# Project Status

更新时间：2026-09-24

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
注册 `tool_calls_count`、`per_tool_call_tokens`、`total_token_consumption`、
`max_tool_token_share`、`output_token_ratio`、`cache_input_token_ratio`、
`per_tool_call_latency_ms`、`end_to_end_latency_ms`、`max_tool_latency_share`、
`answer_evidence_grounding_rate`、`turn_completion_rate`
（2026-09-21 新增）、`continuation_restart_count`（2026-09-21 新增）、
`llm_error_count`（2026-09-21 新增）、`tool_error_rate`（2026-09-21 新增）、
`error_recovery_rate`（2026-09-21 新增）、`compaction_event_count`
（2026-09-21 新增）和 `api_retry_rate`（2026-09-21 新增注册，列于
`compaction_event_count` 之后），
后续指标通过 `MetricColumn` 逐项接入。高危回合画像（`high_risk_turn_profile`）
已实现，按权威口径为报告级组合规则、不注册宽表列。2026-09-20 应需求确认：`per_tool_repeat_call_count` 与
`mcp_tool_call_inflation_rate` 两列从宽表剔除——评测器、collector 与测试保留，仅退出
CSV 注册；`--mcp-manual` CLI 入口与导入期 `mcp_tool_manual.json` 手册加载随膨胀率列
一并移除，宽表入口不再依赖 MCP 手册。2026-09-21 应需求确认：`citation_traceability_rate`
列从宽表剔除——评测器、collector 与测试保留，仅退出 CSV 注册；固定表已按新列集
（17 指标列，含 `api_retry_rate`、不含引用可溯率）全量重跑，此前"重跑暂缓"的
旧列集表状态就此解除。
固定身份列已扩展为
`user_id`、`session_id`、`log_id`、`mode`；统一 `log_id` 在聊天模式取 Trace ID，在研究模式取
用户消息 ID。全量结果固定覆盖写入 `data/processed/metric_table_full.csv`，不再创建版本号副本。
（本文件下方"最近完成"与"验证基线"中的 `metric_table.csv` 与"121 行"为当时批次的历史记录，
其中 2026-09-22 及更早的重跑对象是已删除的旧表；现行唯一固定表为
`metric_table_full.csv`（7,582 行；4 身份列 + 注册表指标列，当前 23 列），
由 `stream_compute_metrics` 产出。）

已完成：

- Python 3.11+ 项目和 pytest、Ruff、mypy 工具链配置。
- `BaseEvaluator`、`EvaluationResult`、`EvaluationStatus` 公共契约。
- 公共契约及现有指标行为测试，共 997 项（含数值锚定率 49 项）；最近一次验证全部通过。
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
- 最近完成：引用可溯率（`citation_traceability_rate`，evaluator `1.0.0`）实现并曾接入统一
  宽表；2026-09-21 应需求退出宽表注册（评测器、collector 与测试保留，仅退出 CSV 注册）。
  最终回答 `[n]` 引用编号在中间检索输出暴露的来源编号注册表内可解析的比例，
  纯编号解析无 Judge；弱判定边界（编号存在 ≠ 内容真用了该来源）随 `cite_v1`/
  `source_registry_v1` 版本声明。注册表：聊天 = 成功检索轮 doc_id 位置序列（复用锚定率
  成功判定口径），研究 = 本回合 completed 工具输出编号清单并集（write/edit 交付产物排除）。
  2026-09-17 样本基线：聊天 46/74 computed（28 无引用）、501 编号 338 可解析、微平均
  68.08%、满分 15 条、混排疑云标记 9 条；研究 15/47 computed（32 无引用）、175 编号
  36 可解析、微平均 38.67%、满分 5 条、139 超界引用集中 10 回合（skill 计数器疑为
  session 级累积，暴露引用协议缺口）。
- 2026-09-20 宽表列集调整：`per_tool_repeat_call_count` 与 `mcp_tool_call_inflation_rate`
  应需求从统一宽表剔除（评测器、collector 与各自测试保留，仅退出 CSV 注册）；宽表入口的
  `--mcp-manual` CLI 参数与导入期 MCP 手册加载随膨胀率列移除，`stream_compute_metrics`
  表头随 `METRIC_COLUMNS` 自动同步。真实样本重算 121 行 × 11 指标列，与剔除前宽表
  14 个共有列（4 身份 + 10 指标）按 `log_id` 对齐后逐单元格对比零差异，保留指标数值
  不受剔除影响。
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
- 进行中的具体指标：引用可溯率（`citation_traceability_rate` v1.0.0，2026-09-21 应需求
  退出宽表注册、评测器与测试保留）与回合完成率
  （`turn_completion_rate` v1.0.0，稳定性维度 7.1 首项）均已实现、测试与真实日志基线
  完成，回合完成率接入统一宽表，等待验收确认。
- 下一项指标：7.7 工具错误率与 7.5 错误自恢复率均已实现待验收；验收后按第 7 章
  稳定性维度候选顺序推进（7.8 框架 API 重试率、7.9 空结果率为后续候选，均需
  逐项立项确认）；回答质量维度候选（回答内部重复率）与工具维度
  备选（4.2 `mcp_selection_accuracy`、4.3 `mcp_parameter_accuracy`）仍有效。
- 2026-09-21 稳定性维度候选提案：《初步入库评测指标.md》新增"# 工作流稳定性维度"
  (7.1-7.13，除 7.1 已于同日确认立项并实现外，其余候选待确认、未编码)。基于 2026-09-17 双模式样本实勘：回合完成率
  (`turn_completion_rate`，单 Trace 输出 0/1 二值标记口径已经需求方确认，报告级聚合成率；
  聊天代理 73/74、研究 43/47)、静默中断信号、续跑重启次数(3 回合 4 次重启、2 次恢复)、
  模型错误数与五类分类目录(llm.error 9 条/6 回合)、错误自恢复率(5.1 研究侧降级实现，
  5/6 恢复)、上下文压缩事件数(49 次/约半数回合)、工具错误率与四类归因目录(研究
  status='error' 现有字段可算；聊天载荷代理口径，含 error_code 空串=成功的硬边界)、
  框架 API 重试率(tool.debug requestId 尾序号，210/573=36.6%，覆盖率 31.9% 须随值输出)、
  空结果率、长尾回合标记(聊天 round 代理需重新立项确认，区别于已取消的循环深度口径)、
  高危回合画像与遥测完整率(报告级/看板项，不进宽表)、横切分析维度(answer.model 模型
  路由切片、定时/交互任务切片、版本回归)。同步修正配套埋点需求汇总的陈旧编号引用
  (4.1→1.3、7.1→5.1、7.2→5.2)并登记研究侧 status='error' 与 requestId 重试代理新实勘。
  第 7 章基线为实勘脚本产出、非版本化 evaluator 输出，正式实现后须重算登记。
- 2026-09-21 高危回合画像（7.11）阈值专项确认（权威口径已修订，仍未编码）：四条件
  AND 公式废弃——实测 47 回合 0 命中（条件边际 23/7/13/5，交集为空）；改为双条件
  step 数 > 30 ∧ api_retry_rate > 0.4，命中 4/47（8.5%）：死亡回合 ecfa41e4
  （120 步/0.45）、40bab013（57 步/0.42）+ 闭环高摩擦回合 060a7676（60 步/0.52，
  全跨度 65.5 分钟/≥35.5M tokens）、f3640858（31 步/0.59），命中回合未闭环率 50%
  为全样本 5.9 倍；压缩次数与空结果数降级为命中特征清单诊断项。同步修正旧实勘
  "ecfa41e4 命中全部特征"错误声称（冻结规则复核：压缩 0、重试 90/200=0.45、
  空结果 0/198；旧分母 170 不可复现，apiCalls 全样本 573/210/36.6% 与 7.8 基线
  对账一致）；新发现续跑回合 turn.end.durationMs 只覆盖最后续跑段（060a7676:
  1819s vs 全跨度 3930s），画像时延解读须双视图。实勘脚本
  `.tmp/inspect_high_risk_profile.py` / `inspect_high_risk_detail.py` /
  `inspect_apicalls_dedup.py` / `inspect_high_risk_sensitivity.py`；实现依赖
  7.8（api_retry_rate）立项。
- 最近完成：回合完成率（`turn_completion_rate`，evaluator `1.0.0`，模板目录
  `failure_template_catalog_v1`）实现、测试与双模式真实日志基线完成，接入统一宽表
  第 12 指标列；聊天为显式声明的 answer 存在性代理口径，研究为 turn.end 事件口径，
  `completion_semantics` 随结果输出，两语义禁止混比。
- 最近完成：续跑重启次数（`continuation_restart_count`，evaluator `1.1.0`，稳定性维度
  7.3）实现、测试与双模式真实日志基线完成，接入统一宽表第 13 指标列；去重键 ts 方案、
  宽表注册与双因素 JSON 输出经需求方 2026-09-21 确认。聊天恒 `NOT_COMPUTABLE /
  no_continuation_mechanism`（模式语义事实，不解析 canvas）；研究消费入口层已合并的
  单逻辑回合序列，单元格为单元素 JSON 数组（2026-09-21 需求方确认统一 `[]` 包围，
  1.0.0→1.1.0 升版，内容键序不变）`[{"continuation_restart_count":N,
  "turn_completed":B}]`——重启计数为去重 `turn.start` 数 − 1（零重启是合法值），闭环
  因素复用 7.1 研究口径且未闭环回合同样输出；ts 缺失/非法保守计为独立事件并进诊断，
  跨 ID `turn.start` 与闭环边界冲突（如 turn.end 耗时冲突）按切分违约整条隔离；
  `restart_semantics` / `completion_semantics` 随结果输出。报告级 restart_turn_ratio /
  restart_recovery_rate 由单元格两因素直接派生，评测器不做跨 Trace 聚合。
- 最近完成：模型层错误数与错误分类（`llm_error_count`，evaluator `1.2.0`，分类规则
  版本 `llm_error_class_v1`，稳定性维度 7.4）实现、测试与双模式真实日志基线完成，
  接入统一宽表第 14 指标列；单元格形态（整数主值 + `error_classes` 分类数组 +
  `error_models` 出错模型路由数组并入 JSON、注册一列）经需求方
  2026-09-21 多次确认（1.0.0 主值+分类；1.1.0 增补出错模型；1.2.0 统一 `[]`
  包围，内容键序不变）。聊天恒
  `NOT_COMPUTABLE / no_model_error_events`（canvas 无模型层错误事件通道，通道不存在
  不等于无错误，禁止记 0）；研究按 `llm.error` 原始事件数计（含重复投影，与 7.5
  基线口径一致），分类匹配只取 name+message 文本（stack/url 显式排除，不参与匹配
  也不落盘），`message.updated.info.error` 与 `finishReason=error` 跨通道投影只进
  诊断不计数。出错模型归因：`llm.error` 不携带模型标识，仅当回合内去重后恰有一条
  (modelID, providerID) 路由时全量归因（顶层与 `info.model` 字典双形态兼容，样本
  47/47 回合均单路由）；多路由/无路由不归因、进 `model_unattributed_error_count`
  诊断，不猜测。守恒校验（分类之和 = 主值；已归因 + 未归因 = 主值）与目录白名单
  校验违约即隔离。
- 最近完成：工具错误率与错误归因（`tool_error_rate`，evaluator `1.1.0`，归因目录
  `tool_error_attribution_v1`，稳定性维度 7.7 首项派单指标）实现、测试与双模式
  真实日志基线完成，接入统一宽表第 15 指标列；宽表单元格形态（比率 + 分子分母 +
  四类归因计数的单元素 JSON 数组，2026-09-21 需求方确认统一 `[]` 包围、
  1.0.0→1.1.0 升版）、降级严格字面口径、"Tool not found" 窄正则补入
  model_misuse、报告级归因 × 工具矩阵留 reporter 批次，均经需求方 2026-09-21 确认。
  聊天为载荷代理口径（`payload_proxy_v1`：嵌套 tool_result 信封解包、JSON 字符串
  解码、入口级 success/error 兜底、code:1 降级分流、空串 error_code=成功硬边界），
  研究为显式终态口径（`explicit_status_v1`：error/completed/pending），
  `terminal_semantics` 随结果输出，两语义禁止混比；归因未命中记 unclassified
  只诊断不猜测，错误正文与载荷不落盘。
- 最近完成：错误自恢复率（`error_recovery_rate`，evaluator `1.1.0`，
  `recovery_semantics = degraded_v1`，稳定性维度 7.5，5.1 的研究侧降级实现）
  实现、测试与双模式真实日志基线完成，接入统一宽表第 16 指标列；宽表单元格形态
  （error_recovered 主值 + had_error + 两层错误计数 + loop 级重试配对计数的
  单元素 JSON 数组，2026-09-21 需求方确认统一 `[]` 包围、1.0.0→1.1.0 升版）与
  had_error=0 写空不进分母经需求方 2026-09-21 确认。聊天恒
  `NOT_COMPUTABLE / no_error_events_observable`（无 llm.error、无工具终态，
  禁止以 7.7 载荷代理口径替代）；研究组合 7.4/7.7/7.1/6.1 四个既有规范化函数
  作为单一事实来源，恢复 = 闭环 ∧ 非空正文（仅技术完成，不判质量）；唯一新增
  逻辑为 loop 级重试配对（同 loop 成功 llm.response 严格后继且 finishReason
  ≠ error，换路恢复不计入）。埋点 C/D 落地后须对齐升版。
- 最近完成：上下文压缩事件数（`compaction_event_count`，evaluator `1.0.0`，稳定性维度
  7.6）实现、测试与双模式真实日志基线完成，接入统一宽表第 17 指标列；整数主值与
  宽表注册经需求方 2026-09-21 确认。聊天恒 `NOT_COMPUTABLE / no_compaction_events`
  （通道不存在不等于无压缩，禁止记 0，不复活已取消的 usageTokens 代理）；研究信任
  单回合契约（与 7.4 一致），统计 `part.type == "compaction"` 且 `auto == true` 的
  唯一 part 数（按 `part.id` 去重），auto=false / auto 非法 / part.id 缺失分别分流
  诊断，同 id 冲突 auto 隔离，`message.updated` 压缩摘要属跨通道投影仅诊断；
  `compaction_semantics` 随结果输出。报告级 compaction_turn_ratio 与平均压缩次数
  由聚合层派生，评测器不做跨 Trace 聚合。
- 最近完成：高危口径改 be≥3 + 归因列注册进宽表（2026-09-24 需求方确认）。富集分析显示
  新口径 rate 对死亡无判别力(1.2x)、对业务错误富集强(4-5x)，而 **be≥3 对高消耗(3.6x)与
  死亡(2.4x)双重富集最强**，且 `be≥3∧rate` 反而削弱信号(死亡富集降到 0.4x)，故 bad case
  高危维度弃用"压缩>0∨重试>0.6"改 **be≥3**（`.tmp/rebuild_badcase_sheet.py`），
  重生成 badcase sheet：死亡 257 + 高危 409 = 666 条，高危原因附框架层归因(主因工具/接口)。
  同时把 `api_retry_attribution` 注册进 `summarize_metric_table` 列注册表(新增
  `_evaluate_api_retry_attribution` 适配器,与 backfill 共用 `build_attribution_cell`),
  未来 `stream_compute_metrics` 全量重跑自动产出该列(列集 22→23)。全量测试 991 项、
  ruff、mypy(src+scripts) 通过。
- 最近完成：框架层 API 重试归因落宽表（2026-09-24 需求方确认"新增独立列 + 只回填非零行 +
  只要框架层"）。collector `normalize_research_api_retry` 对同 callID 内同指纹重复
  产出 `api_retry_attribution`（by_tool / by_endpoint Top5 + retry_after_success，
  不含模型层跨 callID）；evaluator 升 `2.1.0` 透传到 metadata 并做守恒校验
  （by_tool/by_endpoint 计数和 = 重试数、retry_after_success ≤ 重试数）。新增宽表
  独立列 `api_retry_attribution`（22 列），由 `scripts/backfill_api_retry_attribution.py`
  只回填 `api_retry_rate>0` 的行（零重试留空）、其余列原样、断点续跑、temp+rename 原子替换。
  实测归因高度收敛：endpoint 集中于 `node-id-by-full-path`，工具集中于 mkdir/rm/write；
  回填完成(2026-09-24)：rate>0 的 1,191 行全部非空；框架层重复总数 3,182(与微平均
  分子一致)、其中前置成功仍重发 2,415(75.9%)，印证框架对已成功请求无效重发；
  by_tool = mkdir 1431 / rm 568 / write 512 / excel 253 / rename 166 / cp 165 / mv 87；
  by_endpoint = node-id-by-full-path 3041(95.6%) / yjs-context-read 140。
  全量测试 991 项、ruff/mypy 通过。
  **遗留**：该列由 backfill 产出——2026-09-24 稍后已随 `tool_error_attribution` 批次
  一并注册进 `summarize_metric_table` 列注册表，该遗留项解除；
  7.11 高危阈值仍待按新分布重调。
- 最近完成：工具错误归因落宽表（`tool_error_attribution`，2026-09-24 需求方确认
  "只回填大于 0 的行 + 单独开一个脚本只算新增列 + 归因数据也要进宽表脚本"）。
  **动机**：`ToolErrorRateEvaluator` 早已在 metadata 产出 `error_calls`
  （逐次失败的 tool_name + attribution），但 `src/reporters/metric_table.py`
  只从 metadata 提取 `partial_issues` / `partial_issue_reasons`，
  该字段在入宽表时被静默丢弃——结果是报告与 bad case 只能看到"发生 N 次工具错误"、
  定位不到坏在哪个工具（实证：宽表 1,624 行有工具错误，单元格内无 `tool_name`；
  历史 `docs/PROJECT_STATUS.md` 里的工具分布来自一次性脚本
  `.tmp/baseline_tool_error_rate.py`，从未进过正式流水线）。
  新增宽表列 `tool_error_attribution`（紧跟 `tool_error_rate`；单元格为
  by_tool + by_attribution 的单元素 JSON 数组）。口径经需求方确认：只统计 error
  终态，degraded 不带工具名入列；**不做 Top-K 截断**（`error_call_count` 中位 2、
  p99 17、最大 59，截断只会砍掉撞墙最严重的长尾日志的尾部工具）。
  实现复用 `_counter_items` 作为两列归因排序的单一实现（`_topk` / `_topk_all`
  为截断与不截断两种调用），单元格构造 `build_tool_error_attribution_cell` 与
  backfill 脚本共用。`scripts/backfill_tool_error_attribution.py` 只处理
  `error_call_count > 0` 的 1,624 行、其余列原样、断点续跑、temp+rename 原子替换。
  新增测试 5 项（单元格纯函数）+ 宽表列定位行为 1 项；全量 997 项通过、
  ruff 通过、mypy(src+scripts) 通过。
  **遗留**：`api_retry_attribution` 的 Top5 截断与本列的不截断是有意差异，
  两列不可按同一口径互相解释；backfill 未覆盖的 `degraded` 终态若后续要定位，
  需另立项（不在本列范围）。
- 最近完成：仓库整理与作业手册化（2026-09-24，为推 GitLab 做准备）。
  `.gitignore` 补齐泄露缺口：新增忽略 `.tmp/`（一次性实勘脚本与中间 CSV/JSONL 含真实
  log_id 与指标数值）、`data/data/`（驱动脚本相对路径误用产生的嵌套数据目录）、
  `.mcp.json` / `.codex/`（本机工具链配置）、以及两份外部依据文档
  `AI投研平台模型评测方案_v1.8.md` / `Agent测评框架_wechat.md`（只留本地参考，不入库）。
  新增 `docs/runbooks/`（索引 + 打捞日志 / 全量跑宽表 / 生成报告三篇），把三段日常作业的
  输入、参数、断点续跑纪律、产出自查点与常见问题固化；`AGENTS.md`、`START_HERE.md`、
  `.cursor/rules/project-startup.mdc` 同步指向该目录。
  修正文档死链与陈旧清单：`START_HERE.md` 与 cursor 规则里已删除的 `共性指标.md` 引用
  改为权威口径 `初步入库评测指标.md`；README 的产出列集从 11 列（含已退出的
  `citation_traceability_rate`）更新为「4 身份列 + 注册表全部指标列」，并补 `docs/runbooks/`
  与 `scripts/` 结构说明；`code-map.md` 补登记 `docs/runbooks/`、报告技能与其脚本、
  `tests/scripts/test_backfill_api_retry_attribution.py`、`test_stream_compute_metrics.py`
  及 8 个此前漏登记的 collector/evaluator 测试文件。
  同步 `metric-report` 技能：`JSON_EXTRA_COLS` 补齐 5 个此前未覆盖的指标列
  （`max_tool_token_share` / `max_tool_latency_share` / `turn_completion_rate` /
  `api_retry_attribution` / `tool_error_attribution`，第 0 节可计算性清单 14→19 列），
  新增列集漂移自检（宽表出现未覆盖列时报告显式告警而非静默漏列），
  第 1 节列集演进补两条归因列与释义，SKILL.md 修正「17 列」陈旧口径、
  `retry_semantics` 陈旧表述（改为 fingerprint_dup_v2）与留空语义说明。
  可验证性：全量 997 项测试通过、`ruff check .` 全仓通过、`mypy` 171 源文件通过；
  报告脚本用真实宽表复跑并与改前输出逐单元格比对零差异，另以合成加列宽表验证漂移告警生效。
  测试与格式基线：为报告脚本（f-string 内拼 HTML/CSS，折行会伤可读性）在
  `pyproject.toml` 加 `per-file-ignores = ["E501"]`；本批新增文件统一跑
  `ruff format`，仓库内既有未格式化文件（9 个已提交 + 1 个本批新增外的）保持原样未触碰。
- 最近完成：宽表全量重算（2026-09-24，fingerprint_dup_v2 生效）。`stream_compute_metrics`
  对 7,663 任务回源重跑（ok 6,007 + 断点跳过 1,578；missing 72、no_query_version 6、
  error 0），刷新 `data/processed/metric_table_full.csv`（7,582 行 × 21 列，log_id 唯一
  无重复）。新旧对照（同 7,582 log_id）：`api_retry_rate` 旧 suffix 口径 mean 34.76%/
  median 35.71% → 新 fingerprint_dup_v2 **微平均 3,182/61,530 = 5.17%**、单日志 median
  0.00%/p90 14.29%/p99 28.57%/max 46.97%、零重试占 71.1%、可计算 4,125/7,582、
  业务错误 5,160；**>0.4 仅 3 条、>0.6 为 0 条**，印证 7.11 旧阈值(0.4/0.6)在新口径下
  几乎空转、必须重调。其余 19 列与旧表逐单元格一致；唯一额外变化是 `llm_error_count`
  顺带升级到当前 schema（新增 total_call_count/model_calls，旧表本已过期）。
  旧 suffix-v1 表留档 `.tmp/metric_table_prev_suffixv1_20260923.csv`。
- 最近完成：框架 API 重试率口径重设计（`api_retry_rate`，evaluator `1.1.0`→`2.0.0`，
  `retry_semantics` 由 `request_id_suffix_v1` 修订为 `fingerprint_dup_v2`，2026-09-23
  需求方确认"保留列名原地升版 + 任意位置指纹重复 + 本轮只改定义阈值下轮再调"）。
  动机：对全量 3,586 条含重试日志实勘发现 requestId 尾序号 `-N` 是"同一 callID 内
  第 N 个 API 请求"的序号而非重试计数（120 日志 4,787 相邻 apiCall 对中 83.8% 入参
  不同，属一次工具调用内的工作流步骤，如 查路径→建文件→读文档），旧口径把大量工作流
  步骤误计为重试、实测高估约 3 倍。新口径重试 = 同一 `(callID, endpoint+入参指纹)`
  重复出现（组内次数-1），指纹剔除 requestId/messageId/sessionId/ownerId；成功语义
  修正为"response 为对象但缺 code 键 = 成功"（成功响应直接返回业务载荷），仅非对象
  响应进 undeterminable；元数据新增 `api_request_depth_max`（调用内最大请求序号，
  保留工作流深度信号）。60 条 OSS 验证（正式 collector+evaluator）：微平均重试率
  11.38%、单日志中位 12.68%/p90 25.49%/max 46.97%、业务错误 776、深度集中 4~9，
  与旧口径 ~35% 对照印证高估。测试 26 项（collector+evaluator）更新后全绿，全量
  985 项通过、ruff/mypy 通过。**遗留**：7.11 高危画像的 `rate>0.4/0.6` 阈值按旧
  suffix 口径标定、现已失配，下轮须按新分布重选阈值并重算高危与 bad case；宽表
  `api_retry_rate` 列与报告的重算待需求方触发（全量回源约 1.5h）。
- 最近完成：框架 API 重试率（`api_retry_rate`，evaluator `1.1.0`，2026-09-21
  单元格统一 `[]` 包围升版，
  `retry_semantics = request_id_suffix_v1`，稳定性维度 7.8）与高危回合画像
  （`high_risk_turn_profile`，evaluator `1.0.0`，规则版本
  `high_risk_profile_v2`，稳定性维度 7.11）实现、测试与双模式真实日志基线
  完成；7.8 接入统一宽表新增指标列（列于 compaction_event_count 之后；重跑
  暂缓，见"当前里程碑"），7.11 按
  权威口径不注册宽表。7.8 计量工具内部传输/业务层框架重试（requestId 尾序号
  ≥ 2），不是埋点 D 模型级重试，两者禁止互相代理；(callID, requestId) 去重、
  畸形 requestId 保守计入分母不进分子、业务错误字面口径（code 成功集 +
  success≠true）、非对象/缺 code 进 undeterminable、覆盖率交集口径随值输出
  （孤立 debug / 缺 callID 分流诊断）；聊天恒 `NOT_COMPUTABLE /
  no_tool_debug_events`（通道不存在不等于无重试，禁止记 0）。7.11 为组合式
  单一事实来源（7.5 范式）：7.8 重试率硬依赖不可计算即透传（step 数再高也
  不可判定）、7.6 压缩与 7.1 闭环为特征清单诊断项、step 数按 messageID 去重
  （7.10 同口径）；冻结阈值 steps > 30 ∧ rate > 0.4（严格不等式），闭环是
  特征不是条件（missing_turn_boundary 时 turn_completed 为 null 不阻断），
  空结果数特征项待 7.9 立项后补入并升版（需求方 2026-09-21 确认本批省略）。
  7.8 宽表单元格为单元素 JSON 数组（统一 `[]` 包围），7.11 为报告级组合规则
  不注册宽表列、单元格保持单字段紧凑 JSON 对象；endpoint 与请求/响应正文不落盘。埋点 D
  落地后 7.8 须与其对齐区分命名，7.11 补空结果特征时升版。
- 最近完成：逐工具指标全范围口径显式化与 respond 泄漏修复（2026-09-21 需求方
  确认范围）。实证复核四个逐工具指标（`per_tool_call_tokens` /
  `max_tool_token_share` / `per_tool_call_latency_ms` / `max_tool_latency_share`）
  现状已覆盖全部业务工具类型：研究 43/43、聊天 39/39 种工具零遗漏（文件类
  bash/edit/write/read/glob/grep/cp/rm/mkdir/rename、文档类 word/excel、skill、
  task、run_code 与全部 MCP，从不按 MCP 手册过滤）；task（研究侧子 Agent 委派）
  经需求方确认保留计入，报告级可按工具名单独切片。同批修复唯一实证口径不一致：
  聊天 `respond`（agent_return）泄漏进 `per_tool_call_latency_ms`（2026-09-17
  样本 13 行），根因为 `tool_resource_usage` 聊天规范化只排除 agentName 委派；
  修复后两类排除进入 `excluded_agent_delegation_count` / 
  `excluded_agent_return_count` 诊断（委派此前为静默丢弃），被排除调用的结果
  载荷不算孤立结果。`per_tool_call_latency` 与 `max_tool_latency_share`
  evaluator 1.0.0→1.1.0（后者指标值不变：respond 排除上移，组合层名称过滤
  降级为安全网、计数继承上游）。权威口径 1.1/1.2/2.1/3.1/3.3 与
  `docs/specs/metrics.md` 同步：废弃"工具类别目录 / framework_control /
  missing_tool_category_catalog"陈旧措辞（该目录从未实现），改为按日志结构
  判定的显式排除规则 + 全工具范围声明。
- 下一动作：等待引用可溯率（已退出宽表注册，评测器与测试保留）、回合完成率、
  续跑重启次数、模型层错误数、工具错误率、
  错误自恢复率、上下文压缩事件数、框架 API 重试率与高危回合画像的验收确认，
  以及 respond 泄漏修复后 `per_tool_call_latency_ms` / `max_tool_latency_share`
  （evaluator 1.1.0）的重新验收确认；
  宽表 121 行全量重跑已于 2026-09-21 经需求方确认后执行完毕（列集：剔除
  citation_traceability_rate、新增 api_retry_rate，共 17 指标列），并在 5 个
  JSON 列统一 `[]` 包围后再次重跑（差异严格局限于该 5 列且内容零变化），
  重跑驱动为 `.tmp/rerun_metric_table_drop_citation.py` 与
  `.tmp/rerun_metric_table_wrap_json.py`（均含旧表备份、OSS 目录名 user_id 回退
  与逐单元格对比），旧列集/旧形态表已删除，禁止新旧混拼。respond 修复后的
  宽表重跑按需求方指示暂缓：当前表聊天时延列仍为修复前（1.0.0）形态，
  预期差异 13 行（1 行转空）、研究侧零差异，恢复执行用
  `.tmp/rerun_metric_table_api_retry.py`（已改为按列 diff 统计并量化 respond
  修复差异），重跑前禁止把该列与 1.1.0 元数据混比。

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
逐工具时延链 respond 泄漏修复（per_tool_call_latency_ms 1.1.0 /
max_tool_latency_share 1.1.0, 2026-09-21）:
  pytest 全量 971 项通过（本批新增 5 项回归: tool_resource_usage 4 +
  tool_call_latency 1）、ruff check src/tests/scripts 全仓通过、mypy 通过
  （169 源文件）、本批触及 9 个文件 ruff format --check 通过。
  真实日志重算（只读驱动 .tmp/baseline_latency_respond_fix.py, 74 聊天 +
  47 研究回合）: 聊天 57 computed / 17 no_tool_calls；respond 排除 17 次
  调用（其中 13 行曾进入旧表时延列）、agentName 委派排除 180 次（此前静默
  丢弃，现进诊断）；逐工具项 213（null 1）、平均值范围 348.5~31611.8ms；
  与修复前宽表差异严格限于 13 行聊天 per_tool_call_latency_ms（含 1 行
  纯 respond Trace 转空为 no_tool_calls）；研究 47/47 零差异；
  max_tool_latency_share 与 tool_calls_count 列零差异（respond 在两处
  原已排除）。工具范围实证: 研究原始 43 种工具 = 表内 43 种、聊天 39 种 =
  表内 39 种，零静默丢弃（.tmp/inspect_tool_scope.py）。
  宽表重跑: 按需求方 2026-09-21 指示暂缓，data/processed/metric_table.csv
  聊天时延列仍为修复前 1.0.0 形态，未发生写入。
宽表列集剔除 citation_traceability_rate（2026-09-21）:
  应需求方指示，引用可溯率退出统一宽表 CSV 注册（评测器、collector 与各自
  测试全部保留，仅移除 scripts/summarize_metric_table.py 的 import、适配函数
  与 MetricColumn 注册项）；stream_compute_metrics 表头随 METRIC_COLUMNS 自动
  同步。pytest 全量 966 项通过（宽表脚本测试同步更新：整行字典期望移除该列、
  新增"列不在表头"负向断言、issue_count 17→16、summary metric_count 18→17）、
  本批 2 个文件 ruff check 与 ruff format --check 通过、mypy 按项目配置通过
  （169 源文件）。
  固定表全量重跑（此前 api_retry 批次"暂缓重跑"指示经需求方确认后解除）:
  备份旧表至 .tmp/metric_table_prev_cite.csv 后删除重跑，121 行 × 17 指标列
  写入 data/processed/metric_table.csv；与旧表按 log_id 对齐，剔除列恰为
  citation_traceability_rate、新增列恰为 api_retry_rate（并行批次既有注册，
  首次落入固定表，研究 32/47 行非空、聊天 74 行空），20 个共有列逐单元格
  零差异；驱动 .tmp/rerun_metric_table_drop_citation.py。
框架 API 重试率与高危回合画像（api_retry_rate 1.0.0 / request_id_suffix_v1,
high_risk_turn_profile 1.0.0 / high_risk_profile_v2, 2026-09-21）:
  pytest 全量 966 项通过（本批新增 47 项: 7.8 collector 15 + evaluator 10、
  7.11 collector 12 + evaluator 10）、ruff check src/tests/scripts 全仓通过、
  mypy 通过（169 源文件）、本批 8 个新文件 ruff format --check 通过。
  真实日志基线（74 条聊天 + 47 研究逻辑回合, evaluator 重算）:
  7.8: 聊天 74/74 NOT_COMPUTABLE / no_tool_debug_events；研究 32 computed、
  15 no_tool_debug_events、0 no_observable_api_calls；唯一 apiCalls 572
  （原始 573 含 1 次 (callID,requestId) 重复投影）、重试 210、微平均
  210/572 ≈ 36.71%（实勘原始口径 36.6%）；malformed requestId 0；业务错误
  53（11410×38、11443×7、11442×6、11430×2，与实勘逐项一致）；undeterminable
  唯一 519（实勘原始 520 = 68 非对象 + 452 缺 code，差 1 条为该重复投影）；
  覆盖率回合级合计 477/1,216 ≈ 39.2%、孤立 debug 0（实勘 506/1,586 ≈ 31.9%
  为原始文件级口径，两者不得混比）；ecfa41e4 全回合 90/200 = 0.45 与实勘
  重试风暴结论一致。
  7.11: 聊天 74/74 透传 NOT_COMPUTABLE；研究 32 computed、15 透传；命中
  4/32（占全部回合 4/47 ≈ 8.5%）: ecfa41e4（120 步/0.45/未闭环）、40bab013
  （57 步/0.4167/未闭环）、060a7676（60 步/0.5172/闭环）、f3640858（31 步/
  0.5909/闭环），与阈值敏感性实勘逐一吻合；命中回合未闭环率 2/4 = 50% vs
  全样本 4/47 ≈ 8.5%（5.9 倍富集）；单元格 turn_completed 特征与宽表
  turn_completion_rate 列逐回合一致（47/47 零差异）；compaction 特征为
  part.id 去重后计数（060a7676 = 2，实勘原始 4 含重复投影）。
  宽表重跑: 本批记录时按需求方指示暂缓；2026-09-21 需求方确认 citation 列
  剔除后已随该批次恢复执行并写入（见下条"宽表列集剔除 citation_traceability_rate"
  基线），api_retry_rate 列首次落入固定表，研究 32/47 行非空、聊天 74 行空
  （no_tool_debug_events），与本批独立基线一致。
上下文压缩事件数（compaction_event_count, evaluator 1.0.0, 2026-09-21）:
  pytest 全量 918 项通过（本批新增 25 项: collector 14 + evaluator 11；总数含
  并行 7.4/7.5/7.7 批次测试）、本批文件 ruff check 与 ruff format --check 通过、
  本批新增模块 mypy 通过。
  真实日志基线（74 条聊天 + 47 研究逻辑回合, 51 turn.start 段合并）:
  聊天 74/74 NOT_COMPUTABLE / no_compaction_events；研究 47/47 computed，
  去重后压缩事件分布 {0: 24, 1: 18, 2: 5}，合计 28 次、涉及 23 个回合
  （compaction_turn_ratio = 23/47 ≈ 48.9%，平均 0.596 次/回合）。与实勘对账:
  原始 compaction part 事件 49 条 = 唯一 28 + 重复投影 21（21 个 part.id 各
  投影恰 2 次、auto 全为 true、49/49 无手动/无效 auto）；实勘"单回合最多 4 次"
  为未去重原始口径，按 part.id 去重（权威口径要求）后单回合最多 2 次；
  d426dfff 与 7d8e8f6e 均为唯一 2 次（实勘原始 4 次），"压缩后仍
  context_overflow"联动事实不变；message.updated 压缩投影 84 条（流式重发，
  仅诊断）；conflicting / missing_id 均为 0。
  宽表重算 121 行写入 data/processed/metric_table.csv（重算时点指标列含并行
  批次共 17 列，本列居第 17）: 本列聊天 74 行空（NOT_COMPUTABLE）、研究 47 行
  非空整数；重跑确定性验证 21 个共有列（4 身份 + 17 指标）逐单元格零差异。
  对账中曾见 4 处 user_id 差异，属并行批次已登记的驱动 user_id 提取修复
  （见 metrics.md 第 19/20 节），当前表研究行 user_id 与 OSS 目录 uid 47/47
  一致、聊天行与源 userId 74/74 一致，与本批指标列无关。
错误自恢复率（error_recovery_rate, evaluator 1.0.0, recovery_semantics degraded_v1, 2026-09-21）:
  本批测试 31 项通过（collector 14 + evaluator 17）、本批文件 ruff check /
  format --check 与 mypy 通过。
  真实日志基线（74 条聊天 + 47 研究逻辑回合）:
  聊天 74/74 NOT_COMPUTABLE / no_error_events_observable；研究 14 computed
  （had_error=1）、33 no_error_events；报告级 error_recovery_rate = 11/14
  ≈ 78.57%，recovered 分布 {1: 11, 0: 3}。模型错误回合恢复 5/6（唯一未恢复
  e7b0e1fd，与 7.5 实勘一致）；工具错误回合恢复 6/9（首次登记；未恢复
  e7b0e1fd/ecfa41e4/b094b710 恰为 7.1 未闭环回合，40bab013 无错误事件不进
  本指标分母）；had_error 构成 = 模型 6 ∪ 工具 9，重叠 1（e7b0e1fd）= 14。
  loop 级配对: retry_recovered 合计 4（35e9abf0×3 + 931da0c5×1，与实勘一致；
  7d8e8f6e 换路恢复正确不计入，retry_rec=0 且回合级 recovered=1）、unpaired 3、
  loop_missing 2（= 实勘 2/9）；正文通道 final_text 9、write_artifact 5。
  宽表: 本批列注册后，现行 data/processed/metric_table.csv（121 行 × 17 指标
  列）由并行 compaction 批次的重算驱动写入，本批列统计与独立基线脚本逐项一致
  （研究 14 行非空 JSON、107 行空 = 聊天 74 + 研究 no_error_events 33）；
  本批注册时点的旧 19 列表已被该批次按列集变更规则删除，逐单元格零差异对比
  不可执行，改以评估器独立运行直证列值。
工具错误率与错误归因（tool_error_rate, evaluator 1.0.0, tool_error_attribution_v1, 2026-09-21）:
  pytest 全量 851 项通过（本批新增 45 项: collector 27 + evaluator 18）、
  ruff check src/tests/scripts 全仓通过、mypy 通过（153 源文件）、本批 8 个文件
  ruff format --check 通过。
  真实日志基线（74 条聊天 + 47 研究逻辑回合）:
  聊天 54/74 computed、17 no_tool_calls、3 no_terminal_tool_calls；微平均
  6/746 ≈ 0.80%；49/54 零错误、单 Trace 最高 13.33%；归因 model_misuse 2
  （get_stock_details 参数约束 + searchIndustry_indicators tool not found）、
  infrastructure 1、data_availability 3（均 annual_report_extract code500）、
  unclassified 0——与权威口径 7.7 实勘 6 次硬失败逐项一致；degraded 5
  （nl2sql 4 + finance_query_agent 1，严格字面口径；其余 17 条 code:1 自检
  变体按确认口径计成功）；空串 error_code 硬边界复核: 全文件 50 处深层空码
  （debug_trace.nl2sql.nodes[]）全部判成功，零误判。
  研究 47/47 computed；微平均 11/1299 ≈ 0.85%；38/47 零错误、单回合最高 8.00%；
  归因 model_misuse 7（INPUT_INTERRUPTED×3、batch 空 ops×2、路径越权×2）、
  infrastructure 4（MCP -32001×2、Document API 超时×1、socket×1）；工具分布
  grep2/word2/bash2/searchForeignReports2/write1/edit1/searchInternet1 与实勘
  逐项一致；全文件 error 事件 14 条中 3 条（edit/oldString）落在回合窗口外，
  回合内 11 条 = 分母口径。
  宽表重算 121 行 × 15 指标列写入 data/processed/metric_table.csv（旧 14 列 CSV
  已按列集变更规则删除重跑）: 新列聊天 54 行、研究 47 行非空，20 个空单元格对应
  no_tool_calls(17)/no_terminal_tool_calls(3)；与前一版按 log_id 对齐，14 个
  共有指标列逐单元格零差异；4 个研究行 user_id 差异（旧='' 新=uid）来自上一版
  生成驱动缺少 OSS 目录名回退提取，与本指标无关（与 llm_error_count 批次记录的
  同源现象）。
模型层错误数（llm_error_count, evaluator 1.1.0, llm_error_class_v1, 2026-09-21）:
  1.0.0 首版（主值+分类 JSON）当日按需求升版 1.1.0：单元格增补 error_models
  出错模型路由数组（单路由回合全量归因，多路由/无路由不归因进诊断）。
  pytest 本批范围 40 项通过（collector 19 + evaluator 15 + 脚本列测试 6）；升版时
  全量 893 项通过 + 1 项失败属用户 WIP 的 error_recovery 并行批次范围，本批未触碰；
  本批文件 ruff check/format --check 与 mypy 通过。
  真实日志基线（74 条聊天 + 47 研究逻辑回合）: 聊天 74/74 NOT_COMPUTABLE /
  no_model_error_events；研究 47/47 computed，6 回合非零（合计 9 事件）、41 回合
  零错误；分类分布 provider_timeout 4、protocol_corruption 2、context_overflow 1、
  billing_failure 1、queue_timeout 1、unclassified 0，与 7.4 实勘基线逐项一致；
  llm_error_turn_ratio = 6/47 ≈ 12.8%；诊断合计 duplicate_error_event 1
  （turn_027 loop=1 重复投影）、message_error_projection 4、finish_reason_error 1、
  loop_missing 2。
  出错模型归因基线: 47/47 回合单路由，model_unattributed 合计 0；错误路由分布
  pro/comein-auto-model 8 条（5 回合: 7d8e8f6e、3534673b、35e9abf0、364cca9f、
  931da0c5）、glm-5.3/comein-auto-model_openai 1 条（e7b0e1fd, queue_timeout）；
  全样本路由分布 pro 38 回合 / glm-5.3 6 / deepseek-v4-flash 2 / lite 1。
  modelID 为平台自动路由别名，真实后端模型名不在日志内（仅子 Agent task metadata
  偶见 gpt_5_6_luna_medium_pro），按真实模型归因需上游在错误事件或消息元数据落
  实际 modelID。
  宽表重算 121 行（重算时 15 指标列，含并行批次 tool_error_rate 列）写入
  data/processed/metric_table.csv: 本列聊天 74 行空、研究 47 行非空 JSON（6 回合
  含非空 error_models）；与前一版按 log_id 对齐，除本列 schema 升级外其余指标列
  逐单元格零差异；4 个研究行 user_id 差异为既知输入切分现象，与本批无关。
续跑重启次数（continuation_restart_count, evaluator 1.0.0, 双因素 JSON 口径, 2026-09-21）:
  pytest 全量 854 项通过（本批新增 25 项: collector 13 + evaluator 12；总数含并行
  7.4/7.7 批次测试）、本批文件 ruff check 与 ruff format --check 通过、本批
  新增模块 mypy 通过。注意: 用户 WIP 的 tool_error_rate 测试文件存在 mypy
  错误与 ruff 告警，属并行批次范围，本批未触碰。
  真实日志基线（74 条聊天 + 47 研究逻辑回合, 51 turn.start 段合并）:
  聊天 74/74 NOT_COMPUTABLE / no_continuation_mechanism；研究 47/47 computed，
  全部输出双因素 JSON，重启分布 {0: 44, 1: 2, 2: 1}，合计 4 次、涉及 3 个回合
  （060a7676 = {1,1}、2a5e8ac2 = {1,1}、ecfa41e4 = {2,0} 重启仍死），与实勘基线
  逐一吻合；单元格 turn_completed 因素与 7.1 独立判定逐回合一致（零差异）；
  restart_turn_ratio = 3/47 ≈ 6.4%，restart_recovery_rate = 2/3；
  duplicate_turn_start / invalid_ts 诊断均为 0，turn_start_count 合计 51 与段数自洽。
  宽表重算 121 行写入 data/processed/metric_table.csv（重算时点指标列已含并行批次
  扩至 15 列，本列居第 13）: 本列聊天 74 行空（NOT_COMPUTABLE）、研究 47 行双因素
  JSON 非空；与重算前表按 log_id 对齐逐单元格对比，差异 47 处全部集中于本列
  （int → JSON 已确认格式变更），其余共有列零差异。
回合完成率（turn_completion_rate, evaluator 1.0.0, failure_template_catalog_v1, 2026-09-21）:
  pytest 全量 757 项通过（collector 14 + evaluator 7 新增）、ruff check src/tests/scripts
  与 mypy（141 源文件）通过、本批 8 个文件 ruff format --check 通过。
  真实日志基线（74 条聊天 + 47 研究逻辑回合）: 聊天 74/74 computed，73×1 + 1×0
  （固定失败模板命中 1）；研究 47/47 computed，43×1 + 4×0（未闭环: e7b0e1fd、
  ecfa41e4、b094b710、40bab013），duplicate/mismatch/duration 诊断均为 0。
  宽表重算 121 行 × 12 指标列写入 data/processed/metric_table.csv（旧 11 列 CSV 已按
  列集变更规则删除重跑）: 新列 121/121 非空；与前一版按 log_id 对齐逐单元格对比，
  11 个既有指标列与身份列零差异（临时驱动的研究行 user_id 改从 OSS 目录名提取后
  与既有表一致）；锚定率/引用可溯列非空行数 47/34 与 46/15，与已验收基线吻合。
  注意: .tmp/ 下既有实勘脚本存在 lint 告警（UTF-8 声明、行长），属用户临时文件，
  本批未触碰，ruff 验证范围为 src/tests/scripts。
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
宽表两列剔除重算（2026-09-20）: `per_tool_repeat_call_count` 与
  `mcp_tool_call_inflation_rate` 退出 CSV 注册，评测器与测试保留；pytest 全量 736 项
  通过、ruff check/format --check 与 mypy 通过。真实样本重算（74 条聊天 +
  47 条研究回合）写入 `data/processed/metric_table.csv`: 121 行、11 个指标列（含引用
  可溯率列）；与剔除前 `metric_table_with_grounding.csv` 按 `log_id` 对齐后 14 个共有列
  逐单元格对比零差异。注意: 流式入口 `stream_compute_metrics` 的断点续跑只按 `log_id`
  跳过、不校验表头，列集变更后旧结果 CSV 必须删除重跑，禁止新旧列集混拼。
引用可溯率实现与宽表接入（2026-09-20, evaluator 1.0.0, cite_v1/source_registry_v1）:
  pytest 全量 736 项通过（collector 17 + evaluator 15 新增）、ruff check/format --check
  与 mypy 通过；宽表第 13 列注册。
  真实日志基线（74 条聊天 + 47 条研究回合）: 聊天 46/74 computed、28 条
  no_extractable_citations；501 个唯一引用编号、338 个可解析，微平均 68.08%，满分 15 条、
  低于满分 31 条（最低 0.0）；numbering_scheme_mixed_suspected 9 条。
  研究 15/47 computed、32 条 no_extractable_citations；175 个唯一引用编号、36 个可解析，
  微平均 38.67%，满分 5 条、零分 10 条；139 个超界引用集中在 10 个回合
  （skill 计数器疑为 session 级累积，属引用协议缺口而非模型幻觉）。
  实勘支撑: 聊天累积编号假设验证 863/863 全部落在累积序列内；研究 write 通道
  3/3 编号命中注册表；final_text 通道 2 回合 65 处引用全部超界（[439] 型跨回合编号）。
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
