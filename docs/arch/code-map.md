# Code Map

评测流水线方向：`collectors -> evaluators -> reporters`，数据和产物访问由 `storage` 提供。

- `docs/START_HERE.md`：每次新会话的稳定阅读入口和不可破坏约束。
- `docs/PROJECT_STATUS.md`：当前里程碑、下一动作、已知缺口和验证基线。
- `docs/runbooks/`：日常作业手册目录（索引 + 打捞日志 / 全量跑宽表 / 生成报告三篇），
  记录对应脚本的输入、参数、断点续跑纪律、产出自查点与常见问题；执行作业前阅读。
- `初步入库评测指标.md`（根目录）：全部评测指标的权威口径来源——指标定义、双模式字段映射、
  异常边界、真实日志基线与埋点需求；指标确认后的实现细节进入集中规格 `docs/specs/metrics.md`。
- `.agents/skills/metric-report/SKILL.md`：指标报告技能说明——数据源、运行方式、报告六节
  结构、计数单位与高危/死亡/富集口径、列集与错误分类目录的同步维护点。
- `.agents/skills/metric-report/scripts/generate_report.py`：从
  `data/processed/metric_table_full.csv` 渲染自包含 HTML 报告（图表内嵌 base64 PNG，零回源）；
  标量/逐工具/JSON 标量列集分别由 `SCALAR_COLS` / `PER_TOOL_COLS` / `JSON_SCALAR` 定义，
  第 1 节列集演进与新增指标释义由 `EVOLUTION_*` / `ADDED_NOTES` 定义，模型错误分类由
  `LLM_ERROR_CLASS_CATALOG` 穷举（须与 `src/collectors/llm_error_count.py` 的
  `_ERROR_CLASS_RULES_V1` 同步）；3.3 节工具错误归因由
  `build_tool_error_attribution_stats` / `chart_tool_error_attribution` /
  `chart_tool_error_rate_and_matrix` 产出（归因分布 + 错误量、失败率 + 逐工具归因堆叠；
  逐工具归因矩阵仅用"单工具失败"日志推导并在图下标注覆盖率），全部可由宽表实时重算
  （曾有的 3.5.3「冻结抽样基线」静态 OSS 抽样段已按 2026-09-24 需求删除）；
  2.3 稳定性表只展示命中数/占比/其中未闭环三个原始计数，不展示派生的死亡富集倍数
  （该倍数仍作为内部排序/论证用于 4.2/5.1/3.4 的文字）；
  高危口径阈值 `HIGH_BE_THRESHOLD` / `HIGH_TOOL_ERROR_THRESHOLD` 须与
  `.tmp/rebuild_badcase_sheet.py` 的同名常量一致；
  必填参数 `--data-range YYYYMMDD-YYYYMMDD` 渲染进报告表头（宽表不存日期字段，
  无法从 CSV 推导，缺失即报错退出）；
  依赖 matplotlib/numpy，须用 anaconda python 运行。
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
  工具生命周期起止时间，逐调用隔离缺失、非法和冲突耗时；聊天排除 `agentName` 委派与
  `respond` Agent 返回（casefold，2026-09-21 修复 respond 泄漏，两类排除进
  `excluded_agent_delegation_count` / `excluded_agent_return_count` 诊断，被排除调用的
  结果不算孤立结果）；统计范围覆盖全部业务工具类型（MCP、文件类、skill、task 等），
  不按 MCP 手册过滤；明确标记两种模式均无工具执行自身 Token，且不保留参数或响应正文。
- `src/collectors/tool_call_latency.py`：复用逐调用资源视图，按精确工具名聚合有效、缺失和错误
  耗时，输出逐工具总耗时及覆盖计数，不读取参数或响应正文。
- `src/collectors/max_tool_latency_share.py`：复用逐工具耗时统计（respond 排除已上移至
  `tool_resource_usage`，组合层名称过滤仅作安全网、计数继承上游），执行全量业务工具
  耗时覆盖检查，生成最大时延占比的安全分子与分母。
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
- `src/collectors/citation_traceability.py`：引用可溯率规范化。聊天模式从
  `canvas.answer.answer` 抽取唯一 `[n]` 引用编号（剥码、markdown 链接与复合形态诊断），
  在成功检索轮 doc_id 位置序列（1..N）内解析；研究模式正文通道与锚定率一致，注册表为
  completed 工具输出中 `id: [n]` 与行首编号清单并集（write/edit 交付产物排除）；
  注册表为空但有引用时输出 fabrication_risk_signal，编号语义混杂由
  `numbering_scheme_mixed_suspected` 诊断标记；结果仅保留聚合计数、通道与版本。
- `src/collectors/turn_completion.py`：回合完成率规范化。聊天模式按代理口径判定
  `canvas.answer.answer` 非空且不命中版本化固定服务失败模板（目录 v1，剥空白全文
  精确匹配）；研究模式要求入口层已把续跑段合并为单逻辑回合，以 `turn.end` 存在为
  闭环锚点，同 ID 多 `turn.start` 不破坏判定，耗时缺失/非法、重复与 ID 不匹配的
  `turn.end` 只进诊断；结果仅保留 0/1 标记与安全计数，不保留正文或模板原文。
- `src/collectors/continuation_restart.py`：续跑重启次数规范化（稳定性维度 7.3）。聊天
  模式无续跑机制恒返回 `no_continuation_mechanism`，不做结构校验；研究模式消费入口层
  已合并的单逻辑回合事件序列，以去重后 `turn.start` 数减一计量重启，并复用 7.1
  `normalize_research_turn_completion` 附带 `turn_completed` 闭环标记（双因素一起输出，
  闭环边界冲突连带隔离整条）——去重键为解析后毫秒时间戳（同 ID 不能作键），ts
  缺失/非法保守计为独立事件并进诊断，不同 ID 的 `turn.start` 属切分违约按
  `conflicting_turn_boundary` 隔离；结果仅保留计数、闭环标记与标识。
- `src/collectors/llm_error_count.py`：模型层错误数规范化（稳定性维度 7.4）。聊天模式
  无模型层错误事件通道恒返回 `no_model_error_events`（通道不存在不等于无错误,禁止
  记 0）；研究模式统计单回合内 `llm.error` 原始事件数（含重复投影），按版本化目录
  `llm_error_class_v1` 对 `error.value.error` 做大小写不敏感子串分类——对象形态只取
  name+message（stack/url 显式排除，不参与匹配也不落盘）；`message.updated.info.error`
  与 `llm.response.result.finishReason=error` 属跨通道投影只进诊断不计数；出错模型
  归因取 `message.updated` 路由字段（顶层 `modelID` 与 `info.model` 字典双形态），
  1.3.0 起仅取 model 不再取 provider（模型名与 provider 一一对应）；仅单模型回合
  全量归因，多模型/无模型进 `model_unattributed_error_count` 诊断不猜测。模型调用
  计数（1.3.0 新增）：以**唯一 assistant 消息**（`info.role` 为 assistant 且
  `info.id` 去重，与路由字段同通道）为一次模型调用，无路由调用仍计入总数只进
  `model_call_unattributed_count`、id 缺失保守计数、同 id 冲突路由保留首次观测；
  结果仅保留计数、分类标签、模型标识与十一个安全诊断。
- `src/collectors/tool_error_rate.py`：工具错误率与归因规范化（稳定性维度 7.7）。聊天
  模式按 `traceId + toolId` 关联请求与结果做载荷代理分类（`payload_proxy_v1`）：嵌套
  `tool_result` 信封解包一层、JSON 字符串解码、`code:1`+"校验说明"降级分流优先于失败
  信号、入口级 `success:false` 兜底（"Tool not found" 类无载荷失败）、结构化列表载荷为
  成功终态、散文载荷进 `undeterminable_payload_count` 不估算；硬边界为空串/缺失
  `error_code` 恒成功（深层 `debug_trace` 节点码不触碰）。研究模式按 `callID` 合并
  生命周期取显式终态（`explicit_status_v1`）：error 进分子、completed 成功、
  pending/running 进 `terminal_missing_count`；归因目录 v1 为紧凑化子串 + 窄正则的
  有序确定性匹配，未命中记 unclassified；错误正文仅内存流转，结果只留计数与版本。
- `src/collectors/error_recovery.py`：错误自恢复率规范化（稳定性维度 7.5，5.1 的
  研究侧降级实现）。组合四个既有规范化函数作为单一事实来源：7.4 llm.error 计数、
  7.7 工具 error 终态、7.1 turn.end 闭环、6.1 正文通道（write > edit > 最后
  assistant 文本）；聊天无错误事件通道恒返回 `no_error_events_observable`（禁止
  载荷代理），仅做最小结构校验。唯一新增逻辑为 loop 级重试配对：`llm.error(loop=N)`
  之后严格出现 `llm.response(loop=N)` 且 `result.finishReason` 为非空文本且
  ≠ error 才计入 `retry_recovered_error_count`，逐条原始 error 事件独立配对；
  loop 缺失与未配对只进诊断。错误传播优先级：输入非法 > 组件结构错误 >
  缺回合边界 > 无错误事件；结果仅留 0/1 标记、通道标签与安全计数。
- `src/collectors/compaction_event_count.py`：上下文压缩事件数规范化（稳定性维度
  7.6）。聊天模式无压缩事件通道恒返回 `no_compaction_events`（通道不存在不等于
  无压缩，禁止记 0；结构非法才是 ERROR）；研究模式信任单回合契约（与 7.4 一致，
  不重复校验边界），统计 `message.part.updated` 中 `part.type == "compaction"` 且
  `auto == true` 的唯一 part 数——按 `part.id` 去重（重复投影进诊断），auto=false
  手动压缩与 auto 缺失/非布尔均不进主值、分流诊断不猜测，part.id 缺失保守计为
  独立事件（与 7.3 ts 缺失同方向），同 id 冲突 auto 按
  `conflicting_compaction_definition` 隔离；`message.updated` 的
  `info.mode == "compaction"` 摘要属跨通道投影只进诊断；结果仅留计数与标识。
- `src/collectors/api_retry.py`：框架 API 重试证据规范化（稳定性维度 7.8）。
  计量工具内部传输/业务层的重复请求（重试上界），不是埋点 D 的模型级重试，两者禁止
  互相代理；聊天 tool_result 无 API 明细通道恒返回 `no_tool_debug_events`
  （通道不存在不等于无重试，禁止记 0）。研究模式重试判定为 **fingerprint_dup_v2**
  （2026-09-23 修订）：同一 `(callID, endpoint+入参指纹)` 重复出现计为重试
  （组内次数-1），因实勘发现 requestId 尾序号是"调用内第 N 个请求"的序号而非
  重试计数（83.8% 相邻入参不同）；成功语义修正为"response 为对象但缺 code 键
  = 成功"（成功响应直接返回业务载荷），仅非对象响应进 `undeterminable_api_count`；
  元数据另带 `api_request_depth_max`（调用内最大请求序号，工作流深度信号）；
  并产出框架层归因 `api_retry_attribution`（同 callID 内同指纹重复的 by_tool /
  by_endpoint / retry_after_success，2026-09-24 需求方确认），供宽表独立列定位；
  覆盖率 = debug callID ∩ 工具 callID ÷ 全部唯一工具 callID（孤立 debug、缺
  callID 的 debug 事件与工具 part 分流诊断）；有 debug 事件但 apiCalls 为空返回
  `no_observable_api_calls`；错误码分布随结果输出；endpoint 仅以聚合 Top-K 计数
  落归因列，原始逐次 endpoint 与请求/响应正文不落结果。
- `src/collectors/high_risk_turn_profile.py`：高危回合画像特征规范化（稳定性
  维度 7.11，规则版本 `high_risk_profile_v2` = 2026-09-21 确认的双条件口径，
  v1 四条件公式实测 0 命中已废弃）。组合式单一事实来源（7.5 范式）：7.8
  `normalize_*_api_retry` 提供重试率（硬依赖，不可计算即透传——step 数再高
  也不可判定）、7.6 压缩计数与 7.1 闭环标记为特征清单诊断项、step 数为
  step-start 按 messageID 去重计数（与 7.10 同口径，缺 messageID 保守计数）；
  闭环是特征不是条件（missing_turn_boundary 时 turn_completed 为 None 不阻断，
  边界/压缩证据冲突透传隔离）；空结果数特征项待 7.9 立项后复用其 normalizer
  补入（2026-09-21 需求方确认本批省略）；聊天依赖通道不存在透传
  `no_tool_debug_events`。
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
  返回不可计算，非法、负时序或冲突调用只隔离受影响工具项，版本为 `1.1.0`
  （2026-09-21 聊天 respond 泄漏修复，诊断新增两个排除计数键）。
- `src/evaluators/max_tool_latency_share.py`：计算累计耗时最高的业务工具占全部业务工具累计耗时
  的比例，支持并列最大工具并严格拒绝不完整覆盖和零分母，版本为 `1.1.0`
  （2026-09-21 respond 排除上移，指标值不变、诊断契约扩展）。
- `src/evaluators/exact_duplicate_tool_calls.py`：使用安全聚合计数计算完全重复调用率，并输出
  不可比较调用、重复请求、重复结果及孤立结果诊断。
- `src/evaluators/mcp_tool_call_inflation.py`：校验 MCP 调用与请求签名计数，计算同任务内相同
  MCP 且相同入参的后续调用占比；轮询调用排除与空入参诊断随 1.2.0 引入，仅剩轮询调用时
  返回 `no_non_polling_mcp_calls`。
- `src/evaluators/citation_traceability.py`：计算最终回答 `[n]` 引用编号在来源注册表内的
  可解析比例，输出超界、无来源清单、编号混排等诊断；evaluator 版本为 `1.0.0`。
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
- `src/evaluators/turn_completion.py`：校验规范化闭环证据并输出单 Trace 0/1 主值，
  `completion_semantics`（turn_end_event / answer_presence_proxy）随元数据声明；
  缺回合边界不可计算，结构冲突为错误，error 与标记互斥；evaluator 版本为 `1.0.0`。
- `src/evaluators/continuation_restart.py`：校验规范化续跑证据并输出双因素单元素
  JSON 数组（统一 `[]` 包围，2026-09-21 需求方确认）
  `[{"continuation_restart_count":N,"turn_completed":B}]`（零重启是合法值），
  `restart_semantics`（turn_start_event / no_continuation_mechanism）与计算路径的
  `completion_semantics = turn_end_event` 随元数据声明；校验两因素类型与互斥、
  restart = turn_start − 1 自洽关系、聊天无 error 违约；报告级比率由单元格两因素
  直接派生；版本为 `1.1.0`。
- `src/evaluators/llm_error_count.py`：校验规范化模型层错误证据并输出单元素 JSON
  数组主值（统一 `[]` 包围：`[{"llm_error_count":N,"error_classes":[...],"error_models":[...],
  "total_call_count":M,"model_calls":[...]}]`，分类按 count 降序、class 升序，
  模型归因与模型调用按 count 降序、model 升序稳定排序，零错误为合法零值 JSON、
  零错误不等于零调用）；`error_event_semantics`（llm_error_event /
  no_model_error_channel）、`model_attribution`（single_route_turn / unattributed）
  与分类目录版本随元数据声明；校验分类标签白名单、分类守恒（分类之和 = 主值）、
  归因守恒（已归因 + 未归因 = 主值，非空归因必须单模型全量）、调用守恒（已归因
  调用 + 未归因 = 总调用数，分项之和不得超过总数）、跨段交叉校验（能归因到错误的
  模型必须出现在调用分项内）与 error/计数互斥，违约隔离为 `invalid_normalized_trace`；
  版本为 `1.3.0`（1.3.0 增补 `total_call_count` / `model_calls` 并去掉 `error_models`
  内的 provider）。
- `src/evaluators/tool_error_rate.py`：校验规范化终态与归因证据并输出单元素 JSON
  数组主值（统一 `[]` 包围，固定键序：比率 + error/terminal 调用数 + 四类归因计数 + 降级计数，零错误为
  合法零值 JSON）；`terminal_semantics`（payload_proxy_v1 / explicit_status_v1）与
  目录/信号规则版本随元数据声明并做模式一致性校验（聊天必须携带失败信号版本、研究
  必须为 None）；校验 call_id 唯一、terminal_state 与 attribution 互斥关系、归因/降级
  计数与逐项守恒，违约隔离为 `invalid_normalized_trace`；`no_tool_calls` /
  `no_terminal_tool_calls` 为 NOT_COMPUTABLE；元数据 `error_calls` 保留
  (tool_name, attribution) 明细供 reporter 批次聚合矩阵；版本为 `1.1.0`。
- `src/evaluators/error_recovery.py`：校验规范化恢复证据并输出单元素 JSON 数组主值
  （统一 `[]` 包围，固定键序：error_recovered + had_error + 两层错误计数 + loop 级重试配对计数）；
  恢复合成规则 = 闭环 ∧ 非空正文，仅指技术完成不判质量；`recovery_semantics =
  degraded_v1` 随元数据声明并强校验；had_error=0 / 聊天 / 缺回合边界为
  NOT_COMPUTABLE（no_error_events / no_error_events_observable /
  missing_turn_boundary），had_error 与两层错误计数的守恒关系及 0/1 标志值违约
  隔离为 `invalid_normalized_trace`；报告级 Σ 分子 ÷ Σ 分母由聚合层解析 JSON
  单元格派生；版本为 `1.1.0`。
- `src/evaluators/compaction_event_count.py`：校验规范化压缩证据并输出单 Trace
  非负整数主值（零压缩是合法值），`compaction_semantics`（compaction_part_event /
  no_compaction_channel）随元数据声明；error 与主值互斥，聊天无 error 属契约违约；
  报告级 compaction_turn_ratio 与平均压缩次数由聚合层派生；版本为 `1.0.0`。
- `src/evaluators/api_retry_rate.py`：校验规范化重试证据并输出单元素 JSON 数组
  主值（统一 `[]` 包围，固定键序：比率 + 重试/调用计数 + 业务错误数 + 不可判定数 + 覆盖率，
  零重试是合法值，覆盖率必须随值输出且无工具 callID 时为 null）；
  `retry_semantics`（fingerprint_dup_v2 / no_tool_debug_channel）随元数据
  声明（2026-09-23 由 request_id_suffix_v1 修订，弃用尾序号口径）；元数据另带
  `api_request_depth_max`；守恒校验（重试≤调用、错误+不可判定≤调用、错误码分布之和=业务错误数
  且按码升序唯一、覆盖率与工具 callID 数互证、深度≥1）违约隔离为
  `invalid_normalized_trace`；`no_tool_debug_events` / `no_observable_api_calls`
  为 NOT_COMPUTABLE；错误码分布留元数据供 reporter 批次按码聚合，endpoint
  矩阵留 reporter 批次；版本为 `2.0.0`（2026-09-23 fingerprint_dup_v2 修订）。
- `src/evaluators/high_risk_turn_profile.py`：校验规范化画像特征并输出双条件
  0/1 标记的单字段紧凑 JSON（固定键序：high_risk_turn + step 数 + 重试率与
  分子分母 + 压缩次数 + 闭环标记，闭环可为 null）；冻结阈值 steps > 30 ∧
  rate > 0.4（严格不等式）与规则版本 `high_risk_profile_v2` 随元数据声明，
  版本不匹配隔离；rate 与 retry/call 商自洽校验；依赖 7.8 不可计算透传
  NOT_COMPUTABLE（不代表无风险）；报告级组合规则不进宽表单值列，高危回合率
  由聚合层派生；空结果数特征项待 7.9 补入时须升版；版本为 `1.0.0`。
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
  `tool_calls_count`、`per_tool_call_tokens`、`total_token_consumption`、
  `max_tool_token_share`、`output_token_ratio`、`cache_input_token_ratio`、
  `per_tool_call_latency_ms`、`end_to_end_latency_ms`、`max_tool_latency_share`、
  `answer_evidence_grounding_rate`、`turn_completion_rate`、
  `continuation_restart_count`、`llm_error_count`（单元格为整数主值 + 分类数组的
  单元素 JSON 数组，统一 `[]` 包围）和 `tool_error_rate`（单元格为比率 + 分子分母 +
  归因计数的单元素 JSON 数组；聊天载荷代理与研究显式终态两种语义禁止混比）、
  `tool_error_attribution`（2026-09-24 注册：逐次工具失败的 by_tool / by_attribution
  单元素 JSON 数组；复用 tool_error_rate 的规范化与评测器，从 metadata 读
  `error_calls`；**不做 Top-K 截断**，与 `api_retry_attribution` 的 Top5 口径刻意不同；
  零错误写空、空 ≠ 零；与 backfill 脚本共用 `build_tool_error_attribution_cell`
  单一实现。此前 `error_calls` 只存在于评测器 metadata，宽表只提取
  `partial_issues` / `partial_issue_reasons`，逐次失败的工具名被静默丢弃，本列补齐该缺口）、
  `error_recovery_rate`（单元格为 error_recovered 主值 + had_error + 两层错误
  计数 + loop 级重试配对计数的单元素 JSON 数组；聊天恒空、研究 had_error=0 写空
  不进分母）、`compaction_event_count`（单元格为非负整数，零压缩合法；聊天无
  压缩事件通道恒空）和 `api_retry_rate`（单元格为比率 + 分子分母 + 业务错误 +
  不可判定 + 覆盖率的单元素 JSON 数组；聊天无 API 明细通道恒空，研究无
  tool.debug 事件写空不代表无重试；`high_risk_turn_profile` 为报告级组合规则
  不注册列）和 `api_retry_attribution`（2026-09-24 注册：框架层归因 by_tool /
  by_endpoint Top5 + retry_after_success 的单元素 JSON 数组；复用 api_retry 的
  规范化与评测器，从 metadata 读归因；不可计算留空；与 backfill 脚本共用
  `build_attribution_cell` 单一实现）
  （`per_tool_repeat_call_count`
  与 `mcp_tool_call_inflation_rate` 已于 2026-09-20 按需求退出注册，
  `citation_traceability_rate` 已于 2026-09-21 按需求退出注册，评测器与测试保留；
  六个 JSON 单元格列的 `[]` 包围形态于 2026-09-21 经需求方确认，对应评测器同批升版，
  2026-09-24 新增的 `tool_error_attribution` 沿用同形态）；逐工具
  时延以仅含工具名和值的紧凑 JSON 数组写入单个 CSV 单元格，端到端时延直接写入数值；
  锚定率列的研究模式要求 payload 为单回合事件序列（多回合文件先按 sessionID 分组、
  按 turn.start 切分后送入，否则跨回合证据互相污染）；
  指标列由无参 `build_metric_columns()` 静态构建，宽表入口不依赖 `mcp_tool_manual.json`；入口按
  模式提取身份字段并在 Trace ID 缺失时生成稳定匿名回退标识，后续指标只需增加适配函数和
  注册项。`_counter_items` 是两列归因排序的单一实现，`_topk` / `_topk_all` 只是
  截断与不截断两种调用方式。
- `scripts/stream_compute_metrics.py`：研究模式全量指标的 OSS 流式计算入口（日志不落盘）；
  复用 `summarize_metric_table` 的 `METRIC_COLUMNS` 与 `build_research_record`，结果 CSV
  表头随批量入口自动同步；断点续跑只按 `log_id` 跳过、不校验表头，列集变更后旧结果 CSV
  必须删除重跑，禁止新旧列集混拼。
- `scripts/backfill_api_retry_attribution.py`：把框架层 API 重试归因回填进宽表独立列
  `api_retry_attribution`（2026-09-24 需求方确认）。只补这一列、只处理
  `api_retry_rate > 0` 的行（零重试行留空），其余列原样保留；逐行回 OSS 取流只调用
  `normalize_research_api_retry` 读归因、不重算其它指标；归因仅框架层（同 callID 内
  同指纹重复），含 by_tool / by_endpoint（Top5）与 retry_after_success；断点续跑按
  新列是否非空跳过；原子替换（temp + rename）防半写。
- `scripts/backfill_tool_error_attribution.py`：把工具错误归因（失败工具名 + 归因分类）
  回填进宽表独立列 `tool_error_attribution`（2026-09-24 需求方确认）。只补这一列、
  只处理 `tool_error_rate` 单元格 `error_call_count > 0` 的行（零错误行留空），其余列
  原样保留；逐行回 OSS 取流只调用 `normalize_research_tool_error_rate` +
  `ToolErrorRateEvaluator` 读 metadata 的 `error_calls`、不重算其它指标；只统计 error
  终态，degraded（code:1 自检降级）不带工具名入列；by_tool / by_attribution 不截断；
  断点续跑按新列是否非空跳过；原子替换（temp + rename）防半写。
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
  输出、稳定匿名回退标识，以及错误归因列的定位行为（双模式把失败工具名写进独立列、
  零错误行留空、归因计数与值列 `error_call_count` 守恒、错误正文不进列）测试。
- `tests/scripts/test_backfill_tool_error_attribution.py`：错误归因单元格的纯函数行为
  （空/零错误返回空串、单元素 JSON 数组包围、by_tool 与 by_attribution 的计数与
  次数降序 + 名称升序稳定排序、不做 Top-K 截断、缺工具名/归因时占位且计数守恒）测试，
  不触 OSS。
- `tests/scripts/test_backfill_api_retry_attribution.py`：API 重试归因回填的纯函数行为
  （只处理重试行、零重试留空、单元格构造与 `api_retry` 规范化共用实现、原子替换）测试，
  不触 OSS。
- `tests/scripts/test_stream_compute_metrics.py`：流式计算入口的清单解析与去重、带 query 版
  选择、逐条失败隔离为诊断、断点续跑按 `log_id` 跳过、表头与 `METRIC_COLUMNS` 同步测试，
  使用内存假 bucket 隔离真实 OSS。
- `tests/collectors/test_end_to_end_latency.py`、`tests/evaluators/test_end_to_end_latency.py`、
  `tests/collectors/test_model_step_latency.py`、`tests/evaluators/test_model_step_latency.py`、
  `tests/collectors/test_token_consumption.py`、`tests/evaluators/test_token_consumption.py`：
  时间维度双模式边界提取、步骤配对与 Token 汇总的正常、缺失、冲突和非法输入隔离测试。
- `tests/collectors/test_citation_traceability.py` 与
  `tests/evaluators/test_citation_traceability.py`：双模式引用编号提取、来源注册表解析、
  超界与编号混排诊断、无引用不可计算与聚合计数校验测试（该列已退出宽表注册，评测器保留）。
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
- `tests/collectors/test_turn_completion.py` 与 `tests/evaluators/test_turn_completion.py`：
  双模式闭环判定、聊天固定失败模板与空白回答 0 分、结构非法错误、续跑多 turn.start、
  重复与冲突 turn.end、耗时诊断不阻断闭环、ID 不匹配隔离、非法规范化输入拒绝、
  敏感正文隔离与输入不变测试。
- `tests/collectors/test_continuation_restart.py` 与
  `tests/evaluators/test_continuation_restart.py`：聊天固定不可计算、研究单段零重启、
  续跑多 turn.start 计数、同 ts 重复投影去重、ts 缺失/非法保守计数、跨 ID 冲突隔离、
  无 turn.start 不可计算、计数自洽关系校验、error 与主值互斥与敏感正文隔离测试。
- `tests/collectors/test_llm_error_count.py` 与
  `tests/evaluators/test_llm_error_count.py`：五类 v1 目录匹配、字符串/对象双形态、
  stack/url 不参与匹配、unclassified 兜底、重复投影诊断、loop 缺失、跨通道投影不计数、
  模型归因（单模型全量归因、字典形态、双形态去重、多模型/无模型不猜测、provider 不
  拆分同模型）、模型调用计数（同 id 投影去重、非 assistant 不计且带路由时诊断、
  id 缺失保守计数、同 id 冲突路由保留首次并诊断、无路由调用只进诊断不缩分母、
  错误模型必在调用分项内）、聊天固定不可计算、结构非法隔离、JSON 单元格形态与稳定
  排序、分类/归因/调用三重守恒与白名单校验、错误正文不落盘测试。
- `tests/collectors/test_tool_error_rate.py` 与
  `tests/evaluators/test_tool_error_rate.py`：双模式终态分类、归因目录 v1 命中与
  unclassified 兜底、error_code 空串=成功硬边界、code:1 降级分流、code:2 异步回执
  成功、嵌套 tool_result 信封解包、JSON 字符串载荷解码、散文载荷不可判定、列表
  载荷成功终态、入口级 success:false 兜底、"Tool not found" 窄正则、callID 生命
  周期合并、error 优先与冲突诊断、子 Agent/respond/孤立排除、紧凑 JSON 值列键序、
  合法零值、语义标签与版本声明校验、计数守恒、敏感正文隔离与输入不变测试。
- `tests/collectors/test_error_recovery.py` 与
  `tests/evaluators/test_error_recovery.py`：聊天固定不可计算与最小结构校验、研究
  四组件组合、had_error 两层来源、loop 级重试配对（同 loop 多错误独立配对、严格
  后继、finishReason=error 不配对、loop 缺失只诊断）、未闭环/无正文恢复为 0、
  缺回合边界、组件结构错误透传、无错误事件不可计算、紧凑 JSON 键序、降级语义
  标签强校验、had_error 守恒校验、非法标志拒绝、敏感正文隔离与输入不变测试。
- `tests/collectors/test_compaction_event_count.py` 与
  `tests/evaluators/test_compaction_event_count.py`：聊天固定不可计算与结构非法
  区分、研究零压缩合法值、auto=true 唯一 part 计数、part.id 重复投影去重、手动
  压缩分流、auto 缺失/非布尔不猜测、part.id 缺失保守计数、同 id 冲突隔离、
  message.updated 跨通道投影仅诊断、无 turn.start 回退 trace_id、非法规范化输入
  拒绝、敏感正文隔离与输入不变测试。
- `tests/collectors/test_api_retry.py` 与
  `tests/evaluators/test_api_retry_rate.py`：聊天固定不可计算与结构非法区分、
  无 tool.debug 与空 apiCalls 两级不可计算、requestId 尾序号重试判定、
  (callID, requestId) 重复投影去重、畸形 requestId 保守计数、业务错误字面口径
  （成功集/success=true 覆盖/非对象与缺 code 不可判定/缺 response 键）、错误码
  分布排序、覆盖率交集口径与孤立 debug 诊断、缺 callID 的 debug 事件与工具
  part 分流、trace_id 回退、JSON 单元格键序、零重试合法值、覆盖率 null 形态、
  守恒违约隔离、语义标签声明、敏感正文隔离与输入不变测试。
- `tests/collectors/test_high_risk_turn_profile.py` 与
  `tests/evaluators/test_high_risk_turn_profile.py`：聊天依赖透传不可计算、
  研究无 tool.debug 时 step 数再高也不可计算、四组件组合、step-start 按
  messageID 去重与缺失保守计数、闭环/压缩证据冲突透传隔离、缺回合边界时
  闭环特征 null 不阻断主值、未闭环回合照常输出、双条件严格不等式阈值边界
  （steps=30 与 rate=0.4 均不命中）、rate 与分子分母自洽校验、profile_version
  强校验、error 与特征互斥、非法值隔离与敏感正文隔离测试。
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
