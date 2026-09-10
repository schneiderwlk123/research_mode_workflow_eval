# Project Status

更新时间：2026-09-09

## 当前里程碑

评测框架基础骨架、完全重复调用率、MCP 工具调用膨胀率、必填参数完整率、
Schema 合法率、
参数值准确率核心、路由 Precision、路由 Recall、子任务冗余率和意图识别准确率已经实现。
时间范围提取准确率和陈述支持率已验收。工具超时率已实现。

已完成：

- Python 3.11+ 项目和 pytest、Ruff、mypy 工具链配置。
- `BaseEvaluator`、`EvaluationResult`、`EvaluationStatus` 公共契约。
- 公共契约及十五个指标行为测试，共 436 项；最近一次验证全部通过。
- `collectors`、`evaluators`、`reporters`、`storage` 包入口。
- 代码地图和 15 项逐指标开发顺序；时延、执行效率及 Token 消耗类指标统一置于最后阶段。
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
  原子陈述结果契约及确定性规则评测器；新版 canvas 从
  `meta.query/answer.answer` 提取聊天输入，旧版顶层字段不再支持；`statement_support_rate`
  版本为 `3.0.0`。默认路径不调用 LLM 或网络，使用规则分句、字符 bigram、指标别名、时间、
  数值与单位归一化、冲突检测和同比白名单推导；复杂语义保留为 `not_assessable`，并
  输出自动判定覆盖率，query、回答、原子陈述和证据正文不落盘。
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
- 集中式指标规格 `docs/specs/metrics.md`，当前记录十六个已实现指标。
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
- 进行中的具体指标：逐工具调用耗时与工具 Token 可计算状态已按确认口径实现，等待指标验收。
- 下一项指标：当前开发顺序中的指标已全部处理。
- 下一动作：验收逐工具资源字段，再决定是否接入统一 Trace 输出与真实日志重算。

## 已知缺口

- 新版聊天 JSON 数组与研究模式事件流的结构已形成架构文档；完全重复
  调用率、MCP 工具调用膨胀率、必填参数完整率、Schema 合法率、参数值准确率、路由
  Precision/Recall、子任务冗余率、意图识别准确率、时间范围提取准确率和陈述
  支持率 collectors 已支持新版 canvas，其余指标 collectors 仍主要按旧版字段读取，完整统一
  Trace 模型尚未实现。
- collectors、聚合器、报告器、配置加载器和可运行 CLI 尚未实现。
- Golden Benchmark 校准集和交付物读取能力尚未接入。
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
陈述支持率: 临时 17 工具目录下，聊天前 30 条微平均约 0.07646、自动判定覆盖率约
  0.93606；研究日志约 0.09353、覆盖率约 0.96403；生产证据工具目录仍待管理员确认
MCP 工具调用膨胀率: 33/2911 ≈ 0.01134；2878 个不同请求签名，1355 次手册外调用被排除
超时率: 3443/4266 次调用有工具执行耗时，823 次缺失；缺生产 SLA，返回 missing_tool_sla
端到端时延聊天模式: 336/336 条可计算；最小 0 ms、中位数 84500 ms、平均
  99339.286 ms、最大 1181000 ms；秒级边界推导，0 ms 表示起止落在同一秒
端到端时延研究模式: 936405 ms，优先 turn.end.durationMs；时间戳差值 936403 ms，
  boundary_difference_ms = -2
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
pytest: 436 passed
ruff check: passed
ruff format --check: passed
mypy src tests: passed
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
