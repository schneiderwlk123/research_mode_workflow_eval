# Code Map

评测流水线方向：`collectors -> evaluators -> reporters`，数据和产物访问由 `storage` 提供。

- `docs/START_HERE.md`：每次新会话的稳定阅读入口和不可破坏约束。
- `docs/PROJECT_STATUS.md`：当前里程碑、下一动作、已知缺口和验证基线。
- `src/collectors/`：聊天模式 JSONL 与研究模式 JSON 的读取和规范化入口；尚待实现。
- `src/evaluators/base.py`：`BaseEvaluator` 抽象接口、统一结果对象和计算状态。
- `src/evaluators/`：逐指标实现；当前尚无具体指标。
- `src/reporters/`：跨 Trace 聚合与报告输出；尚待实现。
- `src/storage/`：原始数据和评测产物访问；尚待实现。
- `tests/evaluators/test_base_evaluator.py`：评测器公共契约的行为测试。
- `tests/`：测试包根目录；子目录结构与 `src/` 对应。
- `configs/evaluation.example.toml`：提交安全的评测配置样例。
