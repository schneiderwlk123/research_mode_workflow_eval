# Code Map

评测流水线方向：`collectors -> evaluators -> reporters`，数据和产物访问由 `storage` 提供。

- `docs/START_HERE.md`：每次新会话的稳定阅读入口和不可破坏约束。
- `docs/PROJECT_STATUS.md`：当前里程碑、下一动作、已知缺口和验证基线。
- `.env.example`：本地运行配置模板；Judge 使用 `JUDGE_API_URL`、`JUDGE_API_KEY` 和
  `JUDGE_MODEL`，真实密钥只允许写入被忽略的 `.env`。
- `src/collectors/tool_call_events.py`：把聊天 `tool_call_queue` 与研究
  `message.part.updated` 工具状态规范化为不含请求、响应正文的统一调用事件。
- `src/collectors/exact_duplicate_calls.py`：在内存中规范化工具名、参数和响应并比较摘要，
  输出完全重复调用聚合计数，不保留原始正文或摘要。
- `src/collectors/required_parameters.py`：从版本化 MCP 手册构建顶层必填参数目录，
  统计聊天/研究调用的 Schema 覆盖和非空必填参数；输出聚合计数，不输出参数值或摘要。
- `src/collectors/tool_argument_schema.py`：把 MCP 手册类型、枚举和嵌套定义转换为递归
  Schema，按调用输出合法/非法与违规类别聚合计数；额外字段仅诊断，不使调用非法。
- `src/collectors/parameter_value_accuracy.py`：从 MCP 手册构建工具与字段语义目录，按稳定
  调用 ID 提取聊天/研究 query 和参数的内存候选；长期结果不得保留 query 或参数值。
- `src/collectors/routing.py`：从 MCP 手册构建版本化能力目录，提取聊天/研究模式的整体
  意图、全部可见子任务意图和实际 MCP 能力集合；框架工具只进入范围外诊断，明确的
  路由前模型服务失败会在调用 Judge 前标记为不可计算。
- `src/collectors/subtask_redundancy.py`：从聊天 `call_sub_agent_begin` 提取子任务，使用
  NFKC、约束词元和字符 bigram 规则计算高置信度冗余聚合计数；研究模式在结构化子任务
  契约确认前显式返回不可计算，任务正文和中间指纹不进入结果。
- `src/collectors/__init__.py`：导出工具调用事件规范化类型与入口。
- `src/evaluators/base.py`：`BaseEvaluator` 抽象接口、统一结果对象和计算状态。
- `src/evaluators/tool_call_event_completeness.py`：按唯一调用计算请求与终态同时存在的
  调用事件完整率；研究模式缺少 `callID` 时严格返回不可计算。
- `src/evaluators/exact_duplicate_tool_calls.py`：使用安全聚合计数计算完全重复调用率。
- `src/evaluators/required_parameter_completeness.py`：在 Schema 已覆盖范围内计算必填参数
  微平均完整率；覆盖调用数仅作为诊断，不派生辅助指标。
- `src/evaluators/tool_argument_schema_validity.py`：在 Schema 已覆盖调用上计算参数类型、
  枚举和嵌套结构合法率，仅保留安全诊断计数。
- `src/evaluators/parameter_value_accuracy.py`：定义可注入的版本化 Judge 接口，校验逐字段
  三态输出，隔离单调用失败，并仅输出参数值准确率主指标和安全诊断计数。
- `src/evaluators/openai_compatible_parameter_judge.py`：从 dotenv/环境变量加载 Judge 配置，
  通过 HTTPS OpenAI-compatible Chat Completions 接口请求结构化逐字段判定。
- `src/evaluators/routing.py`：定义路由 Judge 与安全映射表契约，一次生成标准能力集合和
  TP/FP/FN 共享计数，并分别计算 `routing_precision` 与 `routing_recall`。
- `src/evaluators/subtask_redundancy.py`：校验规则版本、固定阈值和安全聚合计数，计算
  `subtask_redundancy_rate`。
- `src/evaluators/openai_compatible_routing_judge.py`：把整体意图、全部子任务、MCP Schema
  和历史安全映射提交给 OpenAI-compatible Judge，生成严格结构化标准路由。
- `src/evaluators/__init__.py`：导出公共契约和具体评测器。
- `src/reporters/`：跨 Trace 聚合与报告输出；尚待实现。
- `src/storage/intent_route_mappings.py`：以 JSONL 去重保存不含原始文本和实体的意图签名、
  子任务类型与能力集合，并支持 Jaccard 相似映射检索。
- `src/storage/__init__.py`：导出安全意图路由映射存储入口。
- `tests/evaluators/test_base_evaluator.py`：评测器公共契约的行为测试。
- `tests/collectors/test_tool_call_events.py`：聊天/研究工具事件规范化、去重、严格
  `callID` 和敏感正文隔离测试。
- `tests/collectors/test_exact_duplicate_calls.py`：聊天/研究完全重复识别、生命周期去重、
  缺失字段和敏感正文隔离测试。
- `tests/collectors/test_required_parameters.py`：必填参数目录解析、聊天/研究参数计数、
  占位值、未知 Schema、零必填参数、调用去重和缺失 ID 测试。
- `tests/collectors/test_tool_argument_schema.py`：Schema 类型、数组元素、枚举、嵌套必填、
  额外字段、未知工具、参数解析和研究调用去重测试。
- `tests/collectors/test_parameter_value_accuracy.py`：聊天/研究 query 与调用候选提取、手册
  描述目录、稳定 ID、研究去重、定义冲突和内存参数不可变测试。
- `tests/collectors/test_routing.py`：聊天整体/子任务意图提取、MCP 能力范围、研究前缀映射、
  调用去重和冲突定义测试。
- `tests/collectors/test_subtask_redundancy.py`：聊天精确/高相似冗余、约束差异、非法事件、
  输入不可变、敏感正文隔离及研究模式不可计算测试。
- `tests/evaluators/test_tool_call_event_completeness.py`：完整率正常、边界、不可计算和
  非法输入隔离测试。
- `tests/evaluators/test_exact_duplicate_tool_calls.py`：完全重复率计算、零值、空调用、
  规范化错误和非法计数测试。
- `tests/evaluators/test_required_parameter_completeness.py`：必填参数完整率、辅助指标排除、
  零分母、规范化错误和非法聚合计数测试。
- `tests/evaluators/test_tool_argument_schema_validity.py`：Schema 合法率、辅助指标排除、
  零分母、规范化错误和非法聚合计数测试。
- `tests/evaluators/test_parameter_value_accuracy.py`：Judge 三态字段聚合、辅助指标排除、
  部分失败隔离、非法 Judge 输出和不可计算边界测试。
- `tests/evaluators/test_openai_compatible_parameter_judge.py`：dotenv 配置、HTTPS 边界、请求
  结构、认证头、结构化响应解析和供应商异常脱敏测试。
- `tests/evaluators/test_routing.py`：共享 Judge 调用、Precision/Recall 集合计数、零分母、
  Judge 非法输出、失败脱敏和安全映射写入测试。
- `tests/evaluators/test_subtask_redundancy.py`：冗余率、单任务、零分母、规范化错误和非法
  聚合计数测试。
- `tests/evaluators/test_openai_compatible_routing_judge.py`：整体/子任务意图、MCP Schema 与
  历史映射的结构化请求测试。
- `tests/storage/test_intent_route_mappings.py`：映射表字段安全、记录去重、加载和相似签名
  检索测试。
- `docs/specs/metrics.md`：按开发顺序集中记录所有指标的定义和原始字段映射。
- `tests/`：测试包根目录；子目录结构与 `src/` 对应。
- `configs/evaluation.example.toml`：提交安全的评测配置样例。
