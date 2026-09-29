# Trace 日志结构

本文档记录当前可用的新版聊天模式与研究模式日志结构，作为后续 collector、统一 Trace schema
和指标字段映射的架构依据。本文只描述字段、类型、关系和安全边界，不复制真实 query、回答、
工具参数、工具结果、认证头或用户标识。

本文结论来自以下只读样本：

- 新版聊天模式：`data/raw/agent_trace_pro_20260828-20260903_20260904_171853.json`，
  336 条会话执行记录。
- 研究模式：`data/raw/msg_780b025aaf2a413a9b5d2b82bcdfeceb_*.json`，单次研究任务的
  380 个事件。
- 研究模式（2026-09-23 补充）：`data/processed/research_logs_rebuilt/`，47 个按
  `turn.start` 切分并合并续跑段后的逻辑回合，共 13,747 个事件。第 3.8 节的
  `tool.debug` 层级分析来自该样本（事件量是单条样本的 36 倍，才能观察到跨 callID、
  跨工具的形态分布）。

`data/raw/` 中的文件包含原始 Trace 和潜在敏感信息，只能用于本地分析，不得提交 Git。
本文记录的是观察到的结构，不代表上游已经承诺的稳定 schema；新增兼容逻辑时仍需版本化并
保留未知字段诊断。

## 1. 两种日志的总体模型

新版聊天模式是“完整记录数组”：

```text
JSON 数组
└── 一条用户请求的完整执行记录
    └── canvas
        ├── meta：会话元数据和聊天历史
        ├── agents：编排与子 Agent 执行
        └── answer：本轮最终回答
```

研究模式是“单次研究任务的事件流”：

```text
JSON 数组
├── request
├── turn.start
├── agent.mcp / agent.tools / agent.system
├── message.updated
├── message.part.updated
├── llm.response
├── tool.debug
└── turn.end
```

聊天模式的一个数组元素本身就是完整 Trace；研究模式必须按 `sessionID`、`userMessageID`、
`messageID`、`part.id` 和 `callID` 聚合多个事件后，才能恢复完整 Trace。

## 2. 新版聊天模式

### 2.1 文件与顶层结构

文件根节点是 JSON 数组，不是 JSONL。每个数组元素固定包含 `userId` 和 `canvas`：

```jsonc
[
  {
    "userId": "<redacted>",
    "canvas": {
      "meta": {},
      "agents": [],
      "answer": {}
    }
  }
]
```

正式读取器应支持流式解析大型 JSON 数组，避免文件增长后一次性加载全部内容。

### 2.2 `canvas.meta`

`canvas.meta` 保存会话、请求和历史消息：

```jsonc
{
  "traceId": "<trace-id>",
  "chatId": "<chat-id>",
  "sessionId": "<session-id>",
  "recordId": "<record-id>",
  "query": "<redacted>",
  "model": "<model-name>",
  "create_time": "YYYY-MM-DD HH:mm:ss",
  "message": [
    {"role": "user", "content": "<redacted>"},
    {"role": "assistant", "content": "<redacted>"}
  ],
  "tools": ["<tool-name>"]
}
```

已确认的语义：

- `query` 等于 `message` 中最后一条 `role == "user"` 的 `content`。
- `message` 是包含当前请求的聊天历史，但不包含本轮 `canvas.answer.answer`。
- `model` 是会话入口或编排模型，不一定是最终回答模型。
- `tools` 是当前请求可见的工具名称集合；样本中每条记录有 3 至 117 个工具。

### 2.3 会话与记录标识

样本中的唯一性和基数如下：

- `traceId`：336 个非空值，336 个唯一值；标识一次请求的完整执行 Trace。
- `sessionId`：336 个非空值，222 个唯一值；标识可包含多轮请求的会话。
- `chatId`：336 个非空值，335 个唯一值；不能作为绝对唯一执行主键。
- `recordId`：336 个非空值，335 个唯一值；不能作为绝对唯一执行主键。

`sessionId -> traceId` 是一对多关系：

- 172 个 session 包含 1 个 Trace。
- 50 个 session 包含多个 Trace，共覆盖 164 个 Trace。
- 单个 session 最多包含 11 个 Trace。
- 每个 session 只关联一个 `userId`。
- 每个 `traceId` 只关联一个 `sessionId`。

推荐标识：

```text
会话主键：sessionId
单次执行主键：traceId
导出记录定位：recordId，仅作辅助字段
```

样本中有一组 `chatId` 和 `recordId` 重复：两条记录属于同一 session，query 相同，
但 `traceId` 和最终回答不同。这可能表示同一请求的重新生成或重复执行，因此不得用
`chatId` 或 `recordId` 替代 `traceId`。

### 2.4 `canvas.agents`

`canvas.agents` 保存 orchestrator 和 worker 的执行片段：

```jsonc
{
  "agentId": "<agent-id>",
  "agentName": "<agent-name>",
  "round": 1,
  "messages": [
    {
      "role": "assistant",
      "content": {}
    }
  ]
}
```

样本包含 2,032 个 Agent 条目：

- 1,957 个条目具有非空 `agentId`，共 1,205 个唯一值。
- 75 个条目同时缺少 `agentId`、`agentName` 和 `round`，必须保留为匿名执行片段并记录
  `missing_agent_identity`，不得归并给相邻 Agent。
- 同一 Trace 内没有重复的非空 `agentId`；跨 Trace 可以重复。

观察到的 `agentName`：

```text
orchestrator
worker_search
worker_validation
worker_calculation
worker_multimodal
worker_text2image
```

#### Orchestrator

- 共 758 个 orchestrator 条目，但只有 6 个唯一 `agentId`。
- 这 6 个 ID 分别固定对应 `round` 1 至 6，并会跨多个 Trace 复用，因此 orchestrator 的
  `agentId` 表示编排层级身份，不是单次调用 ID。
- 所有 orchestrator 条目都有 `round`。
- 推荐使用 `traceId + agentId + round` 标识一轮 orchestrator 执行。

单条 Trace 中的 orchestrator round 始终从 1 开始且连续递增，未观察到跳层：

```text
(1)
(1, 2)
(1, 2, 3)
(1, 2, 3, 4)
(1, 2, 3, 4, 5)
(1, 2, 3, 4, 5, 6)
```

样本中的层数分布：

```text
0 层：75 条 Trace
1 层：6 条 Trace
2 层：86 条 Trace
3 层：112 条 Trace
4 层：45 条 Trace
5 层：8 条 Trace
6 层：4 条 Trace
```

当前采用的执行模型是：每层 orchestrator 独立完成本轮规划或汇总，可调用多个 worker；worker
通过 `taskId -> agentId` 返回独立执行结果，后续 orchestrator round 再基于上游结果继续编排。
结构能够确认连续层级和调用关系，但原始传导正文仍属于敏感内容，不进入规范化结果。

#### Worker

- 共 1,199 个 worker 条目，每个 worker 的 `agentId` 在样本中唯一。
- 每个父 Agent 的子 Agent 调用都含唯一 `taskId`。
- 1,199 个 `tool_call.taskId` 全部与同一 Trace 内的 worker `agentId` 精确匹配。
- `tool_call.agentName` 也全部与对应 worker `agentName` 匹配。

因此 worker 调用关系为：

```text
父 Agent tool_call.taskId
          =
子 Agent agents[].agentId
```

推荐使用 `traceId + taskId`，或等价的 `traceId + worker agentId`，标识一次子 Agent 执行。

### 2.5 Agent 消息与 `role`

`canvas.agents[].messages[]` 中的 5,358 条消息全部为：

```json
{"role": "assistant", "content": {}}
```

这里的 `assistant` 只表示消息来自模型或 Agent 执行侧，不表示最终回答、主 Agent、成功状态
或自然语言文本。`content` 可能包含：

- `durationSeconds`
- `usageTokens`
- `tool_call`
- `tool_result`
- 上述字段的部分组合
- 空对象

工具结果嵌套在 `assistant` 消息的 `content.tool_result` 中，没有单独的 `role == "tool"`
消息。最终回答必须读取 `canvas.answer.answer`。

### 2.6 普通工具调用与子 Agent 调用

`content.tool_call[]` 同时承载两种不同语义。

普通工具调用：

```jsonc
{
  "taskId": "<owning-task-id>",
  "toolId": "<tool-call-id>",
  "toolName": "<tool-name>",
  "arguments": {"<field>": "<redacted>"}
}
```

子 Agent 调用：

```jsonc
{
  "taskId": "<child-agent-task-id>",
  "agentName": "<worker-name>",
  "arguments": {"<field>": "<redacted>"}
}
```

分类规则：

```text
存在 toolId/toolName：普通工具调用
存在 agentName 且不存在 toolId：子 Agent 调用
```

样本包含：

- 4,266 个普通工具调用，4,266 个非空且唯一的 `toolId`。
- 1,199 个子 Agent 调用，1,199 个非空且唯一的 `taskId`。

普通工具调用推荐使用 `traceId + toolId` 作为跨导出批次的安全复合键。

### 2.7 MCP 工具识别

`agentName` 和 `agentId` 都不能映射 MCP 工具。6 个 Agent 名称与
`mcp_tool_manual.json` 中 77 个 `tools[].name` 的精确交集为 0。

MCP 工具必须通过以下路径识别：

```text
content.tool_call[].toolName
          ->
mcp_tool_manual.json.tools[].name
```

样本中共有 86 种 `toolName`：

- 50 种与 MCP 手册精确匹配。
- 36 种未精确匹配，包含内部编排、文件操作、运行环境工具和可能的命名别名。
- 4,266 次工具调用中有 2,911 次精确匹配 MCP 手册，约占 68.24%。

不得把所有 `toolName` 都视为 MCP。别名只能在人工确认后写入版本化别名目录，不能使用
模糊字符串猜测，例如 snake_case 与 camelCase 相似并不自动证明是同一工具。

### 2.8 工具结果和请求关联

工具结果位于 `content.tool_result[]`，存在以下形态：

```jsonc
{"toolId": "<id>", "toolName": "<name>", "durationSeconds": 0.1, "result": {}},
{"toolId": "<id>", "toolName": "<name>", "durationSeconds": 0.1, "rawResult": []},
{"toolId": "<id>", "toolName": "<name>", "result": {}},
{"toolId": "<id>", "toolName": "<name>"},
{"toolId": "<id>"}
```

样本共有 4,662 个工具结果：

- 1,854 个包含 `rawResult + durationSeconds`。
- 1,589 个包含 `result + durationSeconds`。
- 180 个包含 `result`，但缺少 `durationSeconds`。
- 314 个只有 `toolId + toolName`。
- 725 个只有 `toolId`。

共 3,443 个结果具有 `durationSeconds`，覆盖率约为 73.85%。

请求和结果通常位于同一 message 的两个数组中。4,248 个请求可在请求数与结果数相同的
message 内按位置关联，其中：

- 3,523 对的 `toolId` 和 `toolName` 都一致。
- 725 对的 `toolId` 一致，但结果侧缺少 `toolName`。

另有只包含 `tool_result`、不包含 `tool_call` 的消息。规范化时应：

1. 优先在同一 message 内按位置关联并校验 `toolId`。
2. 结果缺少 `toolName` 时可从已匹配请求补充，但要记录诊断计数。
3. 对独立结果消息，在同一 Trace 和任务范围内按 `toolId` 回溯。
4. 无法唯一关联时返回稳定原因码，不得猜测。

### 2.9 时间和 Token

聊天记录保留以下绝对时间：

- `canvas.meta.create_time`
- `canvas.answer.create_time`

工具请求和结果没有独立 `create_time`，但部分对象提供相对耗时：

- 4,946 条 Agent message 有 `durationSeconds`。
- 3,443 个工具结果有 `durationSeconds`。
- 335/336 个最终回答有 `durationSeconds`。

#### 耗时层级与确认口径

日志中只有三个 `durationSeconds` 存储路径：

```text
canvas.answer.durationSeconds
canvas.agents[].messages[].content.durationSeconds
canvas.agents[].messages[].content.tool_result[].durationSeconds
```

以下路径不存在：

```text
canvas.durationSeconds
canvas.agents[].durationSeconds
content.tool_call[].durationSeconds
```

当前确认口径如下：

1. `message.content.durationSeconds` 是当前 Agent 消息或步骤自己的耗时。
2. orchestrator 的该字段只表示本 orchestrator round 自身耗时，与其调用的 worker 耗时独立。
3. worker 的该字段表示 worker 内某条消息或步骤耗时，不是显式的 worker 总耗时。
4. `tool_result.durationSeconds` 表示单次工具执行耗时。
5. `canvas.answer.durationSeconds` 是终止 Agent 消息耗时的复制，不是独立的 Trace 总耗时。

每个 orchestrator 条目固定只有一条 message；758 个 orchestrator 条目中 757 个具有
`message.content.durationSeconds`。因此每个非缺失的 orchestrator round 都有一个自身步骤耗时，
但没有包含全部下游 worker 的 orchestrator 聚合总耗时字段。

该独立性得到以下数据验证：在 498 个实际调用 worker 的 orchestrator round 中，orchestrator
自身耗时与子 worker 消息耗时之和相等的记录为 0，与最长子 worker 消息耗时相等的记录也为
0。部分 orchestrator 耗时小于其子 worker 的最长耗时，因此不能把它解释为覆盖完整子树的
墙钟时间。

同样，Agent message 耗时和工具结果耗时不能进行包含关系推断。在 3,234 条单请求、单结果且
两侧都有耗时的消息中：

```text
message duration == tool duration：0
message duration > tool duration：2779
message duration < tool duration：455
```

因此禁止使用以下计算：

```text
模型纯耗时 = message duration - tool duration
Agent 总耗时 = sum(message duration) + sum(tool duration)
Orchestrator 总耗时 = orchestrator duration + sum(worker duration)
```

这些字段应作为彼此独立的观测保存。若需要汇总 worker 已观察步骤，可输出明确命名的
`observed_agent_step_duration_sum`，但不得把它标记为墙钟总耗时。

#### 最终回答耗时不是端到端耗时

335 条具有 `canvas.answer.durationSeconds` 的记录中，该值全部能在某条 Agent message 中找到
完全相同的值。在存在 orchestrator 的 260 条记录中：

```text
canvas.answer.durationSeconds
  == 最后一个 orchestrator round 的 message.content.durationSeconds：260/260

canvas.answer.usageTokens
  == 最后一个 orchestrator round 的 message.content.usageTokens：260/260
```

因此 `canvas.answer` 的耗时和 Token 是终止 Agent 消息元数据的复制，聚合时必须去重。

聊天模式没有显式 Trace 总耗时字段。可候选派生：

```text
trace_wall_clock_duration
  = canvas.answer.create_time - canvas.meta.create_time
```

该差值是秒级时间戳推导值，不等于 `canvas.answer.durationSeconds`；样本中二者中位数分别为
84 秒和约 42.26 秒，335 条中只有 31 条相差不超过 1 秒。使用该派生值前，应保留秒级精度和
端点语义诊断。

`tool_result.durationSeconds` 是独立的工具执行耗时观测，可用于超时规则；聊天日志不提供工具
决策节点的独立绝对时间，不能从消息耗时或数组顺序反推。

Token 字段为：

```json
{
  "input_tokens": 0,
  "output_tokens": 0,
  "cache_tokens": 0
}
```

- 4,922 条 Agent message 有完整三字段 Token。
- 311/336 个最终回答有 Token。

Token 消耗以 Agent message 为模型步骤：4,946 条具有 `durationSeconds` 的模型步骤中，4,922 条
具有合法 input/output Token，24 条缺失 Token；另有 411 条仅含工具结果的消息，不属于新的
模型调用。312/336 条 Trace 的模型步骤 Token 覆盖完整。orchestrator 与 worker 是独立模型
步骤，可以累加；`canvas.answer.usageTokens` 在当前 311 条观测中都能匹配某条 Agent message，
属于重复快照，不得再次计数。总 Token 按确认口径只累加 `input_tokens + output_tokens`；缓存
比率则按已确认的 `cache_tokens / input_tokens` 计算，因为聊天 `input_tokens` 已包含缓存 Token。

### 2.10 `canvas.answer`

```jsonc
{
  "answer": "<redacted>",
  "model": "<final-model>",
  "create_time": "YYYY-MM-DD HH:mm:ss",
  "durationSeconds": 0.1,
  "usageTokens": {
    "input_tokens": 0,
    "output_tokens": 0,
    "cache_tokens": 0
  }
}
```

- `answer`、`model` 和 `create_time` 在 336 条记录中全部存在。
- 1 条 `answer` 是空字符串。
- 1 条缺少 `durationSeconds`。
- 25 条缺少 `usageTokens`。
- 只有 66/336 条记录的 `canvas.meta.model` 与 `canvas.answer.model` 相同，因此最终回答模型
  必须读取 `canvas.answer.model`。

## 3. 研究模式

### 3.1 文件与事件总览

研究日志根节点是 JSON 数组，当前样本包含 380 个事件，并按 `ts` 非递减排列。首事件为
`request`，末事件为 `turn.end`。

事件数量：

```text
request                 1
turn.start              1
agent.mcp               1
agent.tools             1
agent.system            1
message.updated       109
message.part.updated  189
llm.response           54
tool.debug             22
turn.end                1
```

除 `request` 外，事件通常具有：

```jsonc
{
  "type": "<event-type>",
  "ts": "<timestamp>",
  "sessionID": "<session-id>"
}
```

本样本只有一个非空 `sessionID` 和一个 `userMessageID`。`request` 没有顶层 `sessionID`，
但 `body.sessionId` 与后续事件的 `sessionID` 一致。注意聊天模式使用 `sessionId`，研究模式
主要使用 `sessionID`，大小写不同。

### 3.2 `request`

`request` 是原始入口请求快照：

```jsonc
{
  "type": "request",
  "ts": "<timestamp>",
  "method": "POST",
  "path": "<request-path>",
  "headers": {"<header>": "<redacted>"},
  "body": {
    "sessionId": "<session-id>",
    "messageID": "<user-message-id>",
    "query": "<redacted>",
    "parts": [{"type": "text", "text": "<redacted>"}],
    "model": {"providerID": "<provider>", "modelID": "<model>"},
    "sourceChannel": "<channel>",
    "enabledMcpUrls": [],
    "enabledSkillList": []
  }
}
```

安全边界：`headers` 和 `body.enabledMcpUrls[].*.headers` 中存在认证、用户、MCP key、网络与
内部追踪字段。collector 不得把这些对象整体复制到规范化结果、异常消息、日志或报告中。
只允许提取经过白名单批准的非敏感计数和名称，例如启用 MCP 服务数量、启用工具名称和技能
数量。URL、key、uid、IP、内部路径和请求头原文不得持久化。

### 3.3 `turn.start`

```jsonc
{
  "type": "turn.start",
  "ts": "<timestamp>",
  "sessionID": "<session-id>",
  "userMessageID": "<message-id>",
  "query": "<redacted>"
}
```

这是研究回合的稳定起点。端到端时延应优先使用该事件的 `ts`，而不是
HTTP `request.ts`。

### 3.4 Agent 环境事件

`agent.mcp` 记录 MCP 服务与工具范围：

```jsonc
{
  "type": "agent.mcp",
  "mcp": [
    {
      "name": "<server-name>",
      "type": "<server-type>",
      "status": "<status>",
      "url": "<redacted>",
      "tools": ["<tool-name>"]
    }
  ]
}
```

当前样本有 5 个 MCP 服务条目，共观察到 94 个工具名称观测。URL 属于环境信息，不应进入
指标结果。

`agent.tools` 记录 Agent 可见的总工具集合，当前样本为 116 个名称。该集合可能同时包含
MCP、内置工具和运行环境工具，不能直接作为 MCP 工具目录。

`agent.system` 包含系统提示或系统上下文字符串，属于敏感模型上下文，只能在内存中用于明确
授权的语义评估，不得持久化正文。

### 3.5 `message.updated`

`message.updated` 是消息对象的重复状态快照：

```jsonc
{
  "type": "message.updated",
  "ts": "<timestamp>",
  "sessionID": "<session-id>",
  "info": {
    "id": "<message-id>",
    "sessionID": "<session-id>",
    "parentID": "<parent-message-id>",
    "role": "user | assistant",
    "agent": "orchestrator",
    "mode": "orchestrator",
    "providerID": "<provider>",
    "modelID": "<model>",
    "time": {"created": 0, "completed": 0},
    "tokens": {
      "input": 0,
      "output": 0,
      "reasoning": 0,
      "total": 0,
      "cache": {"read": 0, "write": 0}
    },
    "finish": "tool-calls | stop"
  }
}
```

样本有 109 个更新事件，但只有 28 个唯一 `info.id`：

- 27 个唯一 assistant 消息。
- 1 个唯一 user 消息。
- 26 个 assistant 消息最终以 `tool-calls` 结束。
- 最后一个 assistant 消息最终以 `stop` 结束。

同一 `info.id` 会出现多次，表示同一消息从创建到完成的不同快照。必须按 ID 合并，不得把
109 个事件当作 109 条独立消息。

研究模式中的 `role`：

- `user`：用户请求消息。
- `assistant`：orchestrator 的模型消息。
- 工具角色不在此事件中，而是在 `llm.response.result.messages[].role == "tool"` 中出现。

### 3.6 `message.part.updated`

`message.part.updated` 是消息内容片段的生命周期快照。公共字段：

```jsonc
{
  "type": "message.part.updated",
  "ts": "<timestamp>",
  "sessionID": "<session-id>",
  "time": 0,
  "part": {
    "id": "<part-id>",
    "messageID": "<message-id>",
    "sessionID": "<session-id>",
    "type": "<part-type>"
  }
}
```

189 个事件对应 106 个唯一 `part.id`，所有 `part.messageID` 都能匹配
`message.updated.info.id`。同一 part 可能多次更新，必须按 `part.id` 合并。

观察到的 part 类型：

```text
tool         90 个更新事件
step-start   27
step-finish  27
text         23
reasoning    22
```

#### 文本与推理片段

```jsonc
{
  "id": "<part-id>",
  "messageID": "<message-id>",
  "type": "text | reasoning",
  "text": "<redacted>",
  "time": {"start": 0, "end": 0},
  "metadata": {}
}
```

`text` 和 `reasoning` 正文不得写入指标结果、诊断日志或提交文件。最终回答应选择
`turn.end` 前最后一个 assistant 消息的最终非空 `text` part，并先按 `part.id` 合并更新。

#### 步骤边界

`step-start` 和 `step-finish` 各有 27 个，对应 27 个模型循环。`step-finish` 可包含：

- `reason`
- `cost`
- `tokens.input/output/reasoning/total`
- `tokens.cache.read/write`

这些 Token 可能与 `message.updated.info.tokens` 和 `llm.response.result.usage` 重复表达同一次
模型调用，不能跨层直接相加。

Token 消耗以按 `messageID` 去重后的 `step-finish.tokens.total` 为权威来源；
`message.updated.info.tokens` 和 `llm.response.result.usage` 不参与计算，cache 分项不单独处理。

平均模型步骤时延应按 `messageID` 配对 `step-start` 与 `step-finish`，使用事件数值型 `time`
计算；两种 part 的 `part.id` 不同，不能按 part ID 配对。事件 `ts` 只用于边界差异诊断。
`message.updated.info.time.created/completed` 覆盖更宽的消息生命周期，不作为步骤时延边界。

#### 工具 part

```jsonc
{
  "id": "<part-id>",
  "messageID": "<message-id>",
  "callID": "<tool-call-id>",
  "type": "tool",
  "tool": "<tool-name>",
  "state": {
    "status": "pending | running | completed",
    "input": {"<field>": "<redacted>"},
    "output": "<redacted>",
    "raw": "<redacted>",
    "title": "<redacted>",
    "tool_type": "<type>",
    "time": {"start": 0, "end": 0},
    "metadata": {}
  }
}
```

样本有 30 个唯一工具调用。每个调用都严格出现三个状态：

```text
pending -> running -> completed
```

因此 30 个调用产生 90 个 tool part 更新。生命周期去重和聚合必须使用 `callID`，不得按事件
条数统计调用次数。

时间覆盖：

- 60 个状态快照有 `state.time.start`。
- 30 个 completed 快照有 `state.time.end`。
- 完整调用时延应使用同一 `callID` 的最终状态时间，或直接使用 completed 快照中确认过的
  `start/end`。

`state.input`、`state.output`、`state.raw` 和证据 `chunkList` 可能包含用户数据、查询、文档
内容和来源正文，只能在内存中按指标最小范围处理。

### 3.7 `llm.response`

```jsonc
{
  "type": "llm.response",
  "ts": "<timestamp>",
  "sessionID": "<session-id>",
  "userMessageID": "<message-id>",
  "loop": 1,
  "source": "processor",
  "result": {
    "finishReason": "tool-calls | stop",
    "reasoningText": "<redacted>",
    "text": "<redacted>",
    "messages": [],
    "toolCalls": [
      {
        "toolCallId": "<call-id>",
        "toolName": "<tool-name>",
        "input": {"<field>": "<redacted>"}
      }
    ],
    "usage": {}
  }
}
```

样本有 27 个模型循环，但每个 `loop` 出现两个 `llm.response` 事件：

- 一个有 `source == "processor"`。
- 一个没有 `source`。
- 两种事件使用不同的 `usage` schema。

因此 54 个事件不是 54 次独立模型调用。`result.toolCalls` 共观察到 60 次，但只有 30 个唯一
`toolCallId`；这 30 个 ID 全部与 `message.part.updated.part.callID` 对应。

`result.messages[].role` 包含：

- `assistant`：54 个观测。
- `tool`：26 个观测。

`result.messages[].content[].type` 包含：

- `tool-call`
- `tool-result`
- `text`
- `reasoning`

该结构比 `message.updated` 更接近供应商或模型协议原始响应，正文和工具输入输出不得持久化。
模型调用和 Token 统计必须按 `loop` 或其他经确认的单调调用键去重，不能把两个投影重复相加。

### 3.8 `tool.debug`

`tool.debug` 记录**一次工具调用内部的 API 请求明细**。它是研究模式唯一能观察到工具实现的
通道，也是理解 `callID` 与 `requestId` 两级结构的关键。

```jsonc
{
  "type": "tool.debug",
  "ts": "<timestamp>",
  "sessionID": "<session-id>",
  "messageID": "<message-id>",
  "callID": "<tool-call-id>",
  "tool": "<tool-name>",
  "debug": {
    "apiCallCount": 0,
    "apiCalls": [
      {
        "endpoint": "<api-path>",
        "httpStatus": 200,
        "request": {"<field>": "<redacted>"},
        "response": {"<field>": "<redacted>"}
      }
    ],
    "requestIdCount": 0,
    "requestIds": [],
    "requestId1": "<request-id>",
    "debug": {}
  }
}
```

#### 3.8.1 三级结构与两级标识

工具调用不是单次 HTTP 请求，而是"一次逻辑调用 → 多次物理请求"的复合结构：

```text
assistant 消息 (message.updated.info.id / part.messageID)   ← 模型一次输出
  └── 工具调用 (part.callID / tool.debug.callID)             ← 模型决定的逻辑单位，工具名在此层
        └── API 请求 (apiCalls[].request.requestId)          ← 工具实现内部的物理 HTTP
```

- **`callID` 是"每次工具调用"独立，不是"每条消息"一个。** 一个 `messageID` 可对应多个
  `callID`（模型一条消息并行发出多个工具调用）；实测 `callID -> tool` 严格一对一。
  `callID` 形如 `toolu_vrtx_...`（Vertex）/ `toolu_bdrk_...`（Bedrock），是**模型供应商在
  生成 tool_use 时产生的 ID**，不是应用层编号。
- **`requestId` 是"该次工具调用内部的第 N 个 HTTP 请求"。** 单次工具调用内部常需要多次
  REST 往返才能完成；例如 `read` 在 Agent 语义上是"读一个文件"一步，内部却先按路径换节点
  ID、再读文档内容，共 2 次请求。
- **`requestId` 格式为 `req-<uuid>-<N>`，其中 uuid 是一次工具调用内部的会话标识。**
  实测（362 个含 apiCalls 的 callID）：同一 callID 内含多个**不同** uuid 的为 **0**，
  一个 uuid 对应多个 callID 的也为 **0** —— uuid 与 `callID` **严格一一对应**，
  可视为 callID 在 HTTP 层的投影；`N` 是该次调用内请求的先后序号，从 1 起递增。
  实测序号序列形态：`(1,2)` 60 次、`(1,2,3,4)` 36 次、`(1,2,3)` 14 次、`(1,2,3,4,5)` 2 次、
  `(1..7)` 1 次、`(1,1)` 1 次（后者为同一请求被投影两次，见 3.8.4）。

**`N` 不是重试计数。** 相邻两个请求（同一 callID 内 `N` 与 `N+1`）绝大多数在干不同的事。
相邻请求入参不同的比例：**本地 47 回合样本实测 80.0%**（210 对相邻请求中 168 对指纹不同），
权威口径 7.8 记录的全量 3,586 条含重试日志为 **83.8%**——两者是不同总体，量级一致。
说明多数字号对应**工作流步骤**（查路径 → 建文件 → 读文档），而非重发。任何把 `N ≥ 2`
当作重试的口径都会显著高估（实测高估约 3 倍），详见权威口径 7.8 与
`初步入库评测指标.md` 7.8 节。

注意相邻请求中 endpoint 相同的仍有 32.9%（69/210）——**同一 endpoint 的连续调用不等于重发**，
这也是指纹必须由"endpoint + 入参"共同决定、不能只看 endpoint 的原因。

#### 3.8.2 endpoint 穷举

样本出现的 endpoint 全部属于同一个内部文档服务，共 9 个：

```text
   298  /json/inner-research-model/node-id-by-full-path      路径换取节点 ID
    89  /json/inner-research-model/yjs-context-read          读取文档内容
    68  /json/inner-research-model/node-path-glob            路径匹配
    38  /json/inner-research-model/node-upload-by-full-path  上传文件
    27  /json/inner-research-model/node-delete               删除节点
    25  /json/inner-research-model/context-read              读取上下文
    22  /json/inner-research-model/node-create-yjs-file      创建文件
     4  /json/inner-research-model/node-rename               重命名
     2  /json/inner-research-model/node-create-folder        创建目录
```

`endpoint` 必须参与任何"同一请求"的指纹计算，否则同入参不同接口会被误判为重复。

#### 3.8.3 规模与覆盖

47 个重建逻辑回合共 13,747 个事件，其中 `tool.debug` **478 条**：

- 363 条 `debug.apiCalls` 为列表（共 573 条 apiCalls、去重后 572 条唯一 `(callID, requestId)`）。
- 115 条**无 `apiCalls` 列表**，其 `debug` 只含 `requestIds` / `requestId1..N` /
  `requestIdCount` 与内层 `debug`（`backend` 为 `cloud` 或 `local`、文档块信息）。这些事件
  **不携带请求明细**，不能参与重试分子分母；按 7.8 契约属于 `no_observable_api_calls`
  之外的独立形态，读取器必须区分"有事件无明细"与"无事件"。
- 出现 `tool.debug` 的工具：`edit` 115、`read` 108、`excel` 57、`word` 57、`cp` 48、
  `glob` 42、`rm` 28、`write` 10、`grep` 6、`rename` 4、`mkdir` 2、`docdna` 1。
- 含 `tool.debug` 的 callID 共 477 个，但并非所有工具调用都有 debug 事件（`callID` 全集
  实测 1,299 个）。**debug 只能作为可选诊断来源，不能作为工具调用分母**；覆盖率必须
  随指标值输出（7.8 的 `api_debug_coverage`）。

#### 3.8.4 投影与去重

同一条 `tool.debug` 事件可能被投影两次：实测 `turn_036` 事件 625 与 626 的
`(callID, requestId)` 完全相同。因此 573 条原始 apiCalls 对应 572 条唯一值，
**必须按 `(callID, requestId)` 去重**，重复投影进诊断计数。

#### 3.8.5 已知缺口：跨 callID 的重发不可判定

现有字段**无法区分**下列两种情况：

```text
同一工具 + 同一入参，出现在不同 callID
  ├── 框架整调用重发（真重试）
  └── 模型在不同步骤合法地重复读同一文件 / 重复 grep 同一 pattern（正常工作）
```

`tool.debug.tool` 字段存在（478/478 全有）且与 `part.tool` 一致，但按工具名跨 callID 分组
会把合法重复一并计入（实测该口径给出约 39.8%，明显高于保守下界 7.7%）。要区分二者需要
联合 `ts` 与 `step-start` 判定"两次请求之间有没有新的模型步骤"，属未立项的新口径。

因此 7.8 当前采用**保守下界**：只统计同一 `callID` 内部的同指纹重复请求。

安全边界：`debug` 内含接口地址、请求 ID、文件路径、节点 ID、内部身份和响应元数据，属于
高敏感诊断内容。规范化结果最多保留白名单计数（如 `apiCallCount`）与经确认的聚合量
（如 endpoint 频次、业务错误码），不得保留完整 `request` / `response` 对象。


### 3.9 `turn.end`

```jsonc
{
  "type": "turn.end",
  "ts": "<timestamp>",
  "sessionID": "<session-id>",
  "userMessageID": "<message-id>",
  "agents": 1,
  "durationMs": 0
}
```

`turn.end` 是研究回合的稳定终点。研究模式端到端时延应优先使用其 `durationMs`，并以
`turn.start.ts` 到 `turn.end.ts` 的差值做一致性诊断，而不是混用 HTTP request 时间。

### 3.10 研究模式关联键

推荐关联关系：

```text
request.body.sessionId
          =
事件 sessionID

turn.start.userMessageID
          =
本轮用户消息标识

message.updated.info.id
          =
message.part.updated.part.messageID

llm.response.result.toolCalls[].toolCallId
          =
message.part.updated.part.callID
          =
tool.debug.callID（仅部分调用存在）
```

稳定去重键：

```text
消息：sessionID + info.id
消息片段：sessionID + part.id
工具调用：sessionID + callID
模型循环：sessionID + userMessageID + loop
研究回合：sessionID + userMessageID
API 请求：callID + requestId（tool.debug.debug.apiCalls[]，见 3.8）
```

## 4. 双模式字段对应

```text
语义                 聊天模式                         研究模式
会话                 canvas.meta.sessionId            sessionID / request.body.sessionId
单次请求             canvas.meta.traceId              userMessageID（当前最接近）
用户 query           canvas.meta.query                turn.start.query
聊天历史             canvas.meta.message              message.updated + message.part.updated
Agent 类型           agents[].agentName               message.updated.info.agent
Agent 执行实例       worker agentId / taskId          当前样本只有 orchestrator，未见同构 ID
工具调用 ID          tool_call[].toolId                part.callID / toolCallId
工具名称             tool_call[].toolName              part.tool / toolCalls[].toolName
工具输入             tool_call[].arguments             part.state.input / toolCalls[].input
工具结果             tool_result[].result/rawResult    part.state.output/raw / tool-result content
工具时延             tool_result[].durationSeconds     part.state.time.start/end
模型步骤时延         message.content.durationSeconds   step-start/step-finish 的 event.time
最终回答             canvas.answer.answer              turn.end 前最后 assistant text part
回合总时延           answer.create_time -               turn.end.durationMs
                     meta.create_time
Token                message.content.usageTokens       step-finish.tokens.total
```

研究模式当前没有与聊天模式 `traceId` 完全同构的明确字段。`userMessageID` 能标识本轮用户请求，
但正式统一 schema 前应使用派生 `trace_id`，并在 `source_trace_id` 中保留原始可用标识类型，
不得假装二者上游语义完全相同。

## 5. 推荐的统一内部模型

collector 不应让各指标分别理解两套原始结构。建议两种输入先转换为最小、只读、脱敏的统一
模型：

```jsonc
{
  "source_mode": "chat | research",
  "source_schema_version": "<detected-version>",
  "trace_id": "<stable-id>",
  "session_id": "<session-id>",
  "user_message_id": "<optional-id>",
  "started_at": "<optional-time>",
  "ended_at": "<optional-time>",
  "duration_ms": 0,
  "query_present": true,
  "final_answer_present": true,
  "agents": [
    {
      "agent_execution_id": "<optional-id>",
      "agent_name": "<optional-name>",
      "round": 0
    }
  ],
  "tool_calls": [
    {
      "call_id": "<stable-id>",
      "task_id": "<optional-id>",
      "tool_name": "<normalized-name>",
      "is_mcp": true,
      "request_observed": true,
      "terminal_observed": true,
      "started_at": "<optional-time>",
      "ended_at": "<optional-time>",
      "duration_ms": 0,
      "status": "pending | running | completed | failed | timed_out | unknown",
      "safe_diagnostics": {}
    }
  ],
  "token_usage": {
    "input": 0,
    "output": 0,
    "reasoning": 0,
    "cache_read": 0,
    "cache_write": 0,
    "aggregation_status": "computed | not_computable"
  },
  "normalization_diagnostics": {}
}
```

原始 query、回答、reasoning、arguments、result、rawResult、system prompt、认证头、URL、文件
路径和文档 chunk 正文不得进入该模型的持久化结果。需要语义 Judge 时，只能在受控内存边界内
按最小必要范围传递，并对异常做脱敏隔离。

## 6. 仍需上游确认的问题

### 聊天模式

- `chatId` 与 `recordId` 的正式业务语义，以及为什么存在同值多 Trace。
- 75 个匿名 Agent 条目的产生条件。
- 独立 `tool_result` message 与原请求的官方关联规则。

### 研究模式

- 同一 `loop` 的两个 `llm.response` 分别代表什么投影，哪个是计费权威来源。
- `message.updated.info.tokens`、`step-finish.tokens` 与 `llm.response.result.usage` 的包含关系。
- `userMessageID` 是否可被上游正式承诺为单次研究 Trace 标识。
- `part.time` 与 `part.state.time` 的单位和权威优先级。
- `turn.end.durationMs` 是否包含排队、网络请求和交付物写入时间。
- `tool.debug` 的长期保留策略，以及哪些安全计数字段允许进入评测诊断。
- `tool.debug` 中 115/478 条事件无 `apiCalls` 列表（只有 `requestIds` / `requestIdN`）：
  这些事件代表"工具内部无 HTTP 调用"还是"明细未上报"？两种语义对应不同的 7.8 分母口径。
- 跨 `callID` 的"同工具同入参"请求：框架整调用重发与模型合法重复工作，现有字段无法区分
  （见 3.8.5）；需要上游提供重发标记，或确认可用 `ts` + `step-start` 联合判定。

在这些问题确认前，缺少可靠字段或映射的指标必须返回稳定的 `NOT_COMPUTABLE` 原因，不能
使用近似值或通过重复事件推测真实调用次数。
