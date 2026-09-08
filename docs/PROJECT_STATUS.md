# Project Status

更新时间：2026-09-08

## 当前里程碑

评测框架基础骨架、调用事件完整率、完全重复调用率、MCP 工具调用膨胀率、必填参数完整率、
Schema 合法率、
参数值准确率核心、路由 Precision、路由 Recall、子任务冗余率和意图识别准确率已经实现。
时间范围提取准确率、证据查准率和陈述支持率已验收。工具超时率已实现。

已完成：

- Python 3.11+ 项目和 pytest、Ruff、mypy 工具链配置。
- `BaseEvaluator`、`EvaluationResult`、`EvaluationStatus` 公共契约。
- 公共契约及十四个指标行为测试，共 376 项；最近一次验证全部通过。
- `collectors`、`evaluators`、`reporters`、`storage` 包入口。
- 代码地图和 19 项逐指标开发顺序；时延、执行效率及 Token 消耗类指标统一置于最后阶段。
- MCP 工具手册已复制到项目，当前文件名为 `mcp_tool_manual.json`。
- 新版 canvas 和研究工具调用事件的最小规范化切片，不保留工具参数或响应正文；新版聊天按
  `traceId + toolId` 跨消息关联请求与结果，并隔离孤立 Agent 返回；旧版聊天解析已删除。
- `tool_call_event_completeness_rate` 评测器，版本 `1.1.0`。
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
- 可注入、版本化 Judge 契约与 `parameter_value_accuracy_rate` 评测器，版本 `1.3.0`；
  支持逐字段三态判定和部分失败隔离，最终结果不计算或输出辅助率指标。
- OpenAI-compatible Judge 共享运行时与常用 dotenv 配置加载：远程端点强制 HTTPS，优先
  使用严格 JSON Schema，供应商拒绝时回退 JSON 对象模式；默认 512 KiB 请求上限、2 次
  瞬态重试、输出截断检测及稳定脱敏错误分类已覆盖六类 Judge 指标。
- LLM 结合整体意图、全部可见子任务和 MCP Schema 生成标准能力集合，并通过共享
  TP/FP/FN 同时计算 `routing_precision` 与 `routing_recall`；两个指标的新版 canvas
  适配版本均为 `1.2.0`。
- 去实体化的意图签名、子任务类型与能力集合 JSONL 映射表，支持去重和 Jaccard 相似检索；
  原始意图、实体、参数和响应不得持久化。
- 聊天子任务的确定性 NFKC、约束词元和字符 bigram 冗余规则；不调用 LLM，任务正文和
  中间指纹不进入结果；新版 canvas 跨 Agent/message 扫描取代旧聊天队列解析，
  `subtask_redundancy_rate` 版本为 `1.1.0`。
- 聊天与研究模式的意图证据规范化、严格三态 Judge 契约和 OpenAI-compatible 适配器；
  新版 canvas 按调用 ID 去重并提取子 Agent、MCP 能力和最终回答证据，旧聊天解析已删除；
  `intent_recognition_accuracy` 版本为 `1.2.0`，原始 query、规划、回答和 Judge 响应不落盘。
- 聊天与研究模式的时间需求、工具参数和可复现时间锚点规范化，严格字段计数 Judge 契约
  及 OpenAI-compatible 适配器；新版 canvas 的 `canvas_meta` 锚点已贯通 collector 与
  evaluator，`time_range_extraction_accuracy` 版本为 `1.2.0`，
  query、日期原文、参数值和 Judge 响应不落盘。
- 显式 MCP 证据工具目录、聊天/研究成功响应关联、来源原生结构拆分和内存证据去重；
  新版 canvas 按 `traceId + toolId` 跨 Agent/message 关联请求和结果，隔离子 Agent、孤立结果
  及冲突生命周期，旧版聊天解析已删除；`evidence_precision_rate` 版本为 `1.2.0`，Judge
  默认每 40 个证据单元分批且任一批失败不返回部分结果；不可拆分文本不估算，query、证据
  正文、摘要和 Judge 原始响应不落盘；显式证据工具范围生成稳定 `evidence_scope_version`。
- 聊天最终回答和研究模式 `turn.end` 前最后一个 assistant 文本提取、空证据零分入口、严格
  原子陈述 Judge 契约及 OpenAI-compatible 适配器；新版 canvas 从
  `meta.query/answer.answer` 提取聊天输入，旧版顶层字段不再支持；`statement_support_rate`
  版本为 `1.2.0`；为保留跨证据组合支持语义不做证据分批，超过 512 KiB 时快速返回
  `judge_input_too_large`，query、回答、原子陈述、证据正文和 Judge 原始响应不落盘。
- 版本化工具 SLA 与别名目录，以及新版 canvas/研究调用的显式超时、SLA 超限和缺失时间
  分类；聊天模式按 `traceId + toolId` 跨 Agent/message 关联请求与结果，排除子 Agent、孤立
  结果和旧版 `tool_call_queue`。`tool_timeout_rate` 版本为 `1.1.0`；工具 SLA 缺失时不可计算，
  工具已配置 SLA 但调用缺少完整时间时，按确认口径计为超时并单独诊断。
- 集中式指标规格 `docs/specs/metrics.md`，当前记录十四个已实现指标。
- 已按新版聊天 JSON 数组结构重算十四个指标的字段映射、确定性基线和条件可计算范围；
  明确区分普通工具请求、子 Agent 调用与结果侧 Agent 返回，未把 Judge 候选覆盖率当成语义得分。

## 当前状态

- 已暂时跳过指标：调用成功率、异常率、重试成功率、重试率、平均重试尝试次数和
  实体映射准确率；相关实现、测试和专用规格已删除。
- 已删除指标：证据查全率、证据时效合格率、证据可追溯率、无依据陈述率、数值忠实度、
  最终数值与召回证据一致率、硬编码率、循环引用率、需求覆盖率、严格任务完成率、信息保留率、
  信息坍缩率和中间结果与最终结果一致率、公式计算准确率、公式单元格覆盖率、首次工具决策
  时延、单次工具调用时延、工具调用时延 P50、工具调用时延 P95、工具调用时延 P99；这些指标
  不再保留在开发顺序和原始指标范围中。其中证据查全率依赖当前项目尚未具备的高标准 Golden
  Benchmark。
- 已取消指标：每个成功会话的平均成本、模型工具循环深度；已从开发顺序和原始指标范围删除。
  模型工具循环深度的取消原因为聊天模式缺少模型步骤和轮次边界，无法可靠计算。
- 最近完成并验收的新版聊天适配指标：陈述支持率。
- 进行中的具体指标：工具超时率已按新版日志结构完成重组和验证，等待指标验收。
- 下一项指标：工具间空窗时延。
- 下一动作：验收工具超时率的新版双模式映射，再确认工具间空窗时延方案。

## 已知缺口

- 新版聊天 JSON 数组与研究模式事件流的结构已形成架构文档；调用事件完整率、完全重复
  调用率、MCP 工具调用膨胀率、必填参数完整率、Schema 合法率、参数值准确率、路由
  Precision/Recall、子任务冗余率、意图识别准确率、时间范围提取准确率、证据查准率和陈述
  支持率 collectors 已支持新版 canvas，其余指标 collectors 仍主要按旧版字段读取，完整统一
  Trace 模型尚未实现。
- collectors、聚合器、报告器、配置加载器和可运行 CLI 尚未实现。
- Golden Benchmark 校准集和交付物读取能力尚未接入。
- 路由标准目前由 LLM + MCP Schema 生成，尚未经过人工校准集冻结；因此属于可追溯的
  Judge 代理标准，而非人工 Golden Benchmark。
- 严格 JSON Schema 只能约束输出形状；verdict 与 reason code 的业务关系仍由 evaluator
  二次校验。回退到 `json_object` 的供应商更可能产生 `invalid_judge_output`，当前尚未实现
  携带校验反馈的业务 Schema 修复重试。
- 工具结果仅部分提供执行耗时，E2E 与跨层 Token 仍不能直接求和。
- 当前没有经服务方确认的生产工具 SLA 目录，因此聊天与研究真实日志的超时率暂时返回
  `missing_tool_sla`，不得使用示例阈值生成生产指标。

## 验证基线

```text
新版聊天日志规格重算（336 条 Trace，仅结构与确定性指标；未运行真实 Judge）：
调用事件完整率: 4251/4266 ≈ 0.99648；296 条 Trace 含普通工具请求，40 条 no_tool_calls
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
证据查准率结构上限: 缺生产证据工具目录且未运行 Judge，无最终得分；以手册全部 77 个工具
  仅验证解析链路时，140/336 条形成候选，含 2911 次目录内调用、2820 次成功响应和 26564 个
  结构化单元；120 条 incomplete_evidence_segmentation，65 条 no_evidence_calls，
  10 条 no_stable_evidence_units，1 条 no_successful_evidence_responses
陈述支持率/最终数值一致率: 缺生产证据工具目录且未运行 Judge，无最终得分
MCP 工具调用膨胀率: 33/2911 ≈ 0.01134；2878 个不同请求签名，1355 次手册外调用被排除
超时率: 3443/4266 次调用有工具执行耗时，823 次缺失；缺生产 SLA，返回 missing_tool_sla

以下为既有代码、旧版聊天日志与研究日志验证记录：
pytest: 393 passed
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
路由规范化 MCP 目录: 77 个能力，手册版本 0.1.0
路由规范化聊天日志: 106/106 个 Trace 成功，35 个 Trace 含 MCP 路由，提取 185 个子任务片段
路由规范化研究日志: 1 个 Trace 成功，5 个 MCP 能力；当前日志只提供 1 个整体意图片段
路由 collector 与双指标定向测试: 24 项通过；Precision 与 Recall 版本均为 `1.2.0`
路由双指标真实前 10 条聊天日志: 3 条可评估、7 条路由前服务失败；可评估日志 TP=6，
  实际能力=9，标准能力=14；微平均 Precision 0.66667，微平均 Recall 0.42857；
  Judge/格式错误 0
安全意图路由映射表: 14 条去重记录，敏感字段与中文实体扫描命中 0
子任务冗余率测试: 23 项通过；evaluator 版本 `1.1.0`
子任务冗余率聊天日志验算: 336 条中 253 条可计算，严格分母为 1194 个有效子任务；规则命中
  3 个冗余，微平均约 0.00251；81 条无子任务，2 条因无文本多模态子任务不可计算
子任务冗余率研究日志: 当前缺少确认的结构化子任务契约，返回 missing_structured_subtasks
意图识别准确率定向测试: 32 项通过；聊天与研究模式均为 CONDITIONALLY_COMPUTABLE；
  evaluator 版本 `1.2.0`
意图识别准确率聊天模式: 缺少 query 返回 missing_query；无行为证据返回 no_intent_evidence；
  固定前置服务失败返回 pre_intent_failure
意图识别准确率研究模式: 缺少 query 返回 missing_query；缺少调用 ID 返回 missing_call_id；
  同 ID 不同工具返回 conflicting_call_definition
意图识别准确率真实单调用冒烟: 聊天模式 3 段行为证据、1 个 MCP 能力，Judge 返回
  correct / material_intents_aligned / confidence 0.95，最终结果 1.0, computed
时间范围提取准确率定向测试: 35 项通过；聊天与研究模式均为 CONDITIONALLY_COMPUTABLE；
  evaluator 版本 `1.2.0`
时间范围提取准确率聊天模式: 支持显式 reference_time 和 `canvas.meta.create_time`；正常执行
  但未传递时间字段可由 Judge 计算为 0，固定前置服务失败返回 pre_time_extraction_failure
时间范围提取准确率研究模式: 支持事件 timestamp/create_time 或 part.state.time.start 锚点；
  缺少调用 ID、冲突定义和参数解析不完整分别显式返回安全原因码
时间范围提取准确率模拟计算: 正确字段 6、错误字段 1，结果 6/7 ≈ 0.85714, computed
时间范围提取准确率真实前 10 条聊天日志: 2 条可计算、1 条无时间需求、7 条前置模型服务
  失败；可计算日志正确字段 5、错误字段 2，微平均 5/7 ≈ 0.71429；Judge 失败 0
证据查准率定向测试: 31 项通过；聊天与研究模式均为 CONDITIONALLY_COMPUTABLE；evaluator
  版本 `1.2.0`
证据查准率新版聊天结构上限验算: 使用手册全部 77 个工具仅验证解析链路，336 条中 140 条
  形成 Judge 候选，2911 次目录内调用产生 2820 次成功响应和 26564 个稳定结构化单元；
  120 条 incomplete_evidence_segmentation，65 条 no_evidence_calls，10 条
  no_stable_evidence_units，1 条 no_successful_evidence_responses；另隔离 411 个孤立结果
证据查准率研究日志结构上限验算: 5 个目录内调用中抽取 7 个稳定证据单元，但另有 3 个
  成功响应无稳定来源边界，因此返回 incomplete_evidence_segmentation，不缩小分母；未调用
  真实 Judge，未生成语义分数
证据查准率真实前 10 条聊天日志: 使用仅覆盖样本内明确外部检索、行情、财务、公告和文档
  来源的临时 17 工具目录 `evidence-scope-8ce42ba0453c432e`；1 条 computed，50 个证据单元中
  relevant 37、irrelevant 13，结果 37/50 = 0.74；1 条因 24 个单元中存在 1 个
  not_assessable 返回 incomplete_relevance_judgment；1 条含 205 个单元的请求返回
  judge_failure；5 条 incomplete_evidence_segmentation，2 条 no_evidence_calls。该结果为
  共享运行时改良前的历史记录；新版本会对证据单元分批，尚未重新运行真实 Judge。该临时
  目录未进入生产配置，结果不得作为正式生产基线
陈述支持率定向与共享回归测试: 21 项通过；聊天与研究模式均为 CONDITIONALLY_COMPUTABLE；
  evaluator 版本 `1.2.0`
陈述支持率新版聊天结构验算: 使用临时 17 工具目录时，336 条中 192 条形成 Judge 候选，
  共观察 18257 个稳定证据单元；118 条 incomplete_evidence_segmentation，25 条
  pre_answer_failure，1 条 missing_final_answer。没有证据或明确空结果仍可进入 Judge 并得到
  0 分；旧版顶层 query/answer/tool_call_queue 不再支持
陈述支持率研究模式: 通过 assistant message ID 选择 turn.end 前最后一个非空文本；成功产物
  无最终正文返回 missing_final_artifact_content，缺少证据终态和不完整拆分不估算；当前研究
  日志因 3 个成功响应不可稳定拆分而返回 incomplete_evidence_segmentation
陈述支持率模拟计算: 2 个可验证原子陈述中 1 个 supported、1 个 unsupported，另有 1 个
  excluded 建议，结果 1/2 = 0.5, computed；聊天与研究 evaluator 路径均通过
陈述支持率真实前 10 条新版聊天日志: 使用临时 17 工具目录执行 Judge，1 条 computed，50 个
  可验证陈述全部 supported，结果及可计算样本微平均均为 50/50 = 1.0；4 条
  incomplete_evidence_segmentation，1 条 missing_final_answer，1 条 pre_answer_failure，
  1 条 invalid_judge_output，2 条在 120 秒超时边界返回 judge_failure。该结果为共享运行时
  改良前的历史记录；其中约 815 KiB 的超大请求在新版本会于网络调用前返回
  `judge_input_too_large`，尚未重新运行真实 Judge。成功请求耗时约 95.446 秒；临时目录未
  进入生产配置，结果不作为正式生产基线
超时率测试: 22 项通过；聊天与研究模式均为 CONDITIONALLY_COMPUTABLE；新版聊天按
  `canvas.meta.traceId` 与跨 Agent/message 的 `content.tool_call[]/tool_result[]` 关联唯一调用，
  排除子 Agent、孤立结果和旧版 `tool_call_queue`
超时率规则: 显式 timeout/timed_out、duration > 工具 SLA、SLA 已配置但缺少完整时间均计入分子；
  failed/error/cancelled 不自动等同超时，duration == SLA 不超时
超时率真实日志结构冒烟: 聊天与研究模式在未配置生产 SLA 时均返回 missing_tool_sla，
  未使用示例阈值估算或输出伪 0%
调用事件完整率新版聊天实现验算: 4266 次唯一普通工具请求中 4251 次观察到匹配结果，
  4251/4266 ≈ 0.99648；411 条孤立 Agent 返回被排除，296 条 Trace computed，
  40 条 no_tool_calls，重复请求/结果和 malformed 均为 0
完全重复调用率新版聊天实现验算: 4266 次唯一普通工具请求中 3389 次响应可比较，
  26/4266 ≈ 0.00609；877 次缺少可比较响应正文，411 条孤立 Agent 返回被排除，
  296 条 Trace computed，40 条 no_tool_calls，重复 ID、冲突和 malformed 均为 0
```
