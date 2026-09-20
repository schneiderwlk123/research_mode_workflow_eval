# Metric Development Order

当前批次指标以 `docs/specs/metrics_initial.md` 为口径来源，严格按以下顺序逐个设计、确认、
实现和验收。先建立工具调用规模基线，再开发 Token 成本指标，最后开发时延指标。

当前进度：10 项全部验收（2026-09-20 确认第十项 `max_tool_latency_share` 验收通过）。
后续新指标按《初步入库评测指标.md》维度推进，下一项为回答质量维度引用可溯率。

## 第一阶段：工具调用

1. 工具调用总次数（`tool_calls_count`）
2. 单工具调用重复次数（`per_tool_repeat_call_count`）

## 第二阶段：Token 成本

3. 单工具 Token 消耗（`per_tool_call_tokens`）
4. 端到端总 Token 消耗（`e2e_total_tokens`，复用稳定指标名 `total_token_consumption`）
5. 单工具最大 Token 消耗比率（`max_tool_token_share`）
6. 输出 Token 比率（`output_token_ratio`）
7. 缓存输入 Token 比率（`cache_input_token_ratio`）

## 第三阶段：时间

8. 单工具调用时延（`per_tool_call_latency_ms`）
9. 端到端总耗时（`e2e_latency`，复用稳定指标名 `end_to_end_latency_ms`）
10. 单工具最大时延比率（`max_tool_latency_share`）

每一项进入编码前，都必须单独确认算法与数学公式、聊天/研究日志字段映射、异常边界及样例计算。
聊天模式和研究模式必须分别标记为“可直接计算”“有条件可计算”或“不可计算”；不可计算时
必须列出缺失字段或可靠映射、稳定原因码以及未来可恢复计算所需的 schema 变化。无法由当前
日志直接计算的指标必须返回“不可计算”及安全的缺失原因，不得估算或静默省略该模式。
