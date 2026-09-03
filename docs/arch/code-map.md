# Code Map

评测流水线方向：`collectors -> evaluators -> reporters`，数据和产物访问由 `storage` 提供。

- `docs/START_HERE.md`：每次新会话的稳定阅读入口和不可破坏约束。
- `docs/PROJECT_STATUS.md`：当前里程碑、下一动作、已知缺口和验证基线。
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
- `src/collectors/__init__.py`：导出工具调用事件规范化类型与入口。
- `src/evaluators/base.py`：`BaseEvaluator` 抽象接口、统一结果对象和计算状态。
- `src/evaluators/tool_call_event_completeness.py`：按唯一调用计算请求与终态同时存在的
  调用事件完整率；研究模式缺少 `callID` 时严格返回不可计算。
- `src/evaluators/exact_duplicate_tool_calls.py`：使用安全聚合计数计算完全重复调用率。
- `src/evaluators/required_parameter_completeness.py`：在 Schema 已覆盖范围内计算必填参数
  微平均完整率，并同时报告 Schema 覆盖率和手册版本。
- `src/evaluators/tool_argument_schema_validity.py`：在 Schema 已覆盖调用上计算参数类型、
  枚举和嵌套结构合法率，并报告覆盖率与安全诊断。
- `src/evaluators/parameter_value_accuracy.py`：定义可注入的版本化 Judge 接口，校验逐字段
  三态输出，隔离单调用失败，并聚合参数值准确率、完全匹配率和可判定覆盖率。
- `src/evaluators/__init__.py`：导出公共契约和具体评测器。
- `src/reporters/`：跨 Trace 聚合与报告输出；尚待实现。
- `src/storage/`：原始数据和评测产物访问；尚待实现。
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
- `tests/evaluators/test_tool_call_event_completeness.py`：完整率正常、边界、不可计算和
  非法输入隔离测试。
- `tests/evaluators/test_exact_duplicate_tool_calls.py`：完全重复率计算、零值、空调用、
  规范化错误和非法计数测试。
- `tests/evaluators/test_required_parameter_completeness.py`：必填参数完整率、Schema 覆盖率、
  零分母、规范化错误和非法聚合计数测试。
- `tests/evaluators/test_tool_argument_schema_validity.py`：Schema 合法率、覆盖率、零分母、
  规范化错误和非法聚合计数测试。
- `tests/evaluators/test_parameter_value_accuracy.py`：Judge 三态字段聚合、遗漏字段、完全匹配
  与可判定覆盖率、部分失败隔离、非法 Judge 输出和不可计算边界测试。
- `docs/specs/metrics.md`：按开发顺序集中记录所有指标的定义和原始字段映射。
- `tests/`：测试包根目录；子目录结构与 `src/` 对应。
- `configs/evaluation.example.toml`：提交安全的评测配置样例。
