# Code Map

评测流水线方向：`collectors -> evaluators -> reporters`，数据和产物访问由 `storage` 提供。

- `docs/START_HERE.md`：每次新会话的稳定阅读入口和不可破坏约束。
- `docs/PROJECT_STATUS.md`：当前里程碑、下一动作、已知缺口和验证基线。
- `docs/specs/metric-development-order.md`：当前批次 10 项指标的唯一开发顺序；依次覆盖工具调用、
  Token 成本和时间维度。
- `docs/specs/metrics_initial.md`：当前批次 10 项指标的初始定义、双模式字段映射、异常边界和
  观测价值；指标确认后再进入集中规格与实现。
- `.cursor/rules/metric-calculability.mdc`：要求每个指标在编码和验收前分别说明聊天与研究
  模式的可计算性、缺失条件和稳定不可计算原因，禁止估算或静默省略模式。
- `.env.example`：本地运行配置模板；Judge 使用 `JUDGE_API_URL`、`JUDGE_API_KEY` 和
  `JUDGE_MODEL`，真实密钥只允许写入被忽略的 `.env`。
- `src/collectors/exact_duplicate_calls.py`：按 `traceId + toolId` 关联新版聊天 canvas 请求与
  跨消息结果，在内存中规范化工具名、参数和响应并比较摘要；旧版聊天结构不再支持，孤立
  Agent 返回、重复观察和冲突定义只输出安全诊断，不保留原始正文或摘要。
- `src/collectors/tool_call_statistics.py`：直接从新版聊天 `tool_call[]` 或研究 `part.type == tool`
  事件提取唯一调用；聊天排除结构化子 Agent 委派和 `respond`，研究按 `callID` 合并生命周期，
  再按精确工具名输出调用与重复计数，不保留参数、响应或消息正文。
- `src/collectors/tool_step_tokens.py`：按聊天消息位置或研究 `messageID` 关联工具调用与模型步骤
  Token；同一步骤同名工具只关联一次，多工具步骤执行完整 Token 关联，并保留逐工具覆盖诊断。
- `src/collectors/tool_token_share.py`：组合逐工具关联 Token 与 Trace 总 Token，统一无工具、覆盖
  不完整、缺少步骤关联和零分母原因，供最大工具 Token 占比评测使用。
- `src/collectors/mcp_tool_call_inflation.py`：从新版聊天 canvas 的跨 Agent/message
  `content.tool_call[]` 或研究事件流提取普通工具调用，排除子 Agent，按版本化 MCP 目录过滤，
  以调用 ID 去重并对能力 ID 与入参生成不可逆签名，输出调用、唯一签名和膨胀调用聚合计数；
  异步任务轮询调用（规范化入参恰为 taskId 单键）不进入分子分母、计入 `polling_call_count`，
  空入参调用计入 `empty_argument_call_count` 诊断；
  旧版聊天 `tool_call_queue` 不再支持。
- `src/collectors/required_parameters.py`：从版本化 MCP 手册构建顶层必填参数目录；聊天模式
  按 `traceId + toolId` 扫描新版 canvas 并排除子 Agent 调用，研究模式按 `toolCallId` 去重，
  统计 Schema 覆盖和非空顶层必填参数；旧版聊天结构不再支持，输出不含参数值或摘要。
- `src/collectors/tool_argument_schema.py`：把 MCP 手册类型、枚举和嵌套定义转换为递归
  Schema；聊天模式按 `traceId + toolId` 扫描新版 canvas 并排除子 Agent 调用，研究模式按
  `toolCallId` 去重；旧版聊天结构不再支持，额外字段仅诊断，不使调用非法。
- `src/collectors/parameter_value_accuracy.py`：从 MCP 手册构建工具与字段语义目录；聊天模式
  按 `traceId + toolId` 扫描新版 canvas 并排除子 Agent 调用，研究模式按 `toolCallId` 去重，
  生成只在内存流转的 Judge 候选；旧版聊天结构不再支持，长期结果不得保留 query 或参数值。
- `src/collectors/routing.py`：从 MCP 手册构建版本化能力目录；聊天模式扫描新版 canvas，
  按 `traceId + toolId` 去重普通工具请求，并从子 Agent 的 `arguments.query/task` 提取任务；
  研究模式按调用 ID 去重。聊天 `canvas.meta.tools` 与研究 `agent.mcp[].tools` 明确声明但
  手册缺失的 MCP 按唯一工具计入自动匹配路由；无法证明为 MCP 的框架工具只进入范围外诊断。
  旧版聊天结构不再支持，明确的路由前模型服务失败会在调用 Judge 前标记为不可计算。
- `src/collectors/subtask_redundancy.py`：扫描新版聊天 canvas 中跨 Agent/message 的子 Agent
  调用，从对象或 JSON 字符串参数提取任务正文，使用 NFKC、约束词元和字符 bigram 规则
  计算高置信度冗余聚合计数；旧版聊天结构不再支持，研究模式在结构化子任务契约确认前
  显式返回不可计算，任务正文和中间指纹不进入结果。
- `src/collectors/intent_recognition.py`：从新版聊天 canvas 跨 Agent/message 提取 query、
  子 Agent 任务、最终回答和按调用 ID 去重的 MCP 能力；研究模式额外选择 `turn.end` 前最后
  assistant 文本。日志显式声明但手册缺失的 MCP 仍作为行为证据，规则负责证据去重、前置
  失败、缺失字段和冲突诊断，旧版聊天结构不再支持，正文不进入持久化结果。
- `src/collectors/time_range_accuracy.py`：复用参数语义目录，聊天模式从新版 canvas 提取
  query、去重工具参数、最终回答和 `meta.create_time` 锚点，研究模式读取事件流；时间正文与
  参数值仅保留在内存 Judge 候选中。
- `src/collectors/evidence_quality.py`：使用显式 MCP 证据工具目录连接新版 canvas/研究调用与
  成功响应；聊天模式按 `traceId + toolId` 跨 Agent/message 关联请求和结果，隔离子 Agent、
  孤立结果及冲突生命周期，旧版 `tool_call_queue` 不再支持；证据按来源原生结构拆分并在
  内存去重，无边界长文本和派生工具不进入陈述支持率的证据输入。
- `src/collectors/statement_support.py`：复用证据单元抽取，从新版 canvas 的
  `meta.query/answer.answer` 提取聊天输入，或选择研究模式 `turn.end` 前最后一个 assistant
  文本；区分空证据、缺终态、不完整证据拆分和固定前置服务失败，旧版顶层聊天字段不再支持。
- `src/collectors/tool_timeout_rate.py`：构建版本化工具 SLA 与别名目录；聊天模式按
  `traceId + toolId` 跨 Agent/message 关联新版 canvas 请求与结果并排除子 Agent、孤立结果，
  研究模式按 `callID` 合并生命周期，规范化显式超时、SLA 超限和缺失时间超时；旧版聊天
  `tool_call_queue` 不再支持，不保留参数或响应正文。
- `src/collectors/tool_resource_usage.py`：按唯一调用提取聊天 `tool_result.durationSeconds` 或研究
  工具生命周期起止时间，逐调用隔离缺失、非法和冲突耗时；明确标记两种模式均无工具执行
  自身 Token，且不保留参数或响应正文。
- `src/collectors/tool_call_latency.py`：复用逐调用资源视图，按精确工具名聚合有效、缺失和错误
  耗时，输出逐工具总耗时及覆盖计数，不读取参数或响应正文。
- `src/collectors/max_tool_latency_share.py`：复用逐工具耗时统计，排除聊天 `respond` 结果侧
  Agent 返回并执行全量业务工具耗时覆盖检查，生成最大时延占比的安全分子与分母。
- `src/collectors/end_to_end_latency.py`：从聊天 canvas 的 meta/answer 创建时间提取秒级回合
  边界，或合并研究 `turn.start/turn.end` 并保留报告耗时与时间戳交叉校验输入；重复边界去重，
  冲突边界隔离，输出不含 query 或回答正文。
- `src/collectors/model_step_latency.py`：聊天模式收集 Agent message 自身耗时并排除 answer
  复制值；研究模式按 message ID 配对 step-start/step-finish 的事件时间，记录缺失、重复及
  时间戳交叉校验诊断，不保留消息或推理正文。
- `src/collectors/token_consumption.py`：聊天模式汇总模型消息 input/output Token 并排除工具结果
  消息及 answer 重复快照；研究模式按 message ID 去重 step-finish total，隔离缺失和冲突覆盖，
  不读取 cache 分项或正文。
- `src/collectors/output_token_ratio.py`：从聊天模型步骤或研究 `step-finish` 唯一快照提取输出与
  总 Token；要求完整覆盖，隔离非法、冲突和分项不一致，不保留模型正文。
- `src/collectors/cache_input_token_ratio.py`：按聊天 input 已含 cache、研究 input 不含 cache 的确认
  语义规范化缓存与真实输入 Token；按研究步骤 ID 去重并校验完整 Token 分项恒等式。
- `src/collectors/answer_evidence_grounding.py`：数值锚定率规范化。聊天模式从
  `canvas.answer.answer` 抽取"数值+单位" span，在请求侧关联的成功 `tool_result` 载荷
  （排除失败信号、孤立结果与 respond 返回）内做归一化精确匹配；研究模式输入为单个回合
  的合并事件序列，正文按 write 产物 > edit 片段 > 最后 assistant 文本选择，证据取
  chunkList 文本与非排除工具输出。匹配引擎 v2（`grounding_units_v2`/`grounding_norm_v2`）：
  单位换算渲染、尾零压缩、千分位剥离、证据侧斜杠与顿号组合数值展开、数字边界守卫
  （仅键尾为数字时检查右邻，放行"76%2026年"式无缝拼接）、个百分点↔% 互认、货币前缀
  （Rmb/$/¥…）与英文数量级（bn/mn/m…）渲染、单向舍入复述匹配（证据更精确→回答取整
  相等才命中，命中数进 `rounded_match_count` 诊断）；回答与证据正文只在内存流转，
  结果仅输出聚合计数、正文通道与版本。
- `src/collectors/__init__.py`：导出各指标的规范化类型与入口。
- `src/evaluators/base.py`：`BaseEvaluator` 抽象接口、统一结果对象和计算状态；指标值支持有限数值
  或用于单字段结构化输出的非空字符串。
- `src/evaluators/end_to_end_latency.py`：聊天模式计算请求到最终回答的时间戳差值；研究模式
  优先使用 `turn.end.durationMs` 并记录与边界差值的偏差，缺失、非法和负时序使用稳定状态与
  原因码隔离；evaluator 版本为 `1.0.0`。
- `src/evaluators/model_step_latency.py`：计算单条 Trace 的有效步骤算术平均，输出全量微平均
  所需的 `total_step_duration_ms` 与 `valid_step_count`，并严格校验覆盖和交叉诊断计数；
  evaluator 版本为 `1.0.0`。
- `src/evaluators/tool_calls_count.py`：汇总逐工具唯一调用数并计算 `tool_calls_count`；无业务工具
  调用时返回零，缺失标识、冲突定义或非法规范化结构按稳定原因码隔离；版本为 `1.0.0`。
- `src/evaluators/per_tool_repeat_call_count.py`：复用工具调用统计的严格校验，筛选发生重复调用的
  工具并按重复次数、调用次数和工具名稳定排序，输出单字段紧凑 JSON 数组；版本为 `1.0.0`。
- `src/evaluators/per_tool_call_tokens.py`：计算逐工具关联模型步骤的平均 Token；同一日志允许完整
  工具输出数值、缺失工具输出 `null` 和原因码，结果写入单字段紧凑 JSON；版本为 `1.0.0`。
- `src/evaluators/max_tool_token_share.py`：计算最大逐工具关联 Token 与 Trace 总 Token 的比例，
  支持合法零值和并列最大工具，并把逐工具名与比率写入单字段紧凑 JSON 数组；拒绝超过总量的
  非法归因，版本为 `1.0.0`。
- `src/evaluators/token_consumption.py`：仅在全部已识别模型步骤 Token 覆盖完整时求和，部分或
  全部缺失返回不可计算，非法与冲突快照返回错误；evaluator 版本为 `1.0.0`。
- `src/evaluators/output_token_ratio.py`：计算完整模型步骤的输出 Token 与总 Token 比率；合法零
  输出计算为零，零分母和覆盖缺失不可计算，版本为 `1.0.0`。
- `src/evaluators/cache_input_token_ratio.py`：计算缓存 Token 占真实输入 Token 的比例；合法零缓存
  计算为零，完整覆盖和分母边界使用稳定状态与原因码隔离，版本为 `1.0.0`。
- `src/evaluators/tool_resource_usage.py`：逐调用输出 `tool_call_duration_ms`；缺失耗时不补零，
  工具 Token 固定返回 `NOT_COMPUTABLE / no_observable_tool_token_usage`，禁止分摊模型步骤
  Token；两个 evaluator 版本均为 `1.0.0`。
- `src/evaluators/per_tool_call_latency.py`：计算同名工具有效调用的平均执行耗时并输出
  `per_tool_call_latency_ms`；全缺失工具项
  返回不可计算，非法、负时序或冲突调用只隔离受影响工具项，版本为 `1.0.0`。
- `src/evaluators/max_tool_latency_share.py`：计算累计耗时最高的业务工具占全部业务工具累计耗时
  的比例，支持并列最大工具并严格拒绝不完整覆盖和零分母，版本为 `1.0.0`。
- `src/evaluators/exact_duplicate_tool_calls.py`：使用安全聚合计数计算完全重复调用率，并输出
  不可比较调用、重复请求、重复结果及孤立结果诊断。
- `src/evaluators/mcp_tool_call_inflation.py`：校验 MCP 调用与请求签名计数，计算同任务内相同
  MCP 且相同入参的后续调用占比；轮询调用排除与空入参诊断随 1.2.0 引入，仅剩轮询调用时
  返回 `no_non_polling_mcp_calls`。
- `src/evaluators/required_parameter_completeness.py`：在 Schema 已覆盖范围内计算必填参数
  微平均完整率；覆盖调用数仅作为诊断，不派生辅助指标。
- `src/evaluators/tool_argument_schema_validity.py`：在 Schema 已覆盖调用上计算参数类型、
  枚举和嵌套结构合法率，仅保留安全诊断计数。
- `src/evaluators/parameter_value_accuracy.py`：定义可注入的版本化 Judge 接口，校验逐字段
  三态输出，隔离单调用失败并聚合稳定失败原因，仅输出参数值准确率主指标和安全诊断计数；
  evaluator 版本为 `1.4.0`。
- `src/evaluators/openai_compatible_judge_runtime.py`：所有 OpenAI-compatible Judge 的共享
  请求边界；负责严格 JSON Schema、兼容回退、请求大小上限、瞬态重试、输出截断检测和
  脱敏错误分类。
- `src/evaluators/openai_compatible_parameter_judge.py`：从 dotenv/环境变量加载 Judge 配置，
  通过 HTTPS OpenAI-compatible Chat Completions 接口请求结构化逐字段判定；程序为实际参数
  和遗漏候选生成稳定字段 ID，Judge 只返回三态 verdict，适配器再映射回原始路径。
- `src/evaluators/routing.py`：定义路由 Judge 与安全映射表契约，从 Judge 子任务派生标准能力
  并集，把日志声明但手册缺失的 MCP 计为自动匹配 TP；业务输出非法时携带安全原因码重试
  一次，重试后仅映射元数据非法时跳过持久化但保留合法能力计数；生成 TP/FP/FN 共享计数并
  分别计算 `routing_precision` 与 `routing_recall`，两个指标的新版 canvas 适配版本均为
  `1.3.0`。
- `src/evaluators/subtask_redundancy.py`：校验规则版本、固定阈值和安全聚合计数，计算
  `subtask_redundancy_rate`；新版 canvas 适配版本为 `1.1.0`。
- `src/evaluators/openai_compatible_routing_judge.py`：把整体意图、全部子任务、MCP Schema
  和历史安全映射提交给 OpenAI-compatible Judge；动态 Schema 约束安全 snake_case 标签和
  目录内能力 ID，Judge 只返回子任务路由，程序派生顶层能力并集，并支持安全校验反馈重试。
- `src/evaluators/intent_recognition.py`：严格校验固定八字段 Judge 契约，按 45% 意图覆盖、
  20% 规划、15% MCP 合理性和 20% 最终回答的代码权重计算连续得分；主意图错误或意图冲突
  硬归零，业务输出非法时安全重试一次；新版 canvas 适配版本为 `1.3.0`。
- `src/evaluators/openai_compatible_intent_judge.py`：把不可信 query 与最小代理行为证据提交给
  OpenAI-compatible Judge；固定枚举 Schema 与 three-shot 示例约束分项标签，使用共享严格
  结构化运行时并隔离供应商响应和密钥。
- `src/evaluators/time_range_accuracy.py`：第一阶段仅从 query 与锚点冻结用户预期时间字段并由
  程序分配稳定 ID，第二阶段逐 ID 聚合三态 verdict；包含明确年份、最新性、预测属性的确定性
  补漏、一次业务结构校验重试和安全 reason code，计算 `time_range_extraction_accuracy`；
  evaluator 版本为 `2.0.0`。
- `src/evaluators/openai_compatible_time_range_judge.py`：执行 query-only 需求提取和字段证据匹配，
  将工具参数标注为 data period、reporting period、retrieval window、frequency、value type
  或 free-text query，隔离检索窗口与业务期间，并对供应商异常做脱敏处理；prompt 版本为
  `2.0.0`。
- `src/evaluators/statement_support.py`：定义原子陈述支持 Judge 契约，严格校验证据引用、
  三态可验证结论、排除项和安全召回诊断，默认装配本地 Embedding 增强判定并计算
  `statement_support_rate` 与自动判定覆盖率；新版 canvas 适配版本为 `4.0.0`。
- `src/evaluators/embedding_statement_support.py`：定义可注入 Embedding Provider、
  sentence-transformers 本地适配器、按模型 revision 隔离的进程内缓存、Top-K 证据召回、
  灰区判定，以及时间/指标/数值/实体/否定/方向冲突守卫；不持久化正文或向量。
- `src/evaluators/deterministic_statement_support.py`：不调用 LLM，执行规则分句、建议排除、
  字符 bigram 检索、指标/年份/金额/百分比匹配、数值冲突识别和同比白名单推导，并向
  Embedding 增强判定复用结构规则。
- `src/evaluators/tool_timeout_rate.py`：校验策略版本、唯一调用及分类诊断计数，计算
  `tool_timeout_rate`；缺少工具 SLA 时不估算，新版 canvas 适配版本为 `1.1.0`。
- `src/evaluators/openai_compatible_statement_support_judge.py`：把不可信 query、最终回答和
  全部证据提交给 OpenAI-compatible Judge 的历史可注入适配器；不再是陈述支持率默认路径。
- `src/evaluators/answer_evidence_grounding.py`：校验锚定聚合计数与规则版本，计算
  `answer_evidence_grounding_rate`；无 span 返回不可计算，证据为空且回答含 span 时
  计算为 0 并携带无中生有信号，版本为 `1.0.0`。
- `src/evaluators/__init__.py`：导出公共契约和具体评测器。
- `src/reporters/metric_table.py`：通用指标宽表引擎；通过 `MetricColumn` 注入独立指标适配器，
  每行对应一条完整日志，固定输出用户、会话、统一日志主键和模式标识；每个指标只输出一个
  值列，未计算、错误和结构化指标的逐项局部状态及原因保留在表外问题摘要中。统一 `log_id` 在聊天
  模式取 Trace ID，在研究模式取用户消息 ID。
- `scripts/export_oss_jsonl.py`：按 Excel 清单(uid/session_id/msg_id)批量导出 OSS jsonl 日志的
  入口;拼接 `<根前缀>/<uid>/<session_id>/<msg_id>` list 前缀,每条前缀唯一匹配一个对象,
  并发下载到 `data/oss_jsonl/<uid>/<session_id>/` 并写 manifest.json(ok/missing/ambiguous/error),
  重跑自动跳过已下载文件;OSS 连接配置仅来自 CLI 参数与 OSS_* 环境变量。
- `scripts/salvage_oss_logs.py`：按 uid + 北京时间日期 + 问题开头文本从 OSS 打捞异常日志的
  入口;`--env prod/test/both` 指定环境(根前缀末段 prod/test 替换派生),分页列举
  `<根前缀>/<uid>/` 全部对象,按文件名解析 (msg_id, query 开头, UTC 时间戳),
  日期过滤按 UTC+8 换算,query 归一化后三级匹配(开头双向 → 包含+截断重叠≥4字 →
  当天全量回退 date_only),命中 msg 的 noquery 版本一并带出;默认只打印候选清单,
  `--download` 才下载到 `data/oss_salvage/[<env>/]<uid>/<session>/`(gitignored)
  并写 salvage_manifest.json,断点续传跳过已存在文件,单环境单 uid 列举失败隔离;
  复用 export_oss_jsonl 的连接与 .env 加载;Windows 控制台需 `python -X utf8` 运行。
- `scripts/summarize_metric_table.py`：聊天与研究 JSON 日志的 CSV 汇总入口；当前注册
  `tool_calls_count`、`per_tool_repeat_call_count`、`per_tool_call_tokens`、
  `total_token_consumption`、`max_tool_token_share`、`output_token_ratio`、
  `cache_input_token_ratio`、`per_tool_call_latency_ms`、`end_to_end_latency_ms`、
  `max_tool_latency_share`、`mcp_tool_call_inflation_rate` 和
  `answer_evidence_grounding_rate`；逐工具时延以
  仅含工具名和值的紧凑 JSON 数组写入单个 CSV 单元格，端到端时延与膨胀率直接写入数值；
  锚定率列的研究模式要求 payload 为单回合事件序列（多回合文件先按 sessionID 分组、
  按 turn.start 切分后送入，否则跨回合证据互相污染）；
  指标列经 `build_metric_columns(catalog)` 工厂构建，MCP 膨胀率列通过闭包持有仓库根
  `mcp_tool_manual.json` 构建的能力目录（`--mcp-manual` 可覆盖，手册缺失或非法时快速
  失败）；入口按
  模式提取身份字段并在 Trace ID 缺失时生成稳定匿名回退标识，后续指标只需增加适配函数和
  注册项。
- `src/storage/intent_route_mappings.py`：以 JSONL 去重保存不含原始文本和实体的意图签名、
  子任务类型与能力集合，并支持 Jaccard 相似映射检索。
- `src/storage/__init__.py`：导出安全意图路由映射存储入口。
- `tests/evaluators/test_base_evaluator.py`：评测器公共契约的行为测试。
- `tests/evaluators/test_openai_compatible_judge_runtime.py`：严格 Schema、400 兼容回退、请求
  上限、瞬态重试和输出截断的共享运行时测试。
- `tests/collectors/test_exact_duplicate_calls.py`：新版 canvas/研究完全重复识别、跨消息关联、
  请求和结果冲突、孤立结果、生命周期去重、缺失字段及敏感正文隔离测试。
- `tests/collectors/test_tool_call_statistics.py`、`tests/evaluators/test_tool_calls_count.py` 与
  `tests/evaluators/test_per_tool_repeat_call_count.py`：双模式直接调用计数、逐工具重复次数、稳定
  JSON 排序、生命周期去重、控制事件排除、零调用、缺失字段、冲突定义、输入不变和敏感正文
  隔离测试。
- `tests/collectors/test_tool_step_tokens.py` 与 `tests/evaluators/test_per_tool_call_tokens.py`：双模式
  工具—步骤关联、同名工具步骤去重、多工具完整关联、Token 缺失、生命周期去重、冲突隔离和
  逐工具局部不可计算输出测试。
- `tests/collectors/test_tool_token_share.py` 与 `tests/evaluators/test_max_tool_token_share.py`：双模式
  分子分母组合、无工具、覆盖不完整、并列最大、合法零值和非法归因隔离测试。
- `tests/collectors/test_output_token_ratio.py` 与 `tests/evaluators/test_output_token_ratio.py`：双模式
  输出与总 Token 汇总、重复快照、完整覆盖、零输出、零分母、冲突和非法分项隔离测试。
- `tests/collectors/test_cache_input_token_ratio.py` 与
  `tests/evaluators/test_cache_input_token_ratio.py`：双模式
  缓存与真实输入汇总、包含关系、重复快照、完整覆盖、合法零值和分项冲突隔离测试。
- `tests/collectors/test_tool_call_latency.py` 与
  `tests/evaluators/test_per_tool_call_latency.py`：双模式逐工具耗时聚合、部分与全部缺失、局部
  非法或冲突隔离、稳定结构化输出和规范化输入校验测试。
- `tests/collectors/test_max_tool_latency_share.py` 与
  `tests/evaluators/test_max_tool_latency_share.py`：业务工具范围、双模式累计耗时、完整覆盖、
  零分母、非法耗时、并列最大工具和严格规范化输入测试。
- `tests/scripts/test_export_oss_jsonl.py`：OSS 批量导出的 Excel 行解析(含数字 uid 规范化与
  缺列跳过)、前缀拼接、单条状态分类(ok/missing/ambiguous/error 隔离)、manifest 汇总与
  断点续跑测试,使用假 bucket 隔离真实 OSS。
- `tests/scripts/test_salvage_oss_logs.py`：打捞脚本的文件名解析(含 noquery 与非法名)、
  北京时间日期边界(UTC 跨日)、query 归一化三级匹配(开头/包含/截断重叠)与回退、
  环境前缀派生、prod+test 双环境打捞与目录分层、分页列举、默认不落盘、下载断点续传
  和单 uid 失败隔离测试,使用分页假 bucket。
- `tests/reporters/test_metric_table.py` 与 `tests/scripts/test_summarize_metric_table.py`：指标列注册、
  宽表字段隔离、单指标失败与局部状态隔离、身份字段来源优先级、CSV 表头、聊天/研究混合
  输出和稳定匿名回退标识测试。
- `tests/collectors/test_mcp_tool_call_inflation.py`：新版 canvas 跨 Agent/message 扫描、子 Agent
  排除、旧结构拒绝、MCP 范围过滤、规范化入参签名、调用 ID 去重、冲突隔离及研究模式回归测试。
- `tests/collectors/test_required_parameters.py`：必填参数目录解析、新版 canvas/研究参数计数、
  子 Agent 排除、跨消息去重、冲突定义、缺失字段、非法结构、占位值、未知 Schema、零必填
  参数及旧版聊天结构拒绝测试。
- `tests/collectors/test_tool_argument_schema.py`：新版 canvas/研究 Schema 校验、子 Agent
  排除、跨消息去重、冲突定义、缺失字段、非法结构、旧版聊天拒绝、类型、数组元素、枚举、
  嵌套必填、额外字段、未知工具和参数解析测试。
- `tests/collectors/test_parameter_value_accuracy.py`：新版 canvas/研究 query 与 Judge 候选提取、
  子 Agent 排除、跨消息去重、冲突定义、缺失字段、非法结构、旧版聊天拒绝、手册描述目录、
  参数解析失败和内存参数不可变测试。
- `tests/collectors/test_answer_evidence_grounding.py`：双模式锚定引擎行为测试——单位
  换算渲染、尾零压缩、千分位剥离、数字边界守卫、组合数值展开、年份与代码块排除、
  失败信号/孤立结果/respond 排除、无中生有信号、研究三正文通道、chunk 去重、自我锚定
  防护、结构错误隔离与敏感正文不落结果测试。
- `tests/collectors/test_routing.py`：新版 canvas 的跨 Agent/message 意图提取、普通工具请求
  去重、缺失字段、非法容器、旧版结构拒绝、前置失败、日志声明的手册外 MCP 自动匹配，
  以及研究前缀映射和冲突定义测试。
- `tests/collectors/test_subtask_redundancy.py`：新版 canvas 跨 Agent/message 扫描、精确与
  高相似冗余、约束差异、JSON 参数、非法事件、旧结构拒绝、敏感正文隔离及研究模式
  不可计算测试。
- `tests/collectors/test_intent_recognition.py`：新版 canvas 的 query、子 Agent、最终回答与
  MCP 能力提取，声明型手册外 MCP、JSON 参数、证据与调用去重、前置失败、非法结构、旧结构
  拒绝，以及研究最终回答选择测试。
- `tests/collectors/test_time_range_accuracy.py`：新版 canvas/研究时间候选、锚点优先级、调用
  去重、缺失调用字段、非法结构、旧结构拒绝、无调用零分入口、前置失败和不完整参数观察测试。
- `tests/collectors/test_evidence_quality.py`：显式证据工具范围、新版 canvas 跨 Agent/message
  关联、子 Agent 与孤立结果排除、重复和冲突生命周期、旧结构拒绝、结构化单元拆分、内存
  去重、响应级普通字符串证据、非文本标量拒绝及研究回归测试。
- `tests/collectors/test_statement_support.py`：新版 canvas/研究最终回答选择、空证据可计算、
  前置失败、缺终态、部分不可拆分响应、旧结构拒绝和成功产物无正文测试。
- `tests/collectors/test_tool_timeout_rate.py`：超时策略校验、新版 canvas 跨 Agent/message 关联、
  子 Agent 与孤立结果排除、调用去重、研究生命周期合并、显式超时、SLA 超限、缺失时间计
  超时、旧结构拒绝及错误隔离测试。
- `tests/evaluators/test_exact_duplicate_tool_calls.py`：完全重复率计算、零值、空调用、
  规范化错误和非法计数测试。
- `tests/evaluators/test_mcp_tool_call_inflation.py`：膨胀率、零膨胀、零分母、规范化失败和非法
  聚合计数测试。
- `tests/evaluators/test_required_parameter_completeness.py`：必填参数完整率、辅助指标排除、
  零分母、规范化错误和非法聚合计数测试。
- `tests/evaluators/test_tool_argument_schema_validity.py`：Schema 合法率、辅助指标排除、
  零分母、规范化错误和非法聚合计数测试。
- `tests/evaluators/test_parameter_value_accuracy.py`：Judge 三态字段聚合、辅助指标排除、
  部分失败隔离、非法 Judge 输出和不可计算边界测试。
- `tests/evaluators/test_openai_compatible_parameter_judge.py`：dotenv 配置、HTTPS 边界、请求
  结构、认证头、结构化响应解析和供应商异常脱敏测试。
- `tests/evaluators/test_routing.py`：共享 Judge 调用、Precision/Recall 集合计数、手册外 MCP
  自动 TP、零分母、Judge 非法输出安全重试、失败脱敏和安全映射写入测试。
- `tests/evaluators/test_answer_evidence_grounding.py`：锚定率计算、空证据零分加无中生有
  信号、无 span 不可计算、规范化错误透传与非法聚合计数隔离测试。
- `tests/evaluators/test_subtask_redundancy.py`：冗余率、单任务、零分母、规范化错误和非法
  聚合计数测试。
- `tests/evaluators/test_openai_compatible_routing_judge.py`：整体/子任务意图、动态 MCP Schema、
  snake_case 约束、安全校验反馈与历史映射的结构化请求测试。
- `tests/evaluators/test_intent_recognition.py`：固定权重汇总、缺失分项重归一化、硬规则归零、
  不可判定、Judge 失败、业务校验重试、严格输出白名单和敏感正文隔离测试。
- `tests/evaluators/test_openai_compatible_intent_judge.py`：意图证据请求结构、确定性采样、
  固定 JSON Schema、few-shot、校验反馈、响应解析和供应商异常脱敏测试。
- `tests/evaluators/test_time_range_accuracy.py`：两阶段稳定字段 ID、显式年份/最新性/预测补漏、
  检索窗口隔离、无调用零分、不可计算边界、业务校验重试、兼容空诊断省略、严格 Judge 输出
  校验和敏感异常隔离测试。
- `tests/evaluators/test_openai_compatible_time_range_judge.py`：query-only 提取请求、带角色时间
  证据、动态字段 ID Schema、检索窗口隔离及供应商异常脱敏测试。
- `tests/evaluators/test_statement_support.py`：支持率、正常零分、无可验证陈述、不可判定、
  自动判定覆盖率、默认模型缺失隔离、Judge 兼容性、证据引用和敏感正文隔离测试。
- `tests/evaluators/test_embedding_statement_support.py`：同义改写召回、数值/时间/否定/方向冲突、
  相似度灰区、低相关拒绝和模型 revision 缓存隔离测试。
- `tests/evaluators/test_deterministic_statement_support.py`：金额/百分比/时间匹配、数值冲突、
  文本事实、复杂语义保守判定、跨证据同比推导和空证据测试。
- `tests/evaluators/test_tool_timeout_rate.py`：双模式超时率、超时分类计数、缺失 SLA、非法
  规范化输入和错误隔离测试。
- `tests/collectors/test_tool_resource_usage.py` 与
  `tests/evaluators/test_tool_resource_usage.py`：双模式逐调用耗时、重复观测、缺失和非法边界、
  敏感正文隔离，以及工具 Token 明确不可计算测试。
- `tests/evaluators/test_openai_compatible_statement_support_judge.py`：最终回答与证据请求结构、
  历史可注入 Judge 的请求结构、JSON 响应解析和供应商异常脱敏测试。
- `tests/storage/test_intent_route_mappings.py`：映射表字段安全、记录去重、加载和相似签名
  检索测试。
- `docs/specs/metrics.md`：按开发顺序集中记录已确认并实现指标的定义和原始字段映射。
- `tests/`：测试包根目录；子目录结构与 `src/` 对应。
- `configs/evaluation.example.toml`：提交安全的评测配置样例。
