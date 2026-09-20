# 指标初始规格

> **状态：已退役（2026-09-20）。** 评测指标的权威口径以根目录《初步入库评测指标.md》为准；
> 本文仅存档历史过程记录（2026-09-15 零埋点降级口径设计与取消记录）。内容与权威口径冲突处
> （如本文记录"MCP 工具调用膨胀率已取消"，实际该指标已按权威口径实现并验收 v1.2.0）一律以
> 权威口径为准。恢复本文中任一降级口径设计（技术完成率、业务失败信号探测、重试代理口径）前，
> 按仓库规则重新立项并确认公式、字段映射、异常边界和模拟计算。

## 指标设定背景

经日志发现，聊天模式与研究模式最值得优先优化的不是回答正文，而是"模型—工具循环次数"和重复上下文输入。聊天模式的 Token 与模型步骤数相关系数约 0.9645，与工具调用数约 0.9492；研究模式 97% 左右的 Token 都是 cache read，说明同一大上下文被反复送入模型。所以，聊天模式和研究模式主要从工具调用、成本、时延三个维度进行评测。

> 本文结构说明：第 1–3 章为已确认并实现批次（10 项指标）的双模式初始定义；第 4–7 章为 Agent 能力四维度候选批次——任务完成、工具使用、推理与规划、容错与纠错；2026-09-15 调整：5.1 MCP 工具调用膨胀率与 5.3 参数准确率取消，工具调用成功率、MCP 选择准确率、错误恢复完成率（0/1 交付口径）与工具重试率/重试成功率（失败触发再调用代理口径）完成重新定义，四项均具备零埋点降级口径；第 6 章推理与规划维度仅保留已实现的子任务冗余率，无新增候选。全部候选未编码、未验收；第 8–9 章为配套埋点需求与开发顺序。原"证据来源与答案质量"层 7 项候选已暂缓，规格存档于 `docs/specs/metrics_deferred.md`。候选指标进入实现前，每项仍需按仓库规则单独确认公式、字段映射、异常边界和模拟计算。

## 汇总宽表身份列约定

- 固定身份列顺序为 `user_id`、`session_id`、`log_id`、`mode`，其后按开发顺序追加指标值列。
- `log_id` 表示一条完整输入日志的统一主键：聊天模式读取 `canvas.meta.traceId`，研究模式读取用户级 `userMessageID`（兼容 `body.messageID`）。
- 聊天模式另读取顶层 `userId` 和 `canvas.meta.sessionId`；研究模式另读取 `headers.userid` 和 `sessionID`（兼容 `body.sessionId`）。
- 研究模式的 `part.messageID` 是模型步骤标识，`headers.traceid` 是请求链路标识，均不作为完整研究日志的 `log_id`。
- 缺失的非主键身份字段输出空单元格；`log_id` 缺失时使用只与本地源文件和记录位置有关的稳定匿名回退值。
- 全量汇总固定写入 `data/processed/metric_table.csv`；后续增加或修改指标时覆盖更新该文件，不创建带版本号的 CSV 副本。

## 1. 工具调用维度

### 1.1 工具调用总次数（tool_calls_count）

#### 指标定义

tool_calls_count 表示单条 Trace 内按当前结构规则识别的唯一工具调用总数。聊天模式排除可明确识别的 Agent 委派和 respond 返回；研究模式直接统计合法 tool 事件。

```text
tool_calls_count = Σ per_tool_statistics[tool_name].call_count

同名工具重复调用总数 =
  Σ per_tool_statistics[tool_name].repeat_call_count
```

没有业务工具调用时本指标计算为 0，不是不可计算。

#### 字段映射

聊天模式：DIRECTLY_COMPUTABLE。

- 调用定义：canvas.agents[].messages[].content.tool_call[]。
- 调用 ID：toolId；工具名称：toolName。
- 只有 agentName 且没有 toolId/toolName 的子 Agent 委派不进入统计。
- 工具名精确为 respond（忽略大小写）时按 Agent 返回事件排除，不需要工具类别目录。

研究模式：DIRECTLY_COMPUTABLE。

- 调用事件：message.part.updated.part.type == tool。
- 调用 ID：part.callID；工具名称：part.tool。
- 同一 callID 的 pending、running 和终态事件合并为一次调用。
- 当前口径直接统计所有具有非空 callID 和 tool 的 tool 事件，不依赖工具类别目录。

缺少调用 ID 返回 ERROR / missing_call_id；缺少工具名称返回 ERROR / missing_tool_name。

#### 观测价值

- 直接观察工作流扇出规模；调用数异常升高通常意味着搜索扩散、规划反复或循环未及时终止。
- repeat_call_count 定位同名工具在单条 Trace 内被反复调用的问题。
- 与 end_to_end_latency_ms 联合观察，可区分"调用数量多导致慢"和"调用数量少但单次调用慢"。
- 与 Trace 总 Token 联合观察，可判断 Token 增长是否伴随工具调用轮次膨胀。

### 1.2 单工具调用重复次数（per_tool_repeat_call_count）

#### 指标定义

per_tool_repeat_call_count 表示单条 Trace 内每一种业务工具在首次调用之后发生的额外调用次数。只有同名工具调用总数大于 1 时才进入结果；调用一次不算重复。

```text
call_count[tool_name] =
  当前 Trace 内该规范化工具名的唯一调用数

repeat_call_count[tool_name] =
  max(call_count[tool_name] - 1, 0)

total_repeat_call_count =
  Σ repeat_call_count[tool_name]
```

例如，同一 Trace 内工具 A 调用 4 次、工具 B 调用 2 次、工具 C 调用 1 次，则 A 重复 3 次、B 重复 1 次、C 不进入重复工具列表，total_repeat_call_count = 4。

当多个工具发生重复调用时，使用 JSON 数组输出。数组按 repeat_call_count 降序、call_count 降序、tool_name 升序排列，保证相同输入产生稳定结果：

```json
{
  "per_tool_repeat_call_count": [
    {
      "tool_name": "tool_a",
      "call_count": 4,
      "repeat_call_count": 3
    },
    {
      "tool_name": "tool_b",
      "call_count": 2,
      "repeat_call_count": 1
    }
  ],
  "repeated_tool_name_count": 2,
  "total_repeat_call_count": 4
}
```

没有重复工具时返回 COMPUTED，数组为空，两个汇总计数均为 0；不得返回不可计算或省略字段。

汇总宽表只增加 `per_tool_repeat_call_count` 一个字段，其单元格保存上述紧凑 JSON 数组；`repeated_tool_name_count` 和 `total_repeat_call_count` 仅作为表外诊断，不增加 CSV 字段。

跨 Trace 聚合时必须先在每条 Trace 内计算重复次数，再按工具名求和：

```text
report_repeat_call_count[tool_name] =
  Σ_trace max(call_count[trace, tool_name] - 1, 0)
```

禁止把全量数据集中某工具的总调用数直接减 1，否则会把不同 Trace 各自一次的正常调用误判为重复调用。报告级结果可额外输出 affected_trace_count，表示发生该工具重复调用的 Trace 数。

#### 字段映射

聊天模式：DIRECTLY_COMPUTABLE。

- 复用 tool_calls_count 的 canvas.agents[].messages[].content.tool_call[]、toolId 和 toolName。
- 按 traceId + toolId 唯一化后，以规范化 tool_name 分组计数。
- 结构化子 Agent 委派和工具名为 respond 的 Agent 返回不进入分组。

研究模式：DIRECTLY_COMPUTABLE。

- 复用 message.part.updated.part.type == tool、part.callID 和 part.tool。
- 同一 callID 的多个状态事件先合并为一次调用，再按规范化 tool_name 分组计数。

缺少调用 ID、缺少工具名称或同一调用 ID 的工具定义冲突时，分别返回 ERROR / missing_call_id、ERROR / missing_tool_name、ERROR / conflicting_call_definition。

#### 观测价值

- 直接定位单条 Trace 内被反复调用的具体工具及额外调用次数。
- 区分"工具种类多"与"少数同名工具反复调用"；两者的优化方式不同。
- 与 per_tool_call_latency_ms 联合观察，可以估算重复调用带来的额外时延压力。
- 与 per_tool_call_tokens 联合观察，可以定位重复次数和单次 Token 同时较高的重点工具。
- 报告级 affected_trace_count 可判断问题是集中在少量异常 Trace，还是普遍存在于多数请求。

## 2. 成本维度

### 2.1 单工具token消耗（per_tool_call_tokens）

#### 指标定义

per_tool_call_tokens 表示同一 Trace 内，产生某一规范化业务工具调用的模型步骤平均 Token 消耗。字段名为兼容既定输出保留，但统计对象是"工具关联模型步骤"，不是工具服务内部执行。

采用完整步骤关联规则：一个模型步骤调用多个不同工具时，该步骤完整 Token 分别关联给每个不同工具名，不进行平均拆分；同一步骤内同一规范化工具名出现多次时只关联一次，避免同一工具重复累计同一份步骤 Token。

```text
tool_step_count[tool_name] =
  包含该规范化工具名的唯一模型步骤数

tool_associated_token_total[tool_name] =
  Σ 包含该工具名的唯一模型步骤 Token

per_tool_call_tokens[tool_name] =
  tool_associated_token_total[tool_name]
  ÷ tool_step_count[tool_name]
```

call_count 仍表示唯一工具调用次数，tool_step_count 表示包含该工具的模型步骤数；一个步骤并行或批量发出多个同名调用时，两者可能不同，不得互相替代。token_observed_count 和 token_missing_count 按关联模型步骤计数。

```text
single_tool_step_count[tool_name] =
  只包含一个不同业务工具名、且该工具名为 tool_name 的模型步骤数

multi_tool_step_count[tool_name] =
  包含多个不同业务工具名、且其中包含 tool_name 的模型步骤数

tool_step_count[tool_name] =
  single_tool_step_count[tool_name] + multi_tool_step_count[tool_name]

shared_token_step_count =
  当前 Trace 内包含多个不同业务工具名的唯一模型步骤数
```

同名工具关联的任一模型步骤缺少 Token 时，该工具返回 NOT_COMPUTABLE / incomplete_token_coverage，不得输出部分平均值。没有业务工具调用时返回 NOT_COMPUTABLE / no_tool_calls；存在业务工具调用但没有可关联模型步骤时返回 NOT_COMPUTABLE / missing_tool_step_association。

汇总宽表只增加 `per_tool_call_tokens` 一个字段，单元格保存按 `tool_name` 升序排列的紧凑 JSON 数组。可计算工具项只输出 `tool_name` 和 `per_tool_call_tokens`；局部不可计算工具保留 `per_tool_call_tokens: null` 和安全 `reason`，其他覆盖计数只进入表外诊断。只要至少存在业务工具，局部缺失不阻止同一日志中其他工具输出；没有业务工具时整格留空。

#### 字段映射

聊天模式：DIRECTLY_COMPUTABLE。

- 模型步骤按 canvas.agents[].messages[] 的稳定遍历位置识别；只读取同一消息 content.tool_call[] 和 content.usageTokens，不读取 canvas.answer.usageTokens 重复快照。
- 步骤 Token 使用 content.usageTokens.input_tokens + output_tokens，与 total_token_consumption 的聊天模式口径一致；cache_tokens 不额外加入。
- 调用按 traceId + toolId 唯一化，排除 agent_delegation、agent_return 和 framework_control 后，将步骤 Token 关联给该步骤内每个不同的规范化 toolName。
- 一个步骤内同名工具出现多次时，call_count 按不同 toolId 计数，但 tool_step_count、token_observed_count 和 tool_associated_token_total 只累计一次。

研究模式：DIRECTLY_COMPUTABLE。

- 工具调用读取 message.part.updated 中 part.type == tool 的 part.messageID、part.callID 和 part.tool；同一 callID 的 pending、running、completed 等生命周期快照先合并为一次调用。
- 模型步骤 Token 只读取同一 messageID 下 part.type == step-finish 的 part.tokens.total。message.updated.info.tokens 和 llm.response.result.usage 是重复投影，不参与累计。
- 按 part.messageID 将模型步骤 Token 关联给该步骤内每个不同的规范化工具名；同一步骤同名工具只关联一次。
- 缺少 messageID、同一 messageID 出现冲突 Token 快照，分别返回 ERROR / missing_step_id、ERROR / conflicting_token_snapshot。

多工具步骤允许跨工具重复关联同一份步骤 Token，因此：

```text
Σ tool_associated_token_total[tool_name] 可能大于 total_token_consumption
```

这种重叠是已确认的归因规则，不是重复日志。结果必须输出 tool_token_attribution = full_step_association、single_tool_step_count、multi_tool_step_count 和 shared_token_step_count，禁止把各工具关联 Token 相加解释为 Trace 总 Token，也禁止把多工具步骤 Token 平均拆分。

#### 观测价值

- 识别哪些工具通常由高 Token 模型步骤产生，定位工具选择和参数生成阶段的上下文成本。
- 与 per_tool_call_latency_ms 联合观察，可区分"模型规划成本高"和"工具执行本身慢"。
- 与 repeat_call_count 联合观察，可以判断同名工具反复调用是否持续触发高 Token 模型步骤。
- 本指标不能用于计算工具供应商费用；工具内部 Token 仍需工具终态提供独立 usage 字段。

### 2.2 端到端总token消耗（e2e_total_tokens）

#### 指标定义

本指标衡量单条 Trace 从请求开始到最终回答结束期间，全部已确认 Agent 模型步骤的 Token 消耗总和。展示名称为"端到端总 Token 消耗"，稳定指标名继续使用 total_token_consumption，单位为 token，避免与现有实现和历史结果产生重复指标。

```text
聊天模式 Token 消耗 =
  Σ 唯一模型步骤的 (input_tokens + output_tokens)

研究模式 Token 消耗 =
  Σ 唯一 step-finish 的 tokens.total
```

"端到端"表示统计范围覆盖当前 Trace 内 orchestrator、worker 和其他可识别 Agent 的全部模型步骤，不表示把工具耗时、墙钟时间或工具响应正文换算为 Token。本指标不是费用指标，不应用缓存折扣、模型单价或供应商价格系数。

当前主指标只汇总 Agent 模型步骤 Token，不包含工具内部再次调用模型产生的 Token。原因是两种现有日志都没有按工具调用 ID 提供工具自身 usageTokens.total。不得把发起工具调用的模型步骤 Token 重复记为工具 Token。未来上游补齐逐工具 Token 后，如需将其纳入端到端总量，必须提升 evaluator_version 并新增明确的 tool_token_consumption 分项，禁止静默改变历史口径。

#### 字段映射

聊天模式：DIRECTLY_COMPUTABLE。

- 模型步骤来源：canvas.agents[].messages[]。具有 durationSeconds 或 usageTokens 的消息视为模型步骤；仅含 tool_result 的消息是工具结果投影，不进入 Token 覆盖分母。
- 单步骤值：content.usageTokens.input_tokens + output_tokens。
- cache_tokens 不额外加入总量；当前主值只使用 input_tokens + output_tokens，避免缓存 Token 被重复计算。
- canvas.answer.usageTokens 是某条 Agent message 的重复快照，不进入计算。
- orchestrator 和 worker 均代表独立模型步骤，其 Token 可以相加。
- 同一条 Agent message 既包含 usageTokens 又包含一个或多个 tool_call 时，Token 仍只属于该模型步骤，不按工具数量复制或分摊。

研究模式：DIRECTLY_COMPUTABLE。

- 权威来源：message.part.updated.part.type == step-finish 的 part.tokens.total。
- 使用 part.messageID 标识唯一模型步骤；相同重复快照去重并记录 duplicate_token_snapshot_count，不同快照返回 ERROR / conflicting_token_snapshot。
- message.updated.info.tokens 与 step-finish 是重复快照，不参与计算。
- 每个 loop 的两个 llm.response.result.usage 是重复协议投影，不参与计算。
- tokens.input/output/reasoning/cache 不单独解析，直接使用上游 total。
- 当分项完整时，可校验 total = input + output + reasoning + cache.read + cache.write；校验值只作为诊断，不重复加入主指标。

Token 消耗必须覆盖全部已识别模型步骤。全部步骤缺少 Token 时返回 NOT_COMPUTABLE / no_observable_token_usage；部分步骤缺失时返回 NOT_COMPUTABLE / incomplete_token_coverage，不得输出部分总量。非负整数校验失败、缺少研究步骤 ID 分别返回 ERROR / invalid_token_count、ERROR / missing_step_id。规范化结果只保留逐步骤总数、覆盖计数和重复计数，不保留消息、推理或工具正文。evaluator 版本为 1.0.0。

#### 观测价值

- 直接衡量单条 Agent 工作流的整体模型资源消耗，是 Token 优化的主指标。
- 与 tool_calls_count、per_tool_repeat_call_count 联合观察，可以判断总 Token 增长是否来自工具调用轮次和重复调用膨胀。
- 与 output_token_ratio 联合观察，可以区分 Token 主要消耗在上下文输入还是模型输出。
- 与 cache_input_token_ratio 联合观察，可以识别大量输入 Token 是否来自重复读取缓存上下文。
- 与 end_to_end_latency_ms 联合观察，可以区分"高 Token 且慢""高 Token 但不慢"和"Token 不高但外部工具很慢"等不同瓶颈。
- 该指标不能直接替代费用。跨模型、跨供应商或跨缓存策略比较时，必须同时保留模型版本、Token 口径版本和缓存语义。

### 2.3 单工具最大token消耗比率（max_tool_token_share）

#### 指标定义

max_tool_token_share 表示单条 Trace 内，关联模型步骤 Token 最多的规范化业务工具占端到端模型总 Token 的比例。它衡量某一种工具与高 Token 模型步骤的关联集中度，不表示该工具服务内部消耗，也不要求所有工具的占比加总为 1。

```text
tool_associated_token_total[tool_name] =
  Σ 包含该工具名的唯一模型步骤 Token

max_tool_token_share =
  max(tool_associated_token_total[tool_name])
  ÷ total_token_consumption
```

结果范围为 [0, 1]；工具关联步骤 Token 合法为零时结果计算为 0，不得误记为缺失。同一模型步骤包含多个不同工具时，完整步骤 Token 分别进入各工具的 tool_associated_token_total，但分母始终使用不重复的 Trace 级 total_token_consumption。因此各工具的 share 允许重叠，其总和可能大于 1。

指标同时输出 max_token_tool_names 和 max_tool_associated_tokens。多个工具并列最高时，max_token_tool_names 使用按规范化工具名升序排列的 JSON 数组。汇总宽表只增加 `max_tool_token_share` 一个字段，单元格保存紧凑 JSON 数组，每项仅含 `tool_name` 和 `max_tool_token_share`；并列最大工具各占一个数组项并使用相同比率，关联 Token 仅保留在表外元数据。

本指标要求 Trace 总 Token 以及全部工具关联步骤 Token 覆盖完整。存在缺失时返回 NOT_COMPUTABLE / incomplete_token_coverage；无业务工具调用时返回 NOT_COMPUTABLE / no_tool_calls；Trace 总 Token 为零时返回 NOT_COMPUTABLE / zero_total_token_consumption；工具调用无法关联模型步骤时返回 NOT_COMPUTABLE / missing_tool_step_association。

#### 字段映射

聊天模式：DIRECTLY_COMPUTABLE。

- 分母复用 total_token_consumption：汇总所有唯一模型消息的 content.usageTokens.input_tokens + output_tokens。
- 分子复用 per_tool_call_tokens 的同消息 toolName + usageTokens 完整步骤关联结果。

研究模式：DIRECTLY_COMPUTABLE。

- 分母复用 total_token_consumption：按 messageID 去重并汇总全部 step-finish.tokens.total。
- 分子复用 per_tool_call_tokens 的同 messageID 工具与 step-finish 关联结果。
- 不读取 message.updated.info.tokens 或 llm.response.result.usage，避免重复计算。

#### 观测价值

- 比例高表示大部分模型 Token 出现在包含同一种工具的步骤中，应优先检查该工具对应的上下文、参数生成和重复调用链路。
- 与 repeat_call_count 联合观察，可以区分"少量高 Token 步骤"和"重复调用累计模型成本"。
- 与 output_token_ratio、cache_input_token_ratio 联合观察，可以进一步判断关联成本主要来自模型输出、新输入还是缓存上下文。
- 多工具步骤会让多个工具共享同一份 Token，因此该指标用于定位关联热点，不能用于工具间费用分摊。

### 2.4 输出token比率（output_token_ratio）

#### 指标定义

output_token_ratio 是 Trace 级模型输出 Token 占模型总 Token 的比例，用于解释工具工作流中 Token 是消耗在上下文输入还是模型输出。

```text
output_token_ratio =
  Σ 模型步骤 output_tokens
  ÷ total_token_consumption
```

本指标不是工具自身 Token 比例，也不把输出 Token 归属到具体工具。模型 Token 覆盖不完整时返回 NOT_COMPUTABLE / incomplete_token_coverage；分母为零时返回 NOT_COMPUTABLE / zero_total_token_consumption。

合法零输出返回 0。全部步骤无 Token 时返回 NOT_COMPUTABLE / no_observable_token_usage；非法非负整数、研究步骤 ID 缺失、同一步骤快照冲突或 output 大于 total 时返回 ERROR。汇总宽表只增加 `output_token_ratio` 一个数值字段，诊断计数不进入 CSV；evaluator 版本为 1.0.0。

#### 字段映射

聊天模式：DIRECTLY_COMPUTABLE。

- 分子：canvas.agents[].messages[].content.usageTokens.output_tokens 之和。
- 分母：同一批唯一模型步骤的 input_tokens + output_tokens 之和。
- canvas.answer.usageTokens 是最终 Agent 消息的重复快照，不参与计算。

研究模式：DIRECTLY_COMPUTABLE。

- 分子：唯一 message.part.updated 中 part.type == step-finish 的 part.tokens.output 之和。
- 分母：同一批唯一步骤的 part.tokens.total 之和。
- message.updated.info.tokens 和同 loop 的 llm.response.result.usage 是重复投影，不参与计算。

#### 观测价值

- 比例低且总 Token 高，说明成本主要来自上下文输入、缓存读取或多轮模型调用，而不是回答过长。
- 比例高时，可进一步检查模型是否产生过长中间文本或最终回答。
- 与 tool_calls_count 联合观察，可以判断工具调用轮次增加是否主要放大输入上下文。

### 2.5 缓存输入token比率（cache_input_token_ratio）

#### 指标定义

cache_input_token_ratio 是 Trace 级缓存 Token 占真实输入 Token 的比例。

```text
cache_input_token_ratio =
  cache_token_consumption
  ÷ effective_input_token_consumption
```

缓存 Token 是模型步骤资源，不属于工具自身 Token。聊天日志的 input_tokens 已包含 cache_tokens；研究日志的 input 不包含缓存，因此两种模式使用不同字段组合重建同一语义的真实输入。

#### 字段映射

聊天模式：DIRECTLY_COMPUTABLE。

- 分子：canvas.agents[].messages[].content.usageTokens.cache_tokens 之和。
- 分母：相同模型步骤的 input_tokens 之和；该字段已包含 cache_tokens，不得重复相加。
- 每一步必须满足 cache_tokens <= input_tokens。
- canvas.answer.usageTokens 不参与计算。

研究模式：DIRECTLY_COMPUTABLE。

- 分子：唯一 message.part.updated 中 part.type == step-finish 的 part.tokens.cache.read + part.tokens.cache.write 之和。
- 分母：同一批唯一步骤的 part.tokens.input + part.tokens.cache.read + part.tokens.cache.write 之和。
- 每一步必须满足 total = input + output + reasoning + cache.read + cache.write；不满足时返回 ERROR / inconsistent_token_breakdown。

缺少完整 input/cache 观测返回 NOT_COMPUTABLE / no_observable_cache_usage；部分步骤缺失返回 NOT_COMPUTABLE / incomplete_cache_token_coverage；真实输入分母为零返回 NOT_COMPUTABLE / zero_effective_input_token_consumption。合法零缓存计算为 0。汇总宽表只增加 `cache_input_token_ratio` 一个数值字段，诊断计数不进入 CSV；evaluator 版本为 1.0.0。

#### 观测价值

- 比例高表示模型调用主要在重复读取缓存上下文，优化重点应放在减少模型步骤数、缩短上下文或避免重复携带大型工具结果。
- 比例低但 input Token 高，说明主要消耗来自未缓存的新输入或频繁变化的上下文。
- 缓存命中不等于零成本或零时延，本指标只能解释 Token 构成，不能单独代表成本节省比例。

## 3. 时间维度

### 3.1 单工具调用时延（per_tool_call_latency_ms）

#### 指标定义

per_tool_call_latency_ms 表示同一 Trace 内，同名业务工具所有可观测调用的平均执行耗时，单位为毫秒。

```text
单次工具耗时 = 工具终态时间 - 工具开始时间

per_tool_call_latency_ms[tool_name] =
  Σ 同名工具有效调用耗时
  ÷ latency_observed_count
```

部分调用缺少耗时时，仍可对有效调用计算平均值，但必须同时输出 latency_observed_count 和 latency_missing_count。同名工具没有任何有效耗时时返回 NOT_COMPUTABLE / no_observable_tool_duration，不得补零。

#### 字段映射

聊天模式：DIRECTLY_COMPUTABLE。

- 按 traceId + toolId 关联 tool_call 与 tool_result。
- 单次耗时：tool_result.durationSeconds × 1000。
- 相同耗时快照去重；同一调用出现不同耗时返回 ERROR / conflicting_duration_observation。

研究模式：DIRECTLY_COMPUTABLE。

- 按 callID 合并工具生命周期。
- 开始时间：所有状态事件中最早的 part.state.time.start。
- 结束时间：终态事件中最晚的 part.state.time.end。
- 单次耗时为结束时间减开始时间；重复状态只记录诊断。

两种模式缺少完整边界时，该调用标记为 NOT_COMPUTABLE / missing_tool_duration；非法时间和负时序分别返回 ERROR / invalid_duration、ERROR / invalid_event_order。

汇总宽表只增加 per_tool_call_latency_ms 一个字段，单元格保存按 tool_name 升序排列的紧凑 JSON 数组；每项只包含 tool_name 和 per_tool_call_latency_ms。状态、原因、覆盖计数和 evaluator_version 仅保留在表外元数据与问题摘要。缺失耗时只影响覆盖计数；同名工具仍有有效调用时继续计算。没有有效调用时该工具项的值为 null，并记录 NOT_COMPUTABLE / no_observable_tool_duration。非法、负时序或冲突耗时只使受影响工具项为 null 并记录 ERROR，其他工具继续输出。evaluator 版本为 1.0.0。

#### 观测价值

- 按工具名比较平均耗时，定位长期偏慢的工具类型。
- 结合 repeat_call_count 判断某工具的总影响来自"单次慢"还是"调用次数多"。
- 结合 end_to_end_latency_ms 识别单工具长尾，但不得把并行或嵌套调用的耗时简单相加为 Trace 总耗时。

### 3.2 端到端总耗时（e2e_latency）

#### 指标定义

本指标衡量单条 Trace 从用户请求开始到最终回答完成之间经过的墙钟总耗时。展示名称为"端到端总耗时"，稳定指标名继续使用 end_to_end_latency_ms，单位为毫秒，避免与现有实现和历史结果产生重复指标。

```text
end_to_end_latency_ms =
  最终回答完成时间 - 用户请求开始时间
```

该时间范围自然包含模型推理、工具执行、Agent 调度、子任务等待、排队和框架开销，是用户实际等待时间的主指标。它不是各模型步骤耗时与各工具耗时的算术和：步骤和工具可能并行、嵌套，某个 Agent 消息耗时也可能覆盖其内部工具调用时间。

#### 字段映射

聊天模式：DIRECTLY_COMPUTABLE。

- 开始时间：canvas.meta.create_time。
- 结束时间：canvas.answer.create_time。
- 两个字段为秒级时间戳，规范化后计算毫秒差值并保留 timestamp_precision=seconds；结果为 0 ms 时只表示边界落在同一秒。
- canvas.answer.durationSeconds 是终止 Agent 消息耗时的复制，不是完整 Trace 时延，禁止用作本指标。
- canvas.agents[].messages[].content.durationSeconds 和 tool_result.durationSeconds 只用于步骤或工具诊断，不参与端到端总耗时求和。
- 旧版顶层聊天结构不支持，返回 ERROR / invalid_chat_trace_structure。

研究模式：DIRECTLY_COMPUTABLE。

- 回合边界：同一 sessionID + userMessageID 的 turn.start 与 turn.end。
- 指标值优先使用 turn.end.durationMs；同时计算 turn.end.ts - turn.start.ts，将时间戳差值 - durationMs 记录为 boundary_difference_ms。
- durationMs 缺失时回退到时间戳差值；相同重复边界去重并记录计数，不同重复边界返回 ERROR / conflicting_turn_boundary。
- message.updated.info.time、模型 step 边界和工具生命周期只能用于分阶段诊断，不替代 turn.start/turn.end 回合边界。

两种模式缺少开始或结束边界时分别返回 NOT_COMPUTABLE / missing_start_timestamp 或 missing_end_timestamp。非法时间戳、非法报告耗时和负时序分别返回 ERROR / invalid_timestamp、ERROR / invalid_duration 和 ERROR / invalid_event_order。规范化结果只保留标识、时间边界、报告耗时、精度和安全诊断，不保留 query、回答或工具正文。evaluator 版本为 1.0.0。

#### 观测价值

- 直接反映用户感知的整体响应速度，是所有工具调用效率指标的 Trace 级基准。
- 与 tool_calls_count 联合观察，可以区分"调用次数膨胀"与"少量调用但整体仍慢"。
- 与 per_tool_call_latency_ms 联合观察，可以定位单个慢工具是否构成主要瓶颈。
- 与 per_tool_repeat_call_count 联合观察，可以判断同名工具反复调用是否伴随总耗时上升。
- 与 total_token_consumption 联合观察，可以区分高 Token 的模型循环问题与低 Token、长等待的外部工具问题。
- 工具可能并行、嵌套或被模型步骤包含，因此不得把各工具耗时简单求和后当作 end_to_end_latency_ms，两者只用于相关性和瓶颈分析。

### 3.3 单工具最大时延比率（max_tool_latency_share）

#### 指标定义

max_tool_latency_share 表示单条 Trace 内，累计执行耗时最高的同名业务工具占全部业务工具累计执行耗时的比例。它衡量工具耗时是否集中在某一种工具上，不表示该工具占端到端墙钟耗时的比例。

```text
tool_latency_total_ms[tool_name] =
  Σ 同名工具全部唯一调用的 duration_ms

total_tool_latency_ms =
  Σ 全部业务工具唯一调用的 duration_ms

max_tool_latency_share =
  max(tool_latency_total_ms[tool_name])
  ÷ total_tool_latency_ms
```

结果范围为 (0, 1]。指标同时输出 max_latency_tool_names、max_tool_latency_total_ms 和 total_tool_latency_ms。多个工具的累计耗时并列最高时，max_latency_tool_names 使用按规范化工具名升序排列的 JSON 数组，不得任意选择其中一个。

本指标要求当前 Trace 的全部业务工具调用均具有有效耗时。存在任一调用缺少耗时时返回 NOT_COMPUTABLE / incomplete_tool_duration_coverage；无业务工具调用时返回 NOT_COMPUTABLE / no_tool_calls；全部工具累计耗时为零时返回 NOT_COMPUTABLE / zero_tool_duration。非法耗时、负时序和同一调用的耗时观察冲突继续沿用 per_tool_call_latency_ms 的错误原因，不得使用部分调用估算占比。

#### 字段映射

聊天模式：DIRECTLY_COMPUTABLE。

- 复用 per_tool_call_latency_ms 的 traceId + toolId 调用关联和业务工具分类。
- 单次调用耗时读取 tool_result.durationSeconds × 1000。
- 按规范化 tool_name 汇总 tool_latency_total_ms，再从所有业务工具累计耗时计算最大占比。
- tool_result.durationSeconds 缺失的调用进入耗时缺失计数，并使本 Trace 的该指标不可计算。

研究模式：DIRECTLY_COMPUTABLE。

- 复用 per_tool_call_latency_ms 的 callID 生命周期合并和业务工具分类。
- 单次调用耗时为最晚终态 part.state.time.end 减最早 part.state.time.start。
- 同一 callID 的重复状态先去重，再按规范化 tool_name 汇总累计耗时。
- 任一业务工具调用缺少完整开始或结束时间时，本 Trace 的该指标不可计算。

工具可能并行或嵌套，因此 total_tool_latency_ms 是逐调用耗时之和，可能大于 end_to_end_latency_ms。本指标的分子和分母都使用同一累计工具耗时口径，所以不得将 max_tool_latency_share 解读为端到端时间占比，也不得为了消除重叠而擅自截断调用耗时。

聊天模式排除结构化子 Agent 调用及 respond 结果侧 Agent 返回，只统计业务工具。汇总宽表只增加 max_tool_latency_share 一个字段，单元格保存按 tool_name 升序排列的紧凑 JSON 数组，每项只包含 tool_name 和 max_tool_latency_share；累计耗时、覆盖与排除计数仅进入表外元数据。evaluator 版本为 1.0.0。

#### 观测价值

- 比例高表示工具执行成本集中在少数工具上，可优先排查头部工具的单次性能和调用策略。
- 与 per_tool_call_latency_ms 联合观察，可以区分"单次调用慢"和"单次不慢但累计调用次数多"。
- 与 repeat_call_count 联合观察，可以识别最大耗时工具是否由重复调用累计形成。
- 与 end_to_end_latency_ms 联合观察，可以判断工具累计耗时集中是否同时伴随用户等待时间增长；但两者不能直接相减或相除解释并行贡献。
- 报告级可按模式分别统计该工具成为最大耗时工具的 Trace 数及比例，定位稳定的系统瓶颈。

## 4. 任务完成维度（候选批次，未编码未验收）

> 边界声明：本维度全部指标只评测"技术完成"（有没有产出结果、链路有没有失败），不评测"语义正确"（结果对不对）。语义正确性需要任务参照集，属于模型选型评测的范畴，不在评测基座范围内。

> 2026-09-15 调整：埋点 C 在可见周期内不会落地，本维度全部指标改为零埋点降级口径，并以"业务失败信号探测"（8.3 节）作为失败判定基础。信号探测依据真实日志实勘（文件 `agent_trace_pro_20260828-20260903_20260904_171853.json`，336 条聊天 Trace、3541 次业务工具调用）：`errorcode`（字符串"0"= 成功，"500"= 失败，观测 184 次）、`success`（布尔，False = 失败，观测 76 次）、`status`（failed 为业务失败，pending/running/submitted 为异步任务态，观测 17 次）。全库共 45 次调用命中失败信号，分布在 6 个工具（annual_report_extract 34、company_finance_search 3、backtest_tasks_status 3、get_financial_snapshot 2、get_stock_details 2、read_file 1），覆盖 30 条 Trace。孤立结果 1136 条、无结果调用 15 次。信号字段名与语义因工具而异，只作代理判定依据并保留版本化映射表，埋点 C 落地后整体升级为终态权威口径。

### 4.1 工具调用成功率（tool_call_success_rate）

状态：候选待确认，未编码。2026-09-15 重新定义：采用零埋点业务信号降级口径，并以真实日志实勘锚定 bad case 分布。

#### 指标定义

tool_call_success_rate 表示单条 Trace 内，业务结果成功（含业务级成功）的工具调用占全部可判定调用的比例。

```text
成功判定（按优先级）：
  a. 命中业务失败信号（errorcode 非 "0"/"200"、success == False、
     status ∈ {failed, timeout}）→ 失败
  b. 有结果且未命中失败信号 → 成功
  c. 无关联结果（孤立结果、结果缺失、result 为 null）→ 不可判定，不计入分母

tool_call_success_rate =
  判定为成功的唯一业务工具调用数 ÷ 可判定调用总数
```

与既有指标的分母边界严格区分：tool_calls_count 统计全部唯一调用（含不可判定）；本指标分母只含可判定成败的调用。不可判定调用进入 undetermined_call_count 诊断（不补零、不猜测成败）；命中失败信号的调用进入 explicit_failure_signal_count 诊断。埋点 C 落地后，判定升级为终态权威口径，且 isolated_result（孤立结果）按失败类终态处理，届时按仓库规则升版。

诊断计数：undetermined_call_count（无关联结果或 result 为 null 的调用数）、isolated_result_count（无请求侧请求者的孤立结果数）、explicit_failure_signal_count（命中失败信号调用数，附按字段分布）、async_pending_count（status 为 pending/running/submitted 的异步任务调用数，既不算成功也不算失败，单独诊断）。

无业务工具调用时返回 NOT_COMPUTABLE / no_tool_calls；全部调用不可判定时返回 NOT_COMPUTABLE / no_decidable_calls。零失败信号时正确返回 1，不代表链路无风险（信号探测覆盖率有限），需与 undetermined_call_count 一并解读。

#### 字段映射

聊天模式：CONDITIONALLY_COMPUTABLE（业务信号降级口径）。

- 调用唯一化：复用 traceId + toolId 关联口径，排除子 Agent 委派与 respond 返回。
- 结果关联：复用 per_tool_call_latency_ms 的 tool_call ↔ tool_result 关联；同一调用多个结果快照时按终态时间取最新。
- 失败信号：按 8.3 节版本化映射表扫描 result 载荷；映射表升级需重算基线并确认 evaluator 是否升版。

研究模式：NOT_COMPUTABLE（代理口径不适用）。

- 研究 tool part 的 status 仅 pending/running/completed，无失败终态；业务失败信号（errorcode/success）未见统一挂载点。强行以 completed 代理成功会掩盖业务失败，禁止。埋点 C 落地前研究模式保持不可计算。

#### Bad case 场景（满分扣减口径）

- **业务失败信号**：result 载荷 errorcode = "500"（年报提取 34 次、个股详情 51 次观测）、success = False（run_code 1 次）、status = failed（回测任务 3 次）——每次命中即一次失败调用，直接拉低本指标。
- **孤立结果**：1136 条 tool_result 找不到对应请求（4.5.0 版结构上为 Agent 返回侧副产物）。本口径下不计入分母、仅诊断；若未来确认为真实失败回执，须升版改为失败。
- **无结果调用**：15 次调用无关联结果——不可判定，不计入分母；若某工具长期不可判定，说明该工具链路观测能力有缺口，报告级单独提示。
- **误报风险**：部分工具把"查询无结果"以 errorcode = "500" 返回（业务空结果而非链路失败）。映射表无法区分两者时，本指标会低估成功率；该偏差方向必须写进结果元数据，业务确认后可在映射表增加"业务空结果"白名单（版本化）。
- **异步任务态误判**：backtest_tasks_status 的 status = pending/submitted/running 是任务进行中而非失败，禁止计入失败分子或分母外失败。

#### 观测价值

- 任务完成情况的最直接过程证据：失败信号率高的 Trace 很难产出正确答案。
- 与 per_tool_call_latency_ms 联合观察：区分"慢但成功"与"慢且失败"。
- 报告级按工具名聚合 explicit_failure_signal_count，可定位系统性工具故障（当前实勘集中在 annual_report_extract）。

### 4.2 技术完成率（trace_delivery_rate）

状态：候选待确认，未编码。2026-09-15 新增：与 7.1 错误恢复完成率合并口径，统一衡量"最终是否向用户交付"。

#### 指标定义

trace_delivery_rate 表示单条 Trace 最终是否向用户交付了非空最终回答，为 0/1 布尔判定，Trace 级输出、报告级聚合。

```text
delivered = 最终回答锚点非空
          且回答非"链路失败道歉/兜底文案"（可选项，白名单判定）

trace_delivery_rate（报告级）= delivered 的 Trace 数 ÷ 全部 Trace 数
```

判定规则：

- 回答锚点：聊天取 `canvas.answer.answer` 非空字符串；研究取 `turn.end` 前最后 assistant text part 非空。
- 兜底文案识别为可选增强：回答仅含"抱歉/失败/超时/请稍后重试"类模板且无实质内容时计 0（delivered = false）。白名单与模板清单为版本化业务确认项；未提供白名单时只判非空，不得猜测。
- 有工具失败但最终交付非空回答的 Trace，delivered = true（失败情况由 4.1 与 7.1 分别刻画，本指标只看交付结果）。

全部 Trace 均无回答锚点时返回 NOT_COMPUTABLE / no_answer_anchor。本指标为 0/1 判定，不做部分得分。

#### 字段映射

聊天模式：DIRECTLY_COMPUTABLE。

- 回答锚点：canvas.answer.answer。
- 工具失败（供 7.1 使用）：8.3 节业务失败信号探测。

研究模式：DIRECTLY_COMPUTABLE（交付锚点）/ 失败判定 NOT_COMPUTABLE。

- 回答锚点：turn.end 前最后 assistant text part。
- 失败调用判定与聊天模式同因不可用，研究模式失败侧不可计算；交付侧可直接计算。

#### Bad case 场景

- **空回答**：canvas.answer.answer 为空串、null 或纯空白——计 0。
- **兜底道歉文案**：回答只有"抱歉，本次查询失败，请稍后再试"类模板、无任何实质内容——提供白名单后计 0；未提供白名单时只能判 1 并在元数据标记 suspicion_of_fallback_answer。
- **回答与请求主体无关**（跑题交付）：本指标不判跑题（语义正确性不在范围），但与意图识别准确率交叉可发现该类 Trace。
- **孤立失败无回答**：Trace 存在失败信号且回答为空——计 0，且此类 Trace 是 7.1 错误恢复完成率的核心样本。

#### 观测价值

- 直接回答"用户有没有拿到结果"：所有过程指标最终都要落到这个 0/1 上。
- 与 4.1 联合观察：区分"工具失败但兜底交付"与"工具失败且交付失败"。
- 实勘现状：336 条聊天 Trace 中 335 条非空回答、1 条空——当前交付率极高，问题主要在交付质量而非交付本身；本指标用于长期监控回归。

### 4.3 已取消指标

#### 参数准确率（mcp_parameter_accuracy）——2026-09-15 取消

原口径（必填 ∧ 非空 ∧ 通过 Schema 校验的参数占比）与已实现的必填参数完整率（v1.2.0）、Schema 合法率（v1.2.0）高度重叠：三者在真实日志中的差值集中在极少数边界调用上，独立成指标的观测增益有限。参数合法性观测需求由既有两项指标继续承担；语义正确性由 parameter_value_accuracy_rate（v1.4.0，Judge 类）承担。如需恢复，按仓库规则重新立项。

#### MCP 工具调用膨胀率（mcp_tool_call_inflation_rate）——2026-09-15 取消

原口径（同能力 + 相同规范化入参签名的后续调用占比）已实现（v1.1.0）并完成全量验算（33/2911 ≈ 1.13%），但评审确认其观测价值与单工具调用重复次数（per_tool_repeat_call_count）及完全重复调用率（exact_duplicate_tool_call_rate）高度重叠：重复调用次数指标已覆盖"谁被重复调用"，完全重复率已覆盖"是否原样重发"，膨胀率作为两者乘积视角的中间指标不再单独维护。已实现的评测器代码与规格转入存档，不再接入统一宽表。如需恢复，按仓库规则重新立项并升版。

## 5. 工具使用维度（5.1 MCP 选择准确率待实现；5.2 已取消；5.3 已移除）

> 边界声明：第 1 章已覆盖调用计数与重复计数；本维度评测工具使用的质量。2026-09-15 调整：MCP 工具调用膨胀率（原 5.1）与参数准确率（原 5.3）取消（见 4.3），本维度仅保留 MCP 选择准确率一项候选。

### 5.1 MCP 选择准确率（mcp_selection_accuracy）

状态：候选待确认，未编码。口径继承 2026-09-14 评审结论（正确调用的 MCP 数 ÷ 全部 MCP 调用数，分母只含手册目录内的 MCP 调用），2026-09-15 补充零埋点受理判定与 bad case 口径。无 Judge、无标注依赖。

#### 指标定义

mcp_selection_accuracy 表示单条 Trace 内，正确调用（目录匹配且被正常受理）的 MCP 占全部 MCP 调用的比例，衡量 Agent 使用 MCP 能力时每次调用是否正确。

```text
mcp_selection_accuracy =
  正确调用的 MCP 数 ÷ 全部 MCP 调用数
```

- 分母：手册目录内的唯一 MCP 调用（按既有调用唯一化口径去重）；手册外的 toolName 不属于 MCP 调用，不进入分母，进入 out_of_catalog_count 诊断。
- 分子："正确调用"须同时满足：
  - toolName 与 mcp_tool_manual.json 的 tools[].name 精确匹配（禁止模糊字符串猜测）；
  - 调用被正常受理并返回结果。受理判定采用零埋点降级口径：成功关联到 tool_result 且未命中 8.3 节业务失败信号 → 受理；命中失败信号 → 未受理；无关联结果 → 不可判定，退出分子并进入 undetermined_call_count 诊断。
- 调用是否服务 query 意图不进入判定（属路由指标口径）；参数合法性不进入判定（由必填参数完整率与 Schema 合法率承担）。
- 无法证明为 MCP 的框架/环境工具（沿用 routing collector 的既定区分规则）单独计入 framework_tool_count 诊断，不进入分母也不进入分子。

"被正常受理"的精确边界依赖埋点 C（工具终态 status）；落地前使用上述降级口径，两个口径不得混用、须分别版本化。业务空结果（查询无数据但链路正常）计为受理成功，不因返回内容为空判失败。

诊断计数：out_of_catalog_count、framework_tool_count、unaccepted_call_count（命中失败信号的目录内调用数）、undetermined_call_count（无关联结果调用数）。

无业务工具调用时返回 NOT_COMPUTABLE / no_tool_calls；无手册内 MCP 调用时返回 NOT_COMPUTABLE / no_in_catalog_calls。

#### 字段映射

聊天模式：DIRECTLY_COMPUTABLE（降级受理口径）。

- 调用与工具名：复用 tool_calls_count 的 canvas.agents[].messages[].content.tool_call[]、traceId + toolId 唯一化与子 Agent / respond 排除口径。
- 结果关联与失败信号：复用 4.1 的 tool_call ↔ tool_result 关联与 8.3 节信号映射表，作为受理证据。
- 手册目录：mcp_tool_manual.json 的 tools[].name（版本化 0.1.0，77 个能力），与 toolName 精确匹配。

研究模式：NOT_COMPUTABLE（降级受理口径不适用）。

- 研究模式无统一失败信号挂载点，无法判定"未受理"；以 completed 终态代理受理会掩盖业务失败，禁止。埋点 C 后按终态口径解锁；工具名读取 part.tool（兼容 toolCalls[].toolName）。

#### Bad case 场景（满分扣减口径）

- **目录匹配但调用失败**：toolName 在手册内、result 载荷 errorcode = "500"——计入分母、不计入分子（实勘中 get_main_business_segments、get_stock_details、annual_report_extract、searchCompanyOperatingData 均有此类观测）。
- **手册外调用**：不扣本指标分值（不进分母），但 out_of_catalog_count 高说明工具目录覆盖或提示词声明有缺口——实勘 1355 次手册外调用，报告级按工具名分布输出。
- **同名异义**：不同 Agent 生态存在同名工具时，禁止按名称相似度猜测匹配；只精确匹配，匹配歧义调用进入 ambiguous_name_count 诊断并退出分子分母。
- **异步任务态**：backtest 类工具 result.status ∈ {pending, running, submitted} 表示任务已受理、进行中——计为受理成功（请求结构被接受），不因任务未完成扣分。
- **重复调用部分失败**：同一工具多次调用中部分命中失败信号——按调用逐次计，失败的调用拉低本指标；与其他重复类指标的联合解读见观测价值。

#### 观测价值

- 直接回答"Agent 调用 MCP 是否每次都成功"：准确率偏低说明存在未被正常受理的调用，按工具名聚合 unaccepted_call_count 可定位不稳定的具体能力。
- 手册外调用不进入分母，但 out_of_catalog_count 的工具名分布仍是提示词/目录配置问题的直接信号，随本指标一并输出。
- 与 tool_calls_count 联合观察：调用数高 + MCP 选择准确率低 = 调用频繁且失败集中，优先排查该链路的工具声明与受理状况。
- 与 4.1 工具调用成功率构成范围互补：4.1 覆盖全部业务工具（含手册外），本指标只对手册目录内 MCP 计分；两者差值可归因到"手册外工具的失败"。

### 5.2 已取消指标

#### MCP 工具调用膨胀率（mcp_tool_call_inflation_rate）——2026-09-15 取消

取消理由与处置见 4.3。原规格与已实现评测器（v1.1.0）转入存档，不再接入统一宽表。

#### 参数准确率（mcp_parameter_accuracy）——2026-09-15 取消

取消理由与处置见 4.3。原规格（2026-09-14 评审版）废弃，未编码。

## 6. 推理与规划维度（候选批次，未编码未验收）

> 边界声明：规划"好不好"的语义判断需要参照；本维度只评测规划的效率与结构特征。子任务冗余率（已实现，v1.1.0）属于本维度已入库成员，此处不重复定义。6.1 子任务分解效率与 6.2 上下文复用效率经 2026-09-14 评审移出本批次，规格已从本文档删除；如需恢复按仓库规则重新立项。

## 7. 容错与纠错维度（候选批次，未编码未验收）

> 边界声明：这是现有日志缺口最大的维度。2026-09-15 调整：错误恢复完成率重新定义为 0/1 交付判定（聊天模式零埋点可算）；工具重试率与重试成功率重新定义为"失败触发再调用"代理口径（聊天模式零埋点可算）。研究模式在埋点 C 落地前，除交付锚点外不可计算。所有降级口径必须显式声明并输出覆盖率诊断，不得静默代理。

### 7.1 错误恢复完成率（error_recovery_completion_rate）

状态：候选待确认，未编码。2026-09-15 重新定义：只看 Trace 最终是否向用户输出结果还是报错，0/1 二值，不设中间档。

#### 指标定义

衡量"出现失败调用后，链路最终是否把结果兜回来交付给用户"。Trace 级二值判定，报告级输出比率：

```text
had_tool_failure =
  Trace 内存在 ≥1 次失败调用
  （聊天降级口径：8.3 节业务失败信号命中；埋点 C 后升级为终态判定）

recovered ∈ {0, 1}：
  1 = 最终向用户交付了正常结果（回答锚点非空，且不是报错/道歉兜底文案）
  0 = 最终向用户报错或未交付（回答锚点为空，或回答仅为错误提示）

error_recovery_completion_rate（报告级）=
  recovered = 1 的 Trace 数 ÷ had_tool_failure = true 的 Trace 数
```

- 0/1 判定不接受部分得分：一次失败后哪怕其余链路完美，只要最终向用户交付的是报错或空结果，即记 0。
- 回答锚点：聊天取 canvas.answer.answer；研究取 turn.end 前最后一个非空 assistant 文本。
- 报错判定采用版本化兜底文案白名单（如"抱歉，本次查询失败/服务异常/请稍后重试"类模板）；白名单为业务确认项，未冻结时只按非空判定，且结果元数据须声明 fallback_whitelist_version = not_configured，防止把"道歉即交付"误判为恢复。
- 无失败调用的 Trace 不进分母，输出 had_tool_failure = false，不是满分样本。
- Trace 级输出：had_tool_failure（布尔）、recovered（仅 had_tool_failure 时有值）、fallback_answer_suspected（布尔，回答非空但命中兜底文案模板）。
- 与 4.2 技术完成率的关系：4.2 面向全部 Trace 判交付，本指标只面向"有失败调用"的 Trace 判交付——同一判定机制、不同分母，禁止混算。

实勘约束（336 条聊天 Trace）：30 条命中失败信号的 Trace 全部有非空回答，即仅按"非空"判定时恢复率为 30/30 = 1.0，指标失去区分度；因此报错文案判定是本指标能否有效的关键，兜底文案白名单必须随首版实现冻结。

#### 字段映射

聊天模式：CONDITIONALLY_COMPUTABLE（降级口径零埋点）。

- 失败调用：8.3 节业务失败信号探测（errorcode 非 "0"/"200"、success == False、status = failed）。
- 回答锚点：canvas.answer.answer 非空。
- 报错判定：版本化兜底文案白名单匹配（不读取回答内容落盘，仅在内存判定）。

研究模式：NOT_COMPUTABLE（失败判定侧）。

- 失败调用：研究 tool part 无失败终态，业务失败信号无统一挂载点，禁止以 completed 代理成功。
- 回答锚点：turn.end 前最后 assistant text part 非空（交付侧可计算，但失败侧不可计算时整体不可计算）。

#### 缺失埋点

- 失败判定升级依赖埋点 C（工具终态 status + error_code）；报错判定依赖兜底文案白名单（业务确认项）。
- 埋点 D 落地后可进一步拆分"重试恢复"与"换路恢复"。

#### 观测价值

- Agent 测评框架维度 4（容错与纠错）的可计算核心：错误不可怕，翻车不交付才是事故。
- 与 4.1 工具调用成功率联合观察：失败率高但恢复率高 = 链路有兜底能力；失败率高且恢复率低 = 失败直接透传给用户。
- 报告级按 error_code / 失败信号类型聚合恢复率，定位哪些失败类型可以自动兜底、哪些必须人工介入。

### 7.2 工具重试率与重试成功率（tool_retry_rate / retry_success_rate）

状态：候选待确认，未编码。2026-09-15 重新定义：埋点 D（retry_of_call_id）落地前，以可观测行为把"重试"操作化为"失败触发再调用"，聊天模式零埋点可算；代理口径与埋点 D 精确口径分别版本化，不得混用。

#### 指标定义

**重试的代理判定**：同一 Trace 内，后继调用 C2 计为对 C1 的重试，当且仅当：

1. C1、C2 为同一规范化业务工具（toolName 相同，排除子 Agent 与 respond）；
2. C2 的参数签名与 C1 相同或为 C1 的超集增量（参数变化允许，工具一致即可——与已取消膨胀率的"完全相同签名"口径相反，重试允许换参重试）；
3. C1 的结果为失败（8.3 业务失败信号）或 C1 无关联结果（undetermined）；
4. C2 在事件顺序上紧随 C1 且两者之间不存在 C1 的成功结果。

```text
retriable_call_count =
  结果为失败或不可判定的唯一调用数（聊天降级口径）

tool_retry_rate（报告级）=
  发起重试的失败调用数 ÷ retriable_call_count

retry_success_rate（报告级）=
  重试后到达成功结果（未命中失败信号且有关联结果）的调用数
  ÷ 发起重试的调用数
```

- 重试链输出 retry_chain_count 与逐链终态（chain_length、最终成功/仍失败/链中断）；比率只在报告级聚合（单 Trace 内失败调用基数过小）。
- 换参重试（签名不同但同工具）与原样重试（签名相同）分别计数：same_signature_retry_count、changed_param_retry_count——前者是纯重放，后者带策略调整信号，分开观察可区分"机械重试"与"反思重试"。
- 无埋点 D 时，同名工具的正常周期性调用（如轮询 backtest_tasks_status）可能被误判为重试，禁止猜测：轮询类调用由业务确认清单（版本化）显式豁免，未豁免的一律按重试代理判定并保留可疑标记。
- retriable_call_count 为零时（全部调用成功或无终态）报告级分母为空，输出 NOT_COMPUTABLE / no_retriable_calls，不代表无风险。
- 明确语义边界：重试成功指"后续调用拿到成功结果"，不等于最终回答正确；回答正确性由质量类指标承担。

#### 字段映射

聊天模式：CONDITIONALLY_COMPUTABLE（代理口径零埋点）。

- 调用序列：canvas.agents[].messages[] 稳定遍历顺序（跨 Agent 按消息序），排除子 Agent 委派与 respond。
- 失败判定：8.3 节业务失败信号探测；参数签名在内存生成、不落盘。
- 精确链：tool_call[] 增补 attempt_id / retry_of_call_id（埋点 D）后切换精确口径。

研究模式：NOT_COMPUTABLE（埋点 D 前）。

- 重试链：tool part 生命周期扩展重试关联（埋点 D）。

#### 缺失埋点

- 硬依赖埋点 D（重试标识）+ 埋点 C（终态）后升级精确口径；代理口径零埋点可算。
- 代理口径已知局限：无法区分"框架自动重试"与"模型自主重试"（无 attempt 归属），报告级须声明该局限。

#### 观测价值

- 直接对齐 Agent 测评框架维度 4 的重试策略，并补全 v1.8 稳定性报告字段（重试率、重试后成功率）。
- 与 4.1 / 7.1 联合观察：失败信号率高 + 重试率高 + 恢复率高 = 链路有韧性；失败率高 + 重试率低 = 失败直接透传。
- changed_param_retry_count 高说明模型在失败后会调整参数策略（反思重试），是规划灵活性的正向过程信号。

## 8. 配套埋点需求汇总

以下需求基于 2026-09-14 对两份样本日志的全量字段实勘（聊天 336 条 Trace、研究 380 个事件；只扫描字段名与状态取值，不读取正文）。

### 8.1 日志实勘结论

- 聊天模式结构证实：message.content 仅 4 键（durationSeconds / usageTokens / tool_call / tool_result），tool_call 仅 5 键（agentName / arguments / taskId / toolName / toolId），tool_result 仅 5 键（toolId / toolName / result / durationSeconds / rawResult）；无任何 message 级时间戳；usageTokens 仅 input/output/cache 三分项。
- 研究模式结构证实：tool part 的 state.status 仅 pending/running/completed 三态，无失败终态；message.updated.info 具备毫秒级绝对时间戳（time.created/completed）与 cost；全库无任何 retry/attempt 键。
- 业务级失败信号（非埋点，字段不统一）：聊天 result 载荷内观测到 errorcode（184 次，"0"×143、"500"×41）、success（76 次，False×1）、status（17 次，failed×3 / pending×4 / running×5 / submitted×4）；研究 tool.debug 含 apiCalls[].httpStatus（仅 excel 22 条）。
- 溯源字段现状：研究模式 llm.response 的工具输出已含结构化 chunkList（chunkId / docId / libraryId / chunkText / businessTime / url 等）；聊天模式工具输出仅有 url/title，无结构化溯源 ID。

### 8.2 未实现埋点清单

```text
C 工具终态与错误分类：status（success/failed/timed_out/cancelled）、error_code、error_class
  挂载位置：聊天 tool_result[] 增补字段；研究 part.state.status 扩展失败类终态
  实勘证据：聊天 tool_result 无任何 status 类字段；研究三态无失败终态
  解锁：4.1 成功率与 5.1 选择准确率的权威终态口径、7.1 恢复判定的失败分类
  现状：埋点 C 在可见周期内不会落地；聊天模式已改用 8.3 业务失败信号降级口径

D 重试标识：attempt_id / retry_of_call_id / retried_from
  挂载位置：聊天 tool_call[]；研究 tool part 生命周期
  实勘证据：两侧全库 0 个 retry/attempt 键
  解锁：7.2 重试率与重试成功率切换精确重试链口径
  现状：聊天模式已改用"失败触发再调用"代理口径，代理与精确口径分别版本化

E 聊天侧工具请求与消息级绝对时间：tool_call.create_time + message 级 create_time
  挂载位置：聊天 tool_call[] 与 agents[].messages[]
  实勘证据：聊天全无时间类字段；研究模式已具备毫秒级 info.time.created/completed，E 为聊天特有缺口
  解锁：首次工具决策时延、工具间空窗（均为原裁掉口径，恢复需重新立项确认）

B 答案引用结构：citations[]{source_id, locator}（暂缓指标依赖，规格见 metrics_deferred.md）
  挂载位置：聊天 canvas.answer；研究最终 text part
  实勘证据：两模式回答均无引用结构
  解锁：暂缓批次的引用存在性检查、召回-利用比权威口径
```

若个别指标需在埋点 C/D 落地前先行实现，规格必须显式声明降级口径并单独版本化，禁止与埋点后口径混用。第 4、5、7 章当前候选即按此原则全部声明了零埋点降级口径。

### 8.3 过渡方案：业务失败信号探测（非埋点）

埋点 C 落地前，聊天模式以"业务失败信号探测"作为 4.1 / 5.1 / 7.1 / 7.2 的失败判定基础。2026-09-15 依据真实日志（`agent_trace_pro_20260828-20260903_20260904_171853.json`，336 条 Trace）完成实勘并确认初始映射表：

- 探测规则：扫描 result 载荷内以下信号，按版本化映射表归类：
  - `errorcode`：字符串，"0" = 成功，非 "0"（观测值 "500"）= 失败；观测 184 次（get_main_business_segments 70、get_stock_details 51、annual_report_extract 34、searchCompanyOperatingData 19 等）；
  - `success`：布尔，False = 失败；观测 76 次（run_code 29、formula_selector 15、write_file 10 等）；
  - `status`：failed = 失败；pending / running / submitted 为异步任务态，既非成功也非失败；观测 17 次（backtest_tasks_status、report 等）。
- 实勘分布：全库 45 次调用命中失败信号，覆盖 30 条 Trace；集中在 annual_report_extract（34 次）、company_finance_search（3）、backtest_tasks_status（3）、get_financial_snapshot（2）、get_stock_details（2）、read_file（1）。
- 关联质量：孤立结果 1136 条（结构上为 Agent 返回侧副产物，不进入成败分母）、无结果调用 15 次、result 为 null 1923 次（多为异步任务中间态，不可判定）。
- 边界：字段名与语义因工具而异（errorcode 为字符串、success 为布尔、status 含异步任务态），只能作为代理判定依据，埋点 C 落地后升级为终态权威口径；"业务空结果以 errorcode = 500 返回"的偏差方向须写入结果元数据。
- 输出：各指标复用本探测的失败分类；business_failure_signal_count 及按工具名分布随 4.1 一并输出，不新增宽表指标列。

### 8.4 业务确认项（非埋点）

- 框架/环境工具清单：用于 5.1 剥离 framework_tool_count。
- 兜底报错文案白名单：用于 4.2 与 7.1 的"向用户报错"判定；未冻结时按非空判定并声明 fallback_whitelist_version = not_configured。
- 轮询类工具豁免清单：用于 7.2 代理重试判定豁免正常周期性调用（如 backtest_tasks_status 轮询）。
- 业务空结果白名单：确认哪些工具的 errorcode = "500" 实为"查询无结果"，避免成功率低估。
- 生产工具 SLA 目录：tool_timeout_rate 当前返回 missing_tool_sla，不得用示例阈值。
- 手册版本升级与 evaluator 版本联动规则：手册升级时重算基线并确认各依赖手册的指标是否升版。
- 研究模式溯源字段（chunkList）的长期保留承诺：暂缓指标 A（检索溯源）若恢复，研究模式可直接复用现有字段、无需新增埋点；聊天模式是否补齐结构化溯源 ID 需上游确认。

## 9. 开发顺序与范围边界

建议顺序（遵守一次只开发一个指标的仓库规则；2026-09-15 重新定义后聊天模式四项均零埋点可算）：

```text
1. tool_call_success_rate           零埋点（8.3 业务失败信号降级口径），埋点 C 后升级权威终态
2. trace_delivery_rate              零埋点（0/1 交付判定），兜底文案白名单待业务确认
3. mcp_selection_accuracy           零埋点（受理 = 结果关联 且 无失败信号），埋点 C 后升级
4. error_recovery_completion_rate   零埋点（0/1 恢复判定），埋点 C 后升级失败判定
5. tool_retry_rate / retry_success  零埋点（失败触发再调用代理口径），埋点 C + D 后升级精确链
```

2026-09-15 取消记录：

- 参数准确率（mcp_parameter_accuracy）：原 2026-09-14 评审口径与必填参数完整率、Schema 合法率高度重叠，观测增益有限；未编码，规格废弃。
- MCP 工具调用膨胀率（mcp_tool_call_inflation_rate）：与单工具调用重复次数、完全重复调用率高度重叠；已实现评测器（v1.1.0）转入存档，不再接入统一宽表，历史验算基线（33/2911）随之退役。

范围边界（不做清单）：

- 任务语义正确性（答案对不对）不在四维度范围：属评测卡 / Golden 任务集，归模型选型评测项目；本批次只评"技术完成"。
- 任务技术完成率、会话重生成率、幻觉参数率、并行工具调用率、工具调用-产出关联度、子任务分解效率、上下文复用效率、降级工具替代率经 2026-09-14 评审移出本批次。如需恢复按仓库规则重新立项并补充规格。
- 循环检测（A→B→A 环）暂不设指标：与 per_tool_repeat_call_count、7.2 重试代理口径高度重叠，待重试指标实践后评估增量。
- 编排深度 / worker 扇出画像暂缓：诊断价值大于独立指标价值，作为表外统计随既有指标输出。
- 证据来源与答案质量层 7 项候选已暂缓，规格存档于 `docs/specs/metrics_deferred.md`。
- 手册外工具调用率（out_of_catalog_tool_call_rate）在批次调整中未归档，规格暂缺；如需恢复按 metrics_deferred.md 格式补充。
- 以上候选指标全部未编码、未验收；每项进入实现前按仓库规则展示公式、字段映射、异常边界和模拟计算并等待确认。
