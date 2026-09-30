# Trace 日志结构

本文档记录当前可用的新版聊天模式与研究模式日志结构，作为后续 collector、统一 Trace schema
和指标字段映射的架构依据。本文只描述字段、类型、关系和安全边界，不复制真实 query、回答、
工具参数、工具结果、认证头或用户标识。

本文结论来自以下只读样本（2026-09-29 复核，全部重新普查）：

- 新版聊天模式：`data/raw/agent_trace_test_20260916-20260916_20260917_174050.json`，
  74 条完整执行记录。
- 研究模式（生产抽样）：`data/raw/oss_jsonl/`，51 个 `.jsonl` 文件、16,100 个事件，
  来自定时任务与人工会话。其中 47 个带 query 的文件与 `research_logs/` **逐事件完全相同**
  （实测 47/47 字节级一致），另 4 个 `_noquery_` 文件是续跑段的补充来源——两者是同一批
  日志的不同切片，不是独立样本。
- 研究模式（未合并单段）：`data/processed/research_logs/`，47 个原始单段文件，
  共 14,308 个事件。**这是保留子 Agent 侧会话的形态**，`agent.end` 只能在此观测。
- 研究模式（合并逻辑回合）：`data/processed/research_logs_rebuilt/`，47 条按 `turn.start`
  切分并合并续跑段后的逻辑回合，共 13,747 个事件。第 3.10 节的 `tool.debug` 层级分析、
  新增事件类型与 part 类型普查均来自该样本。**该目录已剔除侧会话**（见 3.1.1）。

三个研究样本是同一批日志的三种切法，差异见 3.1.2；引用任何研究侧事件计数前必须先声明读的
是哪个目录。

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
├── agent.start / agent.mcp / agent.tools / agent.system
├── message.updated
├── message.part.updated
├── llm.response
├── llm.error
├── tool.debug
├── agent.end
└── turn.end
```

聊天模式的一个数组元素本身就是完整 Trace；研究模式必须按 `sessionID`、`userMessageID`、
`messageID`、`part.id` 和 `callID` 聚合多个事件后，才能恢复完整 Trace。**注意一个文件里可能
混有多个 `sessionID`**：子 Agent 运行在自己的侧会话中，其事件与主会话事件落在同一个文件里，
却既不属于本轮用户请求、也没有 `turn.start`/`turn.end`。读取器必须先按主会话切分，否则会把
子 Agent 的模型循环与工具调用算进主 Trace（见 3.1.1）。

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

样本 74 条记录的顶层键集合完全一致（`{userId, canvas}`），`canvas` 三个键
（`meta` / `agents` / `answer`）在 74 条中全部存在。

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

- `query` 等于 `message` 中最后一条 `role == "user"` 的 `content`（74/74 精确相等）。
- `message` 是包含当前请求的聊天历史，但不包含本轮 `canvas.answer.answer`。
  样本中共 162 条历史消息（user 118 / assistant 44）。
- `model` 是会话入口或编排模型，不一定是最终回答模型。样本中 72 条为 `orchestrator`，
  2 条为具体模型名；与 `canvas.answer.model` 相同的只有 **2/74**。
- `tools` 是当前请求可见的工具名称集合；样本中每条记录 0 至 91 个工具（含 0，即空集合）。

### 2.3 会话与记录标识

样本中的唯一性和基数如下：

- `traceId`：74 个非空值，74 个唯一值；标识一次请求的完整执行 Trace。
- `sessionId`：74 个非空值，51 个唯一值；标识可包含多轮请求的会话。
- `chatId`：74 个非空值，69 个唯一值；不能作为绝对唯一执行主键。
- `recordId`：74 个非空值，69 个唯一值；不能作为绝对唯一执行主键。

`sessionId -> traceId` 是一对多关系：

- 40 个 session 只包含 1 个 Trace。
- 11 个 session 包含多个 Trace，单个 session 最多包含 6 个 Trace。
- 每个 session 只关联一个 `userId`。
- 每个 `traceId` 只关联一个 `sessionId`。

推荐标识：

```text
会话主键：sessionId
单次执行主键：traceId
导出记录定位：recordId，仅作辅助字段
```

`chatId` 与 `recordId` 各有 5 组重复（74 vs 69）。同一对重复值可能属于不同 session，
但 `traceId` 始终唯一，因此不得用 `chatId` 或 `recordId` 替代 `traceId`。

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

样本包含 345 个 Agent 条目，键形态只有三种：

```text
{agentId, agentName, round, messages}   158 个（全部是 orchestrator）
{agentId, agentName, messages}          180 个（全部是 worker，无 round）
{messages}                                7 个（匿名片段）
```

因此：

- 338 个条目同时具有非空 `agentId` 和 `agentName`。
- 7 个条目**只有 `messages` 键**，`agentId`、`agentName`、`round` 全部缺失，必须保留为匿名
  执行片段并记录 `missing_agent_identity`，不得归并给相邻 Agent。
- `round` 只在 orchestrator 条目上出现；187 个条目的 `round` 缺失。
- 同一 Trace 内没有重复的非空 `agentId`；跨 Trace 可以重复。

观察到的 `agentName`：

```text
orchestrator      158
worker_search     154
worker_validation  22
worker_calculation  4
（无 agentName）     7
```

与早期样本相比，本批没有出现 `worker_multimodal` / `worker_text2image`。worker 名称集合
可能随场景扩展，读取器不得按固定枚举校验。

#### Orchestrator

- 共 158 个 orchestrator 条目，对应 12 个唯一 `agentId`。
- 这 12 个 ID 是**可读的层级名**（形如 `主控L1` … `主控L12`），不是 UUID；每个 ID 固定
  对应一个 `round`，并会跨多个 Trace 复用。因此 orchestrator 的 `agentId` 表示编排层级身份，
  不是单次调用 ID。
- 所有 orchestrator 条目都有 `round`。
- 推荐使用 `traceId + agentId + round` 标识一轮 orchestrator 执行。

单条 Trace 中的 orchestrator round 始终从 1 开始且连续递增，未观察到跳层。样本中的层数分布：

```text
1 层：67 条 Trace
2 层：52
3 层：23
4 层：7
5 层：2
6 层：1
7 层：1
8 层：1
9 层：1
10 层：1
11 层：1
12 层：1
```

当前采用的执行模型是：每层 orchestrator 独立完成本轮规划或汇总，可调用多个 worker；worker
通过 `taskId -> agentId` 返回独立执行结果，后续 orchestrator round 再基于上游结果继续编排。
结构能够确认连续层级和调用关系，但原始传导正文仍属于敏感内容，不进入规范化结果。

#### Worker

- 共 180 个 worker 条目，每个 worker 的 `agentId` 在样本中唯一。
- 每个父 Agent 的子 Agent 调用都含唯一 `taskId`。
- 180 个 `tool_call.taskId` **全部**与同一 Trace 内的 worker `agentId` 精确匹配。

因此 worker 调用关系为：

```text
父 Agent tool_call.taskId
          =
子 Agent agents[].agentId
```

推荐使用 `traceId + taskId`，或等价的 `traceId + worker agentId`，标识一次子 Agent 执行。

子 Agent 调用的 `arguments` 键观察值：`query`（176）、`task`（4）、`data`（4）。
任务正文属敏感内容，只在内存中用于子任务冗余判定。

### 2.5 Agent 消息与 `role`

`canvas.agents[].messages[]` 中的 800 条消息**全部**为：

```json
{"role": "assistant", "content": {}}
```

这里的 `assistant` 只表示消息来自模型或 Agent 执行侧，不表示最终回答、主 Agent、成功状态
或自然语言文本。`content` 可能包含：

- `durationSeconds`（640 条）
- `usageTokens`（639 条）
- `tool_call`（567 条）
- `tool_result`（635 条）
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

- 785 个普通工具调用，785 个非空且唯一的 `toolId`。
- 180 个子 Agent 调用，180 个非空且唯一的 `taskId`。
- 两类调用都带 `arguments`（965 个对象），但只有普通调用带 `toolId`/`toolName`。

普通工具调用推荐使用 `traceId + toolId` 作为跨导出批次的安全复合键。

注意 `respond` 也出现在 `toolName` 中（17 次）。它是 Agent 返回通道，不是外部工具；
工具类指标必须显式排除（见 `AGENTS.md` 的已知缺口与各 collector 的 `respond` 排除逻辑）。

### 2.7 MCP 工具识别

`agentName` 和 `agentId` 都不能映射 MCP 工具。5 个实际出现的 Agent 名称与
`mcp_tool_manual.json` 中 `tools[].name` 的精确交集为 0。

MCP 工具必须通过以下路径识别：

```text
content.tool_call[].toolName
          ->
mcp_tool_manual.json.tools[].name
```

样本中共有 39 种 `toolName`：

- 27 种与 MCP 手册精确匹配。
- 12 种未精确匹配，包含内部编排、文件操作、运行环境工具和可能的命名别名
  （`finance_query_agent` / `searchInternet` / `get_index_kline` / `respond` /
  `get_nl2sql_result` / `annual_report_extract` / `run_code` / `formula_selector` /
  `sector_data` / `searchImage` / `bond_basic_info` / `searchIndustry_indicators`）。
- 785 次工具调用中有 648 次精确匹配 MCP 手册，占 82.55%。

不得把所有 `toolName` 都视为 MCP。别名只能在人工确认后写入版本化别名目录，不能使用
模糊字符串猜测：样本中 `searchIndustry_indicators`（1 次调用）与手册里真实存在的
`search_industry_indicators` 只差一个字符 **`I`/`i` 的大小写**，本次调用即返回
`Tool not found`（见 2.8）。这既不是别名也不是缺失工具，而是**模型把名字拼错了**——
若用大小写不敏感的相似度自动归并，会把这次真实的模型误用洗成"合法调用"。

### 2.8 工具结果和请求关联

工具结果位于 `content.tool_result[]`。样本 944 个结果的键集合：

```text
toolId            944（全部）
success           944（全部，布尔）
toolName          927
durationSeconds   767
rawResult         536
result            253
error               1（字符串，仅出现于 success=false 的那一条）
```

`result` 与 `rawResult` 互斥：`(success, hasResult, hasRawResult)` 的观测组合为
`(True,True,False)=253`、`(True,False,True)=536`、`(True,False,False)=154`、
`(False,False,False)=1`。

**`success` 是比"结果载荷能否解包"更直接的终态信号**，但覆盖面与语义仍需谨慎：

- 样本 943/944 为 `true`，1 条为 `false`，该条同时带 `error` 字符串
  （`Tool '<name>' not found`）且无 `result`/`rawResult`。
- 154 条 `success=true` 但既无 `result` 也无 `rawResult`——成功不等于有载荷。
- 聊天侧不能只依赖 `success` 判定失败：样本里唯一的失败恰好带了 `error` 文本，
  但目录级结论需要更大样本验证，故 `tool_error_rate` 的聊天实现仍以
  `payload_proxy_v1` 载荷代理为主，`success=false` 只作入口级兜底。

共 767 个结果具有 `durationSeconds`，覆盖率 81.2%。

请求和结果关联：

- 476 条消息的 `tool_call` 与 `tool_result` 数量相同，按位置关联可得 785 对；
  其中 785 对 `toolId` 一致，768 对 `toolName` 也一致。
- 另有 159 条消息只包含 `tool_result`、不包含 `tool_call`。

规范化时应：

1. 优先在同一 message 内按位置关联并校验 `toolId`。
2. 结果缺少 `toolName` 时可从已匹配请求补充，但要记录诊断计数。
3. 对独立结果消息，在同一 Trace 和任务范围内按 `toolId` 回溯。
4. 无法唯一关联时返回稳定原因码，不得猜测。

### 2.9 时间和 Token

聊天记录保留以下绝对时间：

- `canvas.meta.create_time`
- `canvas.answer.create_time`

工具请求和结果没有独立 `create_time`，但部分对象提供相对耗时：

- 640 条 Agent message 有 `durationSeconds`。
- 767 个工具结果有 `durationSeconds`。
- 73/74 个最终回答有 `durationSeconds`。

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

该独立性得到数据验证：在 142 个本 Trace 内既有 orchestrator 自身耗时、又有 worker 消息
耗时的 round 中，orchestrator 耗时与子 worker 耗时之**和**相等的为 **0**，与最长子 worker
消息耗时相等的也为 **0**；且有 93 个 round 的 orchestrator 耗时**小于**其子 worker 的最长耗时。
因此不能把它解释为覆盖完整子树的墙钟时间。

同样，Agent message 耗时和工具结果耗时不能进行包含关系推断。在 767 对两侧都有耗时的
消息内配对中：

```text
message duration == tool duration：0
message duration > tool duration：693
message duration < tool duration：74
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

73 条具有 `canvas.answer.durationSeconds` 的记录中，有 orchestrator 的 66 条里：

```text
canvas.answer.durationSeconds
  == 最后一个 orchestrator round 的 message.content.durationSeconds：66/66

canvas.answer.usageTokens
  == 最后一个 orchestrator round 的 message.content.usageTokens：66/66
```

因此 `canvas.answer` 的耗时和 Token 是终止 Agent 消息元数据的复制，聚合时必须去重。

聊天模式没有显式 Trace 总耗时字段。可候选派生：

```text
trace_wall_clock_duration
  = canvas.answer.create_time - canvas.meta.create_time
```

该差值是秒级时间戳推导值，不等于 `canvas.answer.durationSeconds`；样本中二者中位数分别为
94 秒和约 24.52 秒，74 条中只有 30 条相差不超过 1 秒。使用该派生值前，应保留秒级精度和
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

- 639 条 Agent message 有完整三字段 Token（无一缺分项）。
- 72/74 个最终回答有 Token。

Token 消耗以 Agent message 为模型步骤：640 条具有 `durationSeconds` 的模型步骤中，639 条
具有合法 input/output Token。`canvas.answer.usageTokens` 属于重复快照，不得再次计数。
总 Token 按确认口径只累加 `input_tokens + output_tokens`；缓存比率则按已确认的
`cache_tokens / input_tokens` 计算，因为聊天 `input_tokens` 已包含缓存 Token。

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

- `answer`、`model` 和 `create_time` 在 74 条记录中全部存在，无空字符串回答。
- 1 条缺少 `durationSeconds`。
- 2 条缺少 `usageTokens`。
- 只有 2/74 条记录的 `canvas.meta.model` 与 `canvas.answer.model` 相同，因此最终回答模型
  必须读取 `canvas.answer.model`。

## 3. 研究模式

### 3.1 文件与事件总览

研究日志根节点是 JSON 数组，按 `ts` 非递减排列。单段文件首事件恒为 `request`（47/47）；
合并目录则不然——39 个文件以 `request` 开头，8 个直接以 `turn.start` 开头（见 3.1.2）。

事件类型全量普查（47 个合并逻辑回合，13,747 个事件）：

```text
message.part.updated  8112
message.updated       3789
llm.response           962
tool.debug             478
agent.system            77
agent.tools             77
agent.start             55
turn.start              51
request                 47
agent.mcp               47
turn.end                43
llm.error                9
```

同一批 47 个 `userMessageID` 的单段样本（14,308 个事件）按会话拆分后：

```text
                       主会话(11,955)  侧会话(2,353)   合计(14,308)
message.part.updated          7044          1500         8544
message.updated               3295           587         3882
llm.response                   837           142          979
tool.debug                     395            29          424
agent.system                    71            19           90
agent.tools                     71            19           90
agent.start                     51            19           70
agent.mcp                       47            19           66
turn.start                      47             0           47
turn.end                        41             0           41
agent.end                        0            19           19
llm.error                        9             0            9
request                         47             0           47
```

注意 `request` 没有顶层 `sessionID`，**不能**用 `sessionID` 分组把它分出来——它必须按
`body.sessionId`（或回退到首个 `turn.start.sessionID`）归到主会话侧。若只按 `sessionID`
过滤，会静默丢掉全部 47 个 `request`。

除 `request` 外，事件通常具有：

```jsonc
{
  "type": "<event-type>",
  "ts": "<timestamp>",
  "sessionID": "<session-id>"
}
```

各事件类型的顶层键（合并样本实测）：

```text
request               ts,type,method,path,headers,body
turn.start            ts,type,sessionID,userMessageID,query
agent.start           ts,type,sessionID,userMessageID,agent
agent.mcp             ts,type,sessionID,userMessageID,mcp
agent.tools           ts,type,sessionID,userMessageID,tools
agent.system          ts,type,sessionID,userMessageID,system
message.updated       ts,type,sessionID,info
message.part.updated  ts,type,sessionID,time,part
llm.response          ts,type,sessionID,userMessageID,loop,source,result
llm.error             ts,type,sessionID,userMessageID,loop,error
tool.debug            ts,type,sessionID,messageID,callID,tool,debug
turn.end              ts,type,sessionID,userMessageID,agents,durationMs
```

`message.updated` 与 `message.part.updated` **不带** `userMessageID`，只能经 `info.id` /
`part.messageID` 回溯到所属回合。合并样本含 39 个唯一 `sessionID` 与 102 个唯一
`userMessageID`（一轮用户消息可产生多段与多条关联消息）。`request` 没有顶层 `sessionID`，
但 `body.sessionId` 通常与后续事件的 `sessionID` 一致——**但有一例例外**：某文件的
`request.body` 根本没有 `sessionId` 键（47 个单段文件中仅 1 例），此时只能回退到
`turn.start.sessionID`。注意聊天模式使用 `sessionId`，研究模式主要使用 `sessionID`，
大小写不同。

### 3.1.1 侧会话（子 Agent 会话）必须按 sessionID 切分

**一个研究日志文件不等于一个逻辑回合。** 子 Agent 在自己的侧会话中执行，其
`message.updated` / `message.part.updated` / `llm.response` / `tool.debug` 与主会话事件
同处一个文件、同一条 `ts` 时间线上，但：

- 侧会话只有 `agent.start`（`agent.name == "general"`）、`agent.mcp` / `agent.tools` /
  `agent.system` 与消息事件，**没有 `request`、没有 `turn.start`、没有 `turn.end`**；
- 侧会话的 `userMessageID` 与主会话完全不重叠（19 个侧会话的 `userMessageID` 无一落在
  47 个主 `userMessageID` 内）；
- 每个侧会话恰好 1 个 `agent.start` 与 1 个 `agent.end`，且 `agent.end.durationMs`
  与"该侧会话首末 `ts` 跨度"几乎相等（比值 0.98–1.00），即 `agent.end` 是**子 Agent 会话
  的墙钟耗时**，不是主回合的。

样本规模：47 个单段文件中有 4 个含侧会话，共 19 个侧会话、2,353 个侧会话事件。
生产抽样（`data/raw/oss_jsonl/` 的 51 个文件）与之同源，同样是 4 个文件、19 个侧会话、
2,353 个侧会话事件。

**因此研究模式的读取器必须先取主会话**：

```text
主会话 = request.body.sessionId
        若 request 缺失、或 body 里没有 sessionId 键 → 回退到首个 turn.start 的 sessionID
然后丢弃 sessionID != 主会话 的全部事件
无 sessionID 的事件（只有 request）归主会话侧
```

不做这一步的后果（以单段样本计）：`message.part.updated` 多算 1,500 次、
`message.updated` 多算 587 次、`llm.response` 多算 142 次、`tool.debug` 多算 29 条。
这些多算的量在合并目录里**已经被上游切掉了**。

### 3.1.2 三个研究样本是同一批日志的三种切法

三个目录覆盖同一批 47 个 `userMessageID`，但计数各不相同（14,308 / 13,747 / 16,100）。
差异来自两件独立的事——**剔除侧会话**与**并入续跑段**——必须分开算：

```text
单段目录 14,308 = 主会话 11,955（含 47 个 request） + 侧会话 2,353
合并目录 13,747 = 主会话 11,955 + 续跑段 1,792
生产抽样 16,100 = 47 个带 query 文件 14,308（与 research_logs 逐事件相同）
                + 4 个 _noquery_ 文件 1,792（续跑段的补充切片）
```

- **侧会话**：单段目录含（19 个会话、2,353 个事件），合并目录已全部剔除。
- **续跑段**：合并目录含、单段目录缺失，精确为 1,792 个事件。4 个 `_noquery_` 文件正是
  这 1,792 个事件的来源切片，且逐文件与合并目录的增量精确对应。

续跑段按事件类型分解：`message.part.updated +1068`、`message.updated +494`、
`llm.response +125`、`tool.debug +83`、`agent.system +6`、`agent.tools +6`、
`agent.start +4`、`turn.start +4`、`turn.end +2`（合计 1,792）。

**逐文件核对**：47 个文件里 36 个完全相等；3 个（`msg_ecfa41e4` / `msg_060a7676` /
`msg_2a5e8ac2`）因并入续跑段而大幅增加（+1,149 / +515 / +128）。

**另有 4 个文件各多 1 个 `request`**（`msg_1b03822a` / `msg_408df0e2` / `msg_cda609b7` /
`msg_e90d78e8`）：文件中部多出一条 `request`，其 `ts` 与原 `request` 相差数小时、但
`body.sessionId` 相同——它是**续跑段自己的入口快照**。同一批里也有 4 个文件
（`msg_35e9abf0` / `msg_6e4bc03b` / `msg_7a003e14` / `msg_b2ab4de4`）**完全没有 `request`**，
首事件直接是 `turn.start`。

因此**合并目录的文件结构本身不规整**：47 个文件里 39 个恰有 1 个 `request`、4 个有 2 个、
4 个有 0 个。按"每文件必有 `request`"或"`request.body.sessionId` 就是主会话"写读取器会在
后两类文件上落空，必须保留回退路径（见 3.1.1）。

**结论：引用任何研究侧事件计数前必须先声明读的是哪个目录、是否已按主会话切分。**
三个目录的同类指标不可直接比较。

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
    "tag": "<tag>",
    "sensitiveCheck": null,
    "enabledMcpUrls": [],
    "enabledSkillList": []
  }
}
```

**逐文件基数不固定**：47 个单段文件各恰有 1 个 `request`；合并目录则是 39 个文件 1 个、
4 个文件 2 个（两个分段的入口快照都被保留）、4 个文件 0 个（首事件直接是 `turn.start`）。
读取器不能用"文件里必有 `request`"作前提。

`body` 的键集合**并不固定**，实测有 5 种形态：`enabledMcpUrls` / `enabledSkillList` /
`messageID` / `model` / `parts` / `query` / `sourceChannel` 七键恒有，其余按情况增补
`sessionId` / `tag` / `sensitiveCheck`。其中 47 个请求里有 1 个**没有 `sessionId`**、
23 个没有 `tag`、43 个没有 `sensitiveCheck`——这正是 3.1.1 要求保留回退路径的原因。
`parts` 在样本中只有 `text` 一种类型。

观察值：`sourceChannel` 取 `CONSOLE`(39) / `WEIXIN`(7) / `LARK`(1)；`tag` 多为 `TASK`(24)
或缺失(23)；`sensitiveCheck` 为 `null`(43) 或 `false`(4)；`enabledMcpUrls` 每请求 11–25 个；
`enabledSkillList` 每请求 19–89 个。**`enabledMcpUrls` 的个数与同文件 `agent.mcp` 的条目数
精确相等**——在 39 个恰有 1 个 `request` 的文件上全部成立（0 例不符）；其余 8 个文件因
`request` 数量不是 1（见上段）不在比对本内，需要逐个 `request` 对账。

安全边界：`headers` 和 `body.enabledMcpUrls[].*.headers` 中存在认证、用户、MCP key、网络与
内部追踪字段。collector 不得把这些对象整体复制到规范化结果、异常消息、日志或报告中。
只允许提取经过白名单批准的非敏感计数和名称，例如启用 MCP 服务数量、启用工具名称和技能
数量。URL、key、uid、IP、内部路径和请求头原文不得持久化。

### 3.3 `turn.start` 与续跑分段

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

**一个逻辑回合可能含多个 `turn.start`**（续跑/重启）：合并样本中 44 个文件有 1 个、
2 个文件有 2 个、1 个文件有 3 个。同一文件内多个 `turn.start` 的 `userMessageID`
**完全相同**（样本中不存在一个文件内跨 `userMessageID` 的 `turn.start`），因此
`turn.start` 不能作为唯一事件键：

```text
续跑重启次数 = 去重后的 turn.start 数 − 1
去重键必须是解析后的毫秒时间戳，不能是 userMessageID
```

同时观察到续跑段的 `turn.start.query` 为空字符串，而首段带完整 query——与
`continuation_restart_count`（7.3）的去重与保守计数规则一致。

### 3.4 `agent.start` / `agent.end`

`agent.start` 标记一次 Agent 启动。事件数随目录不同：

```text
单段目录（含侧会话）  70 = 主会话 51（orchestrator 25 / compaction 26） + 侧会话 19（general）
合并目录              55 = 单段主会话 51 + 续跑段 4
```

```jsonc
{
  "type": "agent.start",
  "ts": "<timestamp>",
  "sessionID": "<session-id>",
  "userMessageID": "<message-id>",
  "agent": {"name": "orchestrator | compaction | general", "mode": "primary"}
}
```

`agent.mode` 在样本中全部为 `primary`。`agent.start` 只说明"启动了哪个 Agent 类型"，
**不带 Agent 实例 ID**，因此研究模式至今没有与聊天模式 `worker agentId / taskId` 同构的
执行实例标识（见第 4 节）。

`agent.end` 只在**单段目录与生产日志**中出现在侧会话（主会话 0 条）：

```jsonc
{
  "type": "agent.end",
  "ts": "<timestamp>",
  "sessionID": "<session-id>",
  "userMessageID": "<message-id>",
  "durationMs": 56409
}
```

- 19 个 `agent.end` 全部落在侧会话，每个侧会话恰好 1 条，与 `agent.start` 严格配对。
- `durationMs` 与侧会话首末 `ts` 跨度比值 0.98–1.00，即它是**子 Agent 会话的墙钟耗时**。
- 合并目录里 0 条 `agent.end`：切分逻辑丢弃了侧会话，主会话本就没有该事件。
  因此**任何"Agent 总耗时"口径都要先声明读的是哪个目录**，否则结论会随目录变化。

`turn.end.agents` 的语义可由此确定：在 41 个有 `turn.end` 的单段文件上，
`agents` **恒等于全文件（含侧会话）的 `agent.start` 数 + 1**（41/41 成立），
即"本轮涉及的主 Agent 与子 Agent 总数"，不是主会话内部的计数。用它做对账时必须用
含侧会话的口径，否则会对不上。

### 3.5 Agent 环境事件

`agent.mcp` 记录 MCP 服务与工具范围：

```jsonc
{
  "type": "agent.mcp",
  "ts": "<timestamp>",
  "sessionID": "<session-id>",
  "userMessageID": "<message-id>",
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

`agent.mcp` 只在主会话与侧会话各出现一次（单段样本 47 + 19；合并目录只留主会话的 47）。
事件级 `mcp` 条目数 11–25，单条 MCP 的 `tools` 数量 0–40，`type` 全部为 `remote`。
样本出现 26 个不同的 `name`（`hexin-fund` / `hexin-bond` / `文档管理` / `量化回测` /
`mcp-server-brm-data-scope` / `wind-stock` …），其中 11 个在全部 47 个回合里都出现，
其余只在个别回合出现（如 `IR数据管理` 3 次、`wind-*` 各 1 次）。

`status` 为 `connected`(527) 或 `failed`(7)。**`status` 必须被检查**：把 `failed` 的 MCP 的
工具一并当作可用工具，会污染路由与必填参数分母。URL 属于环境信息，不应进入指标结果。

#### `agent.mcp[].tools` 与 `agent.tools` 的命名不能直接比对

两个集合里的工具名**不是同一种写法**，直接取交集会得出严重偏低的结论：

```text
agent.mcp[].tools   156 个不同名称，其中 105 个含 ___ 形态的服务名前缀
                    例：_____comein-common-brm-public___document_access
                        mcp-server-brm-data-scope_comein-common-brm-public_...
                    其余 51 个无该分隔符，如 hexin-fund_search_funds（服务名即前缀段）
agent.tools         163 个不同名称，均为裸工具名
两者精确交集         仅 3 个
```

按最后一个连续下划线切掉前缀后，156 个 MCP 名称里有 108 个能对上 `agent.tools`、
69 个能对上 `mcp_tool_manual.json`（后者与 `agent.tools` 的交集是 71）。**但仍有 48 个
切完仍对不上任何已知目录**，且前缀分隔符形态不统一（`_`、`__`、`___`、`_____` 都出现过），
所以**没有一条可靠的字符串规则**可从 `agent.mcp[].tools` 还原出规范工具名。

因此：

- `agent.mcp[].tools` 只能用于"某服务声明了哪些工具"的粗粒度统计；
- 判断某次调用是不是 MCP，仍须走 2.7 / `mcp_tool_manual.json` 的精确匹配路径，
  不能拿它当 MCP 工具目录；
- 两个集合也**不是包含关系**（实测互有出入），不能用后者减前者来推导"MCP 工具清单"
  或"内置工具清单"。

期望上游在后续版本给出规范化的工具标识（见第 6 节）。

`agent.system` 包含系统提示或系统上下文字符串，属于敏感模型上下文，只能在内存中用于明确
授权的语义评估，不得持久化正文。

### 3.6 `message.updated`

`message.updated` 是消息对象的重复状态快照。合并样本 3,789 个事件，对应 1,025 个唯一
`info.id`（单条消息最多被更新 120 次），必须按 ID 合并。

`info` 的完整键集合：

```text
id, sessionID, parentID, role, agent, mode, model, modelID, providerID,
cost, tokens, time, finish, error, summary, query, path, tag,
sourceChannel, streamFinish, streamFinishReason
```

```jsonc
{
  "type": "message.updated",
  "ts": "<timestamp>",
  "sessionID": "<session-id>",
  "info": {
    "id": "<message-id>",
    "parentID": "<parent-message-id>",
    "role": "user | assistant",
    "agent": "orchestrator | compaction",
    "mode": "orchestrator | compaction",
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
    "cost": 0,
    "finish": "tool-calls | stop | other",
    "error": {},
    "summary": {},
    "streamFinish": true,
    "streamFinishReason": "idle"
  }
}
```

样本中的 `role` / `agent` / `mode` 分布：

```text
role      assistant 2787 / user 1002
agent     orchestrator 3705 / compaction 84
mode      orchestrator 2703 / compaction 84 / 缺失 1002
```

要点：

- **`agent` 现在除 `orchestrator` 外还取 `compaction`**，`mode` 同值。压缩是独立 Agent 生命周期。
- `mode` 在全部 user 消息上缺失（1002），`agent` 仍存在；判定"哪个 Agent 产生这条消息"
  应以 `agent` 为准，`mode` 只作交叉校验。
- `finish` 取值：`tool-calls`(1682) / `stop`(174) / `other`(4) / 缺失(1929，多为快照早期或
  user 消息)。`other` 是与 `tool-calls`/`stop` 并列的第三种终态，读取器不得只认前两种。
- `tokens` 只在 2787 条 assistant 快照上出现（user 快照没有）。其分项为
  `input` / `output` / `reasoning` / `cache.read` / `cache.write`，其中 `total` 只覆盖 1866 条。
  **`total` 不可靠，不能用它做守恒校验**；权威 Token 来源仍是 3.7 的 `step-finish.tokens`。
- `cost` 与 `tokens` 同时出现（2787 条），单位未确认。
- `error` 只在 4 条 assistant 快照上出现，形如
  `{"name": "APIError", "data": {"message": "400: ..."}}`。这是**跨通道投影**，
  与 `llm.error`（3.9）表达同一次失败，不得重复计数。
- `summary` 有两种形态：947 条 user 快照上是对象（键为 `diffs`），84 条 assistant 快照上是
  **布尔值**。按 dict 解包会抛异常，必须做类型判别。
- `streamFinish` / `streamFinishReason` 仅在 43 条上出现（`(True, 'idle')`），可作流式结束
  的辅助信号。
- `time.created` 覆盖 3,789 条，`time.completed` 只覆盖 958 条。

研究模式中的 `role`：

- `user`：用户请求消息。
- `assistant`：orchestrator / compaction 的模型消息。
- 工具角色不在此事件中，而是在 `llm.response.result.messages[].role == "tool"` 中出现。

### 3.7 `message.part.updated`

`message.part.updated` 是消息内容片段的生命周期快照，也是本批变化最大的事件。公共字段：

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

样本 8,112 个事件对应 **4,112** 个唯一 `part.id`（单条 part 最多更新 9 次），涉及 975 个
不同 `part.messageID`。同一 part 可能多次更新，必须按 `part.id` 合并。

**但 `part.messageID` 与 `message.updated.info.id` 并非完全对齐**：975 个
`part.messageID` 中有 5 个（涉及 6 个 tool part 事件）找不到对应的 `message.updated`。
读取器不能假设"每个 part 都能回溯到一条消息快照"，这类缺口要进诊断而不是静默丢弃或猜测归属。

观察到的 part 类型：

```text
tool         4349 个更新事件
reasoning    1041
step-start    916
step-finish   910
text          847
compaction     49
```

**`compaction` 是新增的 part 类型**（见下方「压缩片段」）。`step-start` 与 `step-finish`
不再一一相等（916 vs 910），存在 6 个缺 `step-finish` 的未闭合步骤，读取器必须处理配对缺失。

#### 文本与推理片段

```jsonc
{
  "id": "<part-id>",
  "messageID": "<message-id>",
  "type": "text | reasoning",
  "text": "<redacted>",
  "time": {"start": 0, "end": 0},
  "synthetic": null,
  "metadata": {}
}
```

`text.synthetic` 取 `None`(820) 或 `true`(27)；`true` 表示该文本由系统合成而非模型输出
（如压缩后的续接提示），语义评估前应单独判别。`text.metadata` 的观察键为 `chunkList` /
`compaction_continue` / `openai`。

`text` 和 `reasoning` 正文不得写入指标结果、诊断日志或提交文件。最终回答应选择
`turn.end` 前最后一个 assistant 消息的最终非空 `text` part，并先按 `part.id` 合并更新。

#### 步骤边界

`step-start` 与 `step-finish` 的 part 键集合：

```text
step-start    id, messageID, sessionID, type
step-finish   id, messageID, sessionID, type, reason, cost, tokens
```

`step-finish` 的 `reason` 取值：`tool-calls`(841) / `stop`(67) / `other`(2)，与
`message.updated.info.finish` 同源；`cost` 为整数；`tokens` 分项为
`input` / `output` / `reasoning` / `total` / `cache.read` / `cache.write`，
其中 `total` 覆盖 908/910。

这些 Token 可能与 `message.updated.info.tokens` 和 `llm.response.result.usage` 重复表达同一次
模型调用，不能跨层直接相加。

Token 消耗以按 `messageID` 去重后的 `step-finish.tokens.total` 为权威来源；
`message.updated.info.tokens` 和 `llm.response.result.usage` 不参与计算，cache 分项不单独处理。

平均模型步骤时延应按 `messageID` 配对 `step-start` 与 `step-finish`，使用事件数值型 `time`
计算；两种 part 的 `part.id` 不同，不能按 part ID 配对。事件 `ts` 只用于边界差异诊断。
`message.updated.info.time.created/completed` 覆盖更宽的消息生命周期，不作为步骤时延边界。

#### 压缩片段

```jsonc
{
  "id": "<part-id>",
  "messageID": "<message-id>",
  "sessionID": "<session-id>",
  "type": "compaction",
  "auto": true,
  "overflow": null,
  "tail_start_id": "<part-id>"
}
```

样本 49 个更新事件对应 28 个唯一 `part.id`，`auto` 全部为 `true`，`overflow` 为
`null`(47) 或 `true`(2)。`tail_start_id` 指向保留尾部起始的 part。

统计口径：`compaction_event_count`（7.6）按 `part.id` 去重计数，且只计 `auto == true`。
`agent.start` 的 `compaction` 名称（28 条）与这里的唯一 part 数一致，可作交叉校验；
`message.updated` 中 `info.mode == "compaction"` 的摘要属跨通道投影，只进诊断不计数。

#### 工具 part

```jsonc
{
  "id": "<part-id>",
  "messageID": "<message-id>",
  "callID": "<tool-call-id>",
  "type": "tool",
  "tool": "<tool-name>",
  "metadata": {},
  "state": {
    "status": "pending | running | completed | error",
    "input": {"<field>": "<redacted>"},
    "output": "<redacted>",
    "raw": "<redacted>",
    "title": "<redacted>",
    "tool_type": "<type>",
    "error": "<redacted>",
    "attachments": [],
    "modelInput": {},
    "time": {"start": 0, "end": 0},
    "metadata": {}
  }
}
```

样本 4,349 个 tool part 更新对应 1,299 个唯一 `callID`（`callID` 与 `part.id` 严格一对一），
涉及 43 种工具。`callID -> tool` 严格一对一（0 冲突）。

状态分布与生命周期形态（跨文件合并后统计）：

```text
status 计数     pending 1293 / running 1746 / completed 1299 / error 11

per callID 形态
  pending -> running -> completed   1281
  pending -> running -> error          9
  completed（只有终态）                 6
  pending -> error                     2
  pending（只有起点）                    1
```

**`error` 是与 `completed` 并列的工具终态**，此前版本没有观察到；`error` 状态带
`state.error` 字符串正文（属敏感内容，只在内存中用于归因）。另外仍有 6 个调用只观测到
终态、1 个只观测到起点，说明**不能假设生命周期的三个状态一定齐全**：上列五种形态都必须能
落到稳定结论或稳定原因码。

⚠️ **跨逻辑回合的键必须全局去重，不能逐文件分组**。实测有 1 个 `part.id` 与 1 个 `callID`
同时出现在两个逻辑回合文件中（`msg_408df0e2` 与 `msg_b2ab4de4`，同一 `sessionID`）：
前者有 `pending/running/completed` 三个快照，后者只多一条 `completed`。逐文件统计会把这个
调用算成 2 个（`completed` 单独成组 7 个而非 6 个），并在两个回合里各记一次工具执行。
本目录的 47 个文件并非彼此独立的命名空间。

时间覆盖：

- 3,056 个状态快照有 `state.time.start`。
- 1,310 个有 `state.time.end`。
- 12 个有 `state.time.compacted`（压缩 Agent 专用）。

完整调用时延应使用同一 `callID` 的最终状态时间，或直接使用 completed 快照中确认过的
`start/end`。`state.title` 为字符串（1,320 条），`state.attachments` 为列表（543 条，
样本中均为空），`state.modelInput` 只在 115 条上出现（形如
`{"filePath":..., "after":..., "newString":...}`，属编辑类工具的模型输入回显），
`state.error` 只在 11 条上出现（与 `error` 终态数量一致）。

`state.input`、`state.output`、`state.raw`、`state.error` 和证据 `chunkList` 可能包含用户
数据、查询、文档内容和来源正文，只能在内存中按指标最小范围处理。

### 3.8 `llm.response`

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

样本 962 个事件。按 `(userMessageID, loop)` 分组后，组大小只有两种：

```text
只有 1 条事件                    908 组
有 2 条事件（一条带 processor）    27 组
```

即绝大多数 loop 只观测到一条 `llm.response`；只有 27 个组出现第二条
`source == "processor"` 的投影，且两条**都在同一个文件内**（不是跨文件投影）。因此：

- 模型调用数应按 `(userMessageID, loop)` 去重，**不是**事件条数（962 ≠ 调用数）。
- `source == "processor"` 的事件是同一 loop 的第二投影，不得重复计数。

`loop` 取值类型：整数 883 个、**`null` 79 个**（`source == "processor"` 的 27 条**全部**
是 `loop == null`；剩余的 52 条 `loop == null` 是各自 loop 里的唯一事件）。`null` 不能直接
参与去重键——按 `(userMessageID, loop)` 分组时，`loop == null` 只是"无 loop 标识"的占位，
把它们一律折成一条会丢调用。必须先诊断 loop 缺失、再决定是否计为独立调用。

每个文件内 `loop` 序列单调递增（0 个文件违反），也没有观测到 loop 重置；样本
`userMessageID` 无缺失。

`result.toolCalls` 共 1,289 条，`toolCallId` 全部唯一，**没有**跨 `(userMessageID, loop)`
重复出现的条目——即本样本中该数组本身无需去重。但它与工具 part 的 `callID` 集合并不相等：
1,289 个 `toolCallId` 全部落在工具 part 的 1,299 个 `callID` 内，**反向有 10 个 callID 只在
`message.part.updated` 里出现**（`llm.response` 未投影）。因此工具调用的权威计数仍应以
part 侧按 `callID` 去重为准（见 3.12），`result.toolCalls` 只作交叉校验。

`result.finishReason` 取值：`tool-calls`(841) / `stop`(118) / `other`(2) / **`error`(1)**。
它与 `message.updated.info.finish`（`tool-calls`1682 / `stop`174 / `other`4）和
`step-finish.reason` 同源但计数不同——同一逻辑循环在这三处的投影数量不等，禁止跨处相加，
也不要用某处的计数推断另一处。

`result.messages[].role` 包含：

- `assistant`（961）
- `tool`（841）

`result.messages[].content[].type` 包含：

- `tool-call`（1289）
- `tool-result`（1289）
- `reasoning`（541）
- `text`（459）

该结构比 `message.updated` 更接近供应商或模型协议原始响应，正文和工具输入输出不得持久化。

### 3.9 `llm.error`

```jsonc
{
  "type": "llm.error",
  "ts": "<timestamp>",
  "sessionID": "<session-id>",
  "userMessageID": "<message-id>",
  "loop": 13,
  "error": {
    "value": {
      "error": {
        "name": "<error-name>",
        "message": "<redacted>",
        "stack": "<redacted>",
        "url": "<redacted>",
        "requestBodyValues": {}
      }
    }
  }
}
```

样本 9 个事件，分布在 6 个文件（4 个文件 1 条、1 个文件 2 条、1 个文件 3 条）。
`loop` 指向发生错误的模型循环，可与同 loop 的 `llm.response` 配对判定该错误是否被重试后
恢复（`error_recovery_rate` / 7.5 的 loop 级配对即基于此）。

`error.value.error` 的对象形态只应取 `name` + `message` 参与分类：

- `stack` 与 `url` 含内部路径与追踪信息，**显式排除**，既不参与匹配也不落盘。
- `requestBodyValues` 内含完整 `model` / `max_tokens` / `thinking` / `system` 提示词与
  历史 `messages`（实测单条 system 文本可达数万字符），是最高敏感度的诊断载荷，
  规范化结果中不得保留任何部分。

`message.updated.info.error` 是同一失败的另一投影，只进诊断不计数。

### 3.10 `tool.debug`

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

#### 3.10.1 三级结构与两级标识

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
  实测：同一 callID 内含多个**不同** uuid 的为 **0**，一个 uuid 对应多个 callID 的也为 **0**
  —— uuid 与 `callID` **严格一一对应**，可视为 callID 在 HTTP 层的投影；`N` 是该次调用内
  请求的先后序号，从 1 起递增。

**`N` 不是重试计数。** 相邻两个请求（同一 callID 内 `N` 与 `N+1`）绝大多数在干不同的事。
权威口径 7.8 记录的全量含重试日志中，相邻请求入参不同的比例为 **83.8%**，说明多数字号对应
**工作流步骤**（查路径 → 建文件 → 读文档），而非重发。任何把 `N ≥ 2` 当作重试的口径都会
显著高估，详见权威口径 7.8 与 `初步入库评测指标.md` 7.8 节。同样，**同一 endpoint 的连续
调用不等于重发**，这也是指纹必须由"endpoint + 入参"共同决定、不能只看 endpoint 的原因。

#### 3.10.2 endpoint 穷举

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

#### 3.10.3 规模与覆盖

合并样本 13,747 个事件中有 `tool.debug` **478 条**，涉及 477 个 callID：

- 363 条 `debug.apiCalls` 为列表（共 573 条 apiCalls、去重后 **572 条**唯一
  `(callID, requestId)`）。
- 115 条**无 `apiCalls` 列表**，其 `debug` 只含 `requestIds` / `requestId1..N` /
  `requestIdCount` 与内层 `debug`。这些事件**不携带请求明细**，不能参与重试分子分母；
  读取器必须区分"有事件无明细"与"无事件"。
- 出现 `tool.debug` 的工具：`edit` 115、`read` 108、`excel` 57、`word` 57、`cp` 48、
  `glob` 42、`rm` 28、`write` 10、`grep` 6、`rename` 4、`mkdir` 2、`docdna` 1。
- 含 `tool.debug` 的 callID 共 477 个，但工具调用 `callID` 全集为 1,299 个——
  **覆盖率仅 36.7%**。477 个 debug callID 全部落在工具 part 的 callID 内（0 个孤立 debug），
  说明这是"部分调用有 debug"而不是"debug 指向了别处"。
  **debug 只能作为可选诊断来源，不能作为工具调用分母**；覆盖率必须随指标值输出
  （7.8 的 `api_debug_coverage`）。

内层 `debug.debug` 的键随工具而异，观察到的包括 `backend`(cloud 97 / local 13)、
`blockId` / `blockLabel` / `blockOp` / `baseRevisionNo` / `revisionNo` / `appliedCount` /
`wireTranslated` / `wireOldString` / `wireNewString`（编辑类）、`executionPath` /
`routedDepsAvailable` / `identity` / `action` / `filePath` / `fullPath` / `preflight` /
`brm` / `bridge` / `truncated`（环境类）与 `channel` / `durationMs`。该层含路径与文件内容，
属高敏感，不得持久化。

#### 3.10.4 投影与去重

同一条 `tool.debug` 事件可能被投影两次：实测 `turn_036` 的两个相邻事件
`(callID, requestId)` 完全相同。因此 573 条原始 apiCalls 对应 572 条唯一值，
**必须按 `(callID, requestId)` 去重**，重复投影进诊断计数。

#### 3.10.5 已知缺口：跨 callID 的重发不可判定

现有字段**无法区分**下列两种情况：

```text
同一工具 + 同一入参，出现在不同 callID
  ├── 框架整调用重发（真重试）
  └── 模型在不同步骤合法地重复读同一文件 / 重复 grep 同一 pattern（正常工作）
```

`tool.debug.tool` 字段存在（478/478 全有）且与 `part.tool` 一致，但按工具名跨 callID 分组
会把合法重复一并计入。要区分二者需要联合 `ts` 与 `step-start` 判定"两次请求之间有没有新的
模型步骤"，属未立项的新口径。

因此 7.8 当前采用**保守下界**：只统计同一 `callID` 内部的同指纹重复请求。

安全边界：`debug` 内含接口地址、请求 ID、文件路径、节点 ID、内部身份和响应元数据，属于
高敏感诊断内容。规范化结果最多保留白名单计数（如 `apiCallCount`）与经确认的聚合量
（如 endpoint 频次、业务错误码），不得保留完整 `request` / `response` 对象。

### 3.11 `turn.end`

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

`turn.end` 是研究回合的稳定终点。43/47 个合并逻辑回合有 `turn.end`（4 个缺失，
即未闭环）；`durationMs` 在存在的 43 条上全部有值。`agents` 取值 1–10。

`agents` 的语义不是"主会话内的 Agent 数"：在 41 个有 `turn.end` 的**单段**文件上，
它**恒等于全文件（含侧会话）的 `agent.start` 数 + 1**（41/41 成立）——即"本轮涉及的主
Agent 与所有子 Agent 总数"。用它做对账必须用含侧会话的口径，否则会对不上。

研究模式端到端时延应优先使用其 `durationMs`，并以 `turn.start.ts` 到 `turn.end.ts`
的差值做一致性诊断，而不是混用 HTTP request 时间。

### 3.12 研究模式关联键

推荐关联关系（**箭头不表示集合相等**，三处覆盖范围依次收窄）：

```text
request.body.sessionId
          =
事件 sessionID（仅主会话；侧会话有独立 sessionID，见 3.1.1）

turn.start.userMessageID
          =
本轮用户消息标识（同一逻辑回合内可有多条 turn.start，同值）

message.updated.info.id
          =
message.part.updated.part.messageID（975 个 part.messageID 中有 5 个无对应快照）

message.part.updated.part.callID      1,299 个（工具调用的权威全集）
          ⊇
llm.response.result.toolCalls[].toolCallId   1,289 个（少 10 个）
          ⊇
tool.debug.callID                      477 个（覆盖率 36.7%）
```

稳定去重键（**除主会话键外，每条都必须先限定在主会话内**，见 3.1.1）：

```text
主会话：request.body.sessionId
        （request 缺失或无 sessionId 键时回退首个 turn.start 的 sessionID）
消息：sessionID + info.id
消息片段：sessionID + part.id
工具调用：sessionID + callID
模型循环：sessionID + (loop 或 null 占位)（loop 为 null 时须先诊断，不可直接折并）
研究回合：主会话 + userMessageID
续跑分段：sessionID + userMessageID + 解析后的 turn.start 毫秒时间戳
API 请求：callID + requestId（tool.debug.debug.apiCalls[]，见 3.10）
```

侧会话的 `callID` / `info.id` / 段落计数若与主会话混在一个命名空间里，跨会话合并会凭空
造出"同一个调用被观测多次"的假象。反过来，**跨逻辑回合的合并又必须做**——实测有 1 个
`callID` 同时落在两个合并目录文件里（见 3.7 的工具 part），所以"按 sessionID 切分"与
"全局键去重"两件事都要做，缺一不可。

## 4. 双模式字段对应

```text
语义                 聊天模式                         研究模式
会话                 canvas.meta.sessionId            主会话 sessionID / request.body.sessionId
单次请求             canvas.meta.traceId              userMessageID
用户 query           canvas.meta.query                turn.start.query
聊天历史             canvas.meta.message              message.updated + message.part.updated
Agent 类型           agents[].agentName               message.updated.info.agent
                                                      （orchestrator / compaction；侧会话为 general）
Agent 执行实例       worker agentId / taskId          未见同构 ID（agent.start 只给类型名）
子 Agent 隔离        同一 canvas 内的 worker 片段      独立侧会话（必须按 sessionID 切分，见 3.1.1）
工具调用 ID          tool_call[].toolId                part.callID / toolCallId
工具名称             tool_call[].toolName              part.tool / toolCalls[].toolName
工具输入             tool_call[].arguments             part.state.input / toolCalls[].input
工具结果             tool_result[].result/rawResult    part.state.output/raw
工具成功标记         tool_result[].success             无同构布尔字段；用 info.finish /
                                                      step-finish.reason / 工具终态联合判断
工具时延             tool_result[].durationSeconds     part.state.time.start/end
模型步骤时延         message.content.durationSeconds   step-start/step-finish 的 event.time
最终回答             canvas.answer.answer              turn.end 前最后 assistant text part
回合总时延           answer.create_time -              turn.end.durationMs
                     meta.create_time
Token                message.content.usageTokens       step-finish.tokens.total
压缩事件             无压缩通道                        part.type == "compaction"
模型层错误           无模型错误通道                    llm.error / info.error
```

两处容易误配的点：

- **"工具成功标记"两模式不同构**。聊天侧有显式 `tool_result.success` 布尔字段（944/944 覆盖）；
  研究侧没有对应的布尔键，成功要靠 `message.updated.info.finish` / `step-finish.reason`
  与工具终态联合判断。不要为了对齐 schema 而给研究侧硬造一个 `success`。
- **"子 Agent 隔离"的实现方式相反**。聊天侧子 Agent 在主 canvas 的 `agents[]` 内，
  靠 `taskId -> agentId` 区分（同一容器内分区）；研究侧子 Agent 在**独立的 `sessionID`** 里
  （跨容器隔离）。任何"按 Agent 汇总"的实现都必须针对两种模式各写一套，不能复用同一段过滤逻辑。

研究模式当前没有与聊天模式 `traceId` 完全同构的明确字段。`userMessageID` 能标识本轮用户
请求，但正式统一 schema 前应使用派生 `trace_id`，并在 `source_trace_id` 中保留原始可用
标识类型，不得假装二者上游语义完全相同。

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
      "status": "pending | running | completed | error | failed | timed_out | unknown",
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
- 7 个匿名 Agent 条目的产生条件（只有 `messages`、无任何身份字段）。
- 独立 `tool_result` message 与原请求的官方关联规则（本批 159 条）。
- `tool_result.success` 的权威语义：它是否覆盖所有失败形态？样本 943:1 的极度偏斜不足以
  判断，且当前 `tool_error_rate` 的聊天实现仍需以载荷代理为主。
- 子 Agent 的 `arguments.query` / `task` / `data` 三种键各自的契约。

### 研究模式

- **`agent.start` / `agent.end` 的配对边界**：侧会话内已确认严格 1:1（19/19），主会话则
  有 `agent.start`（51 条）但**一条 `agent.end` 都没有**。主会话的 Agent 生命周期终点由
  `turn.end` 表达，还是上游漏发了 `agent.end`？这决定"Agent 级耗时"能否在主会话上成立。
- 子 Agent 侧会话与主会话的**归属关系没有显式外键**：侧会话不带父 `callID` / `taskId`，
  目前只能靠"同文件 + 时间重叠"推断。需要上游提供父子关联字段，否则子 Agent 成本无法
  回挂到主 Trace。
- `agent.mcp[].status == "failed"` 的 MCP，其工具是否仍在 `agent.tools` 中、是否可调用。
- `llm.response.loop` 为 `null` 的 79 条事件代表什么：是否等价于"该 loop 无编号"，
  能否与整数 loop 共用同一个去重命名空间。
- `message.updated.info.tokens`、`step-finish.tokens` 与 `llm.response.result.usage` 的包含关系；
  `info.tokens.total` 只覆盖 1866/2787，缺失是上游未填还是消息尚未完成。
- 续跑段的入口快照为何**只在部分文件**保留：4 个合并文件中部多出一条 `request`、
  4 个完全没有 `request`，其余 39 个恰有 1 条。是导出时的条件差异还是切分逻辑的遗漏？
- `agent.mcp[].tools` 与 `agent.tools` 的工具名**用了两套写法**（前者多带服务名前缀、
  分隔符形态不统一），精确交集只有 3，剥前缀后仍有 48 个对不上任何目录。需要上游给出
  规范化的工具标识，或一份"声明名 → 规范名"的映射。
- 同一 `(userMessageID, loop)` 为什么只有 27 组出现第二条 `processor` 投影，且这 27 组的
  `loop` **全部为 `null`**、其余 52 条 `loop == null` 却是各自组内的唯一事件。这是否意味着
  `processor` 投影只发生在 loop 编号缺失的路径上？会不会影响计费口径？
- `userMessageID` 是否可被上游正式承诺为单次研究 Trace 标识。
- `part.time` 与 `part.state.time` 的单位和权威优先级；`state.time.compacted` 是否参与工具时延。
- `turn.end.durationMs` 是否包含排队、网络请求和交付物写入时间。
- `turn.end.agents` 的官方定义是否就是"本轮全部 Agent 数（含子 Agent）"——本批 41/41 满足
  `agents = 全文件 agent.start 数 + 1`，但 `+1` 的那一个是谁（主 Agent？）尚无文档。
- `compaction` part 的 `overflow` / `tail_start_id` 语义，以及手动压缩当前是否只存在于
  `auto == false`（样本未观测到该取值，可能只是本批未出现）。
- `text.synthetic == true` 的 27 条文本应在哪些指标中排除。
- `message.updated.info.summary` 在 user 快照上是对象、在 assistant 快照上是布尔值：
  两种形态各自代表什么，是否有统一读取方式。
- `tool.debug` 的长期保留策略，以及哪些安全计数字段允许进入评测诊断。
- `tool.debug` 中 115/478 条事件无 `apiCalls` 列表（只有 `requestIds` / `requestIdN`）：
  这些事件代表"工具内部无 HTTP 调用"还是"明细未上报"？两种语义对应不同的 7.8 分母口径。
- 跨 `callID` 的"同工具同入参"请求：框架整调用重发与模型合法重复工作，现有字段无法区分
  （见 3.10.5）；需要上游提供重发标记，或确认可用 `ts` + `step-start` 联合判定。
- 同一 `callID` 会跨两个逻辑回合文件重复出现（见 3.7）：这是上游的投影行为，还是切分
  逻辑把同一次调用归到了两个 `userMessageID`？前者要求下游全局去重，后者要求修切分。

在这些问题确认前，缺少可靠字段或映射的指标必须返回稳定的 `NOT_COMPUTABLE` 原因，不能
使用近似值或通过重复事件推测真实调用次数。
