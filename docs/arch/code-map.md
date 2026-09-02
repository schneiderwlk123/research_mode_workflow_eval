# Code Map

评测流水线方向：`collectors -> evaluators -> reporters`，数据和产物访问由 `storage` 提供。

- `docs/START_HERE.md`：每次新会话的稳定阅读入口和不可破坏约束。
- `docs/PROJECT_STATUS.md`：当前里程碑、下一动作、已知缺口和验证基线。
- `src/collectors/tool_call_events.py`：把聊天 `tool_call_queue` 与研究
  `message.part.updated` 工具状态规范化为不含请求、响应正文的统一调用事件。
- `src/collectors/exact_duplicate_calls.py`：在内存中规范化工具名、参数和响应并比较摘要，
  输出完全重复调用聚合计数，不保留原始正文或摘要。
- `src/collectors/__init__.py`：导出工具调用事件规范化类型与入口。
- `src/evaluators/base.py`：`BaseEvaluator` 抽象接口、统一结果对象和计算状态。
- `src/evaluators/tool_call_event_completeness.py`：按唯一调用计算请求与终态同时存在的
  调用事件完整率；研究模式缺少 `callID` 时严格返回不可计算。
- `src/evaluators/exact_duplicate_tool_calls.py`：使用安全聚合计数计算完全重复调用率。
- `src/evaluators/__init__.py`：导出公共契约和具体评测器。
- `src/reporters/`：跨 Trace 聚合与报告输出；尚待实现。
- `src/storage/`：原始数据和评测产物访问；尚待实现。
- `tests/evaluators/test_base_evaluator.py`：评测器公共契约的行为测试。
- `tests/collectors/test_tool_call_events.py`：聊天/研究工具事件规范化、去重、严格
  `callID` 和敏感正文隔离测试。
- `tests/collectors/test_exact_duplicate_calls.py`：聊天/研究完全重复识别、生命周期去重、
  缺失字段和敏感正文隔离测试。
- `tests/evaluators/test_tool_call_event_completeness.py`：完整率正常、边界、不可计算和
  非法输入隔离测试。
- `tests/evaluators/test_exact_duplicate_tool_calls.py`：完全重复率计算、零值、空调用、
  规范化错误和非法计数测试。
- `docs/specs/metrics.md`：按开发顺序集中记录所有指标的定义和原始字段映射。
- `tests/`：测试包根目录；子目录结构与 `src/` 对应。
- `configs/evaluation.example.toml`：提交安全的评测配置样例。
