# 实时日志评测与结果落库方案

## 1. 目标与结论

当前评测器从本地 JSON 文件读取日志，适合开发、回归测试和历史数据重算。生产环境应当把
“文件输入”替换成稳定的日志接入层，但继续复用现有的 normalization、evaluation、
aggregation 契约，避免为实时场景重新实现指标。

推荐的生产模式是：

```text
生产日志源
  -> 日志接入层
  -> 原始日志不可变存储
  -> Trace 完结检测
  -> 评测任务队列
  -> 评测 Worker
  -> 评测结果写入服务或受限数据库账号
  -> 聚合报表与监控
```

核心职责边界：

- 评测代码、指标版本、日志解析、Judge 调用和结果生成由评测项目负责。
- 数据库管理员负责数据库实例、表结构审核、账号权限、容量、备份和迁移执行。
- 不建议让数据库管理员长期手工运行评测脚本。
- 不建议把数据库管理员提供的高权限账号写进评测脚本。
- DBA 可以执行一次性的建表或迁移 SQL；持续计算应由评测服务或定时任务自动完成。

如果公司已有消息队列和任务平台，优先使用异步 Worker。如果暂时没有，可以先用定时增量
批处理上线，但接口和结果表仍按可重试、可幂等、可追溯设计。

## 2. 为什么不能直接把本地脚本交给 DBA

把 Python 脚本直接推送给 DBA 执行，短期看起来简单，但会产生以下问题：

- DBA 需要理解 Python 环境、依赖、Judge 配置和各指标的运行条件。
- 指标版本升级后，脚本和数据库状态容易不同步。
- 失败重试、部分失败隔离、日志脱敏和运行监控没有统一入口。
- 数据库凭证可能散落在脚本、命令历史或配置文件中。
- 无法明确区分“新日志首次计算”和“旧日志按新版本重算”。
- 同一条 Trace 被重复执行时容易产生重复数据。

因此，DBA 最适合管理数据库边界，不适合成为评测程序的实际操作者。

## 3. 推荐的职责分工

### 3.1 评测项目维护方

负责：

- 定义统一 Trace 输入契约。
- 维护聊天模式和研究模式的日志适配器。
- 维护 evaluator、`evaluator_version`、Judge prompt 版本和证据工具目录版本。
- 实现任务消费、失败重试、幂等检查和结果写入。
- 提供容器镜像、部署包或内部服务，而不是零散源码文件。
- 提供健康检查、结构化运行日志和安全诊断。
- 提供重算命令，但限制重算范围和版本。

### 3.2 日志平台或业务服务

负责：

- 在 Trace 完结时发出事件，或者将完整日志写入约定的对象存储、日志仓库或消息队列。
- 提供稳定标识，例如 `trace_id`、`chat_id`、`session_id` 和 `user_message_id`。
- 提供事件时间、模式、日志 schema 版本和日志存储位置。
- 保证日志至少投递一次；重复投递由评测系统通过幂等键处理。

### 3.3 数据库管理员

负责：

- 审核并创建评测数据库、表、索引和保留策略。
- 创建最小权限账号。
- 配置数据库网络访问、连接池限制、备份、审计和告警。
- 执行经过审核的 schema migration。
- 不接触原始用户正文、Judge 密钥或不必要的业务敏感内容。

## 4. 接口还是脚本

### 4.1 推荐方案：评测服务加受控写入边界

评测服务接收“某条 Trace 已完成”的任务，读取完整日志，运行指标，再通过以下两种方式之一
写入结果。

方式 A：调用结果入库 API。

- DBA 或数据平台维护一个内部写入 API。
- 评测服务只提交经过校验的评测结果。
- API 负责鉴权、幂等、字段校验和数据库事务。
- 适合数据库不允许业务程序直连，或者公司已经有统一数据服务层的环境。

方式 B：使用数据库最小权限账号直写。

- DBA 创建只允许操作评测结果表的账号。
- 评测服务使用连接池和参数化 SQL 写入。
- 账号不允许修改 schema，不允许读取其他业务表。
- 适合内部系统、调用链较短且数据库访问规范成熟的环境。

两种方式都可以。选择原则不是“接口一定比脚本好”，而是数据库由谁负责以及公司是否允许
应用直连数据库。

建议优先级：

1. 公司已有统一数据写入服务：使用入库 API。
2. 允许内部服务直连专用评测库：使用最小权限数据库账号。
3. 暂时没有服务化条件：使用受调度平台管理的容器化批处理。
4. 只在一次性历史回填时，由 DBA 执行建表 SQL；不要让 DBA 长期手动执行评测脚本。

### 4.2 可以交给 DBA 的内容

- 建库、建表和索引 SQL。
- schema migration 文件。
- 数据保留、分区和归档建议。
- 最小权限账号需求。
- 结果字段字典和容量估算。
- 回滚 SQL 或迁移工具说明。

### 4.3 不应交给 DBA 手工维护的内容

- 指标 Python 实现。
- Judge prompt 和 API 调用代码。
- 聊天、研究日志解析逻辑。
- 失败重试和重算流程。
- 每天需要人工执行的计算命令。

## 5. 生产实时处理的详细过程

### 5.1 生成和保存原始日志

生产 Agent 执行过程中持续产生事件。原始事件应先进入不可变日志存储，例如对象存储、日志
平台或专用原始事件表。

原始日志不要直接写进评测结果表。评测结果必须能够追溯到原始日志，但结果库只保存安全
标识和统计，不保存 query、工具参数、工具响应正文或模型思维过程。

每条日志至少需要携带：

- `trace_id` 或能够稳定组成 Trace ID 的字段。
- `mode`：`chat` 或 `research`。
- `schema_version`。
- 事件时间。
- 原始日志存储位置或事件偏移量。
- Trace 是否完结的状态。

### 5.2 判断 Trace 是否完整

不能在日志尚未结束时计算最终指标，否则工具响应、最终回答和 Token 使用量可能还没有到齐。

聊天模式建议以明确的 Trace 完结事件或 canvas 完整落盘作为触发条件。研究模式建议以
`turn.end` 或等价终态事件作为触发条件。

如果生产系统无法保证完结事件，可以使用静默窗口，例如某个 Trace 在一段时间内没有新事件后
进入评测，但仍应记录这种推断方式，并允许迟到事件触发重算。

### 5.3 创建评测任务

Trace 完结后创建评测任务。任务中只保存定位信息，不复制原始正文。

建议任务字段：

```json
{
  "trace_id": "stable-trace-id",
  "mode": "chat",
  "source_uri": "object-storage-or-log-reference",
  "source_schema_version": "canvas-v2",
  "evaluation_run_id": "uuid",
  "requested_metric_set_version": "2026-09",
  "trigger": "trace_completed"
}
```

消息队列通常提供至少一次投递，因此消费者必须允许同一个任务被重复收到。

### 5.4 读取并规范化日志

评测 Worker 根据 `source_uri` 读取完整日志，然后进入现有处理链：

```text
ingestion -> normalization -> evaluation -> aggregation -> reporting
```

其中：

- ingestion 负责读取对象存储、日志 API、消息事件或数据库记录。
- normalization 负责把聊天和研究日志转换成各 evaluator 的稳定输入。
- evaluation 运行确定性指标和 Judge 指标。
- aggregation 计算跨 Trace 微平均、分布和状态统计。
- reporting 提供查询、报表或监控数据。

实时场景只需要增加新的 input adapter，不应修改指标公式来适配传输方式。

### 5.5 执行指标

每个指标独立运行并隔离失败。一个指标失败，不应阻止其他指标落库。

执行顺序建议为：

1. 校验日志 schema 和 Trace 完整性。
2. 运行无需 Judge 的确定性指标。
3. 生成需要 Judge 的候选。
4. 对候选调用版本固定的 Judge。
5. 严格校验 Judge 输出。
6. 生成不含敏感正文的 `EvaluationResult`。
7. 分指标写入结果表。

结果状态至少区分：

- `computed`：成功计算并有数值。
- `not_computable`：输入不足、分母不完整或 Judge 无法完整判定。
- `error`：输入结构或程序契约异常。

不能把 `not_computable` 写成零，也不能因为某个批次失败而保存部分分数。

### 5.6 幂等写入

同一条 Trace、同一指标和同一 evaluator 版本只能有一条确定的结果。建议使用以下幂等键：

```text
trace_id
+ metric_name
+ evaluator_version
+ evaluation_run_id 或 input_revision
```

如果希望保留每次运行历史，使用：

```text
evaluation_run_id + trace_id + metric_name + evaluator_version
```

如果只保留当前有效结果，另建一张 current 表或使用受控 upsert，不要覆盖历史审计记录。

推荐先写入一次运行记录，再批量写入各指标结果，最后将运行状态更新为成功、部分成功或失败。

### 5.7 聚合和报表

聚合层只统计 `computed` 结果，并单独展示不可计算率和错误率。

例如陈述支持率应同时展示：

- 成功计算 Trace 数。
- `NOT_COMPUTABLE` Trace 数及原因分布。
- supported 与 unsupported 的总计数。
- not_assessable 总计数与 `automatic_assessment_coverage`。
- 基于完整可计算分母的微平均。
- evaluator、规则集和证据工具目录版本；仅实际使用模型的指标记录 Judge prompt 与模型版本。

只显示成功子集的平均值而不显示覆盖率，会产生误导。

## 6. 推荐数据库结构

数据库应区分“评测运行”“单 Trace 指标结果”和“安全诊断计数”。原始日志正文应留在原始日志
系统，不复制到评测结果库。

### 6.1 evaluation_runs

一行代表一次在线消费、定时批次或历史重算。

建议字段：

- `evaluation_run_id`：主键。
- `run_type`：`realtime`、`scheduled_batch` 或 `backfill`。
- `metric_set_version`：本次启用的指标集合版本。
- `code_version`：Git commit 或镜像版本。
- `started_at`、`finished_at`。
- `status`：`running`、`succeeded`、`partial_failed`、`failed`。
- `input_count`、`computed_count`、`not_computable_count`、`error_count`。
- `created_by`：服务或调度任务标识，不保存个人密钥。

### 6.2 evaluation_results

一行代表一条 Trace 的一个指标结果。

建议字段：

- `evaluation_run_id`。
- `trace_id`。
- `mode`。
- `metric_name`。
- `evaluator_version`。
- `status`。
- `metric_value`，不可计算时必须为 `NULL`。
- `reason_code`。
- `source_schema_version`。
- `input_revision` 或原始日志内容摘要。
- `evaluated_at`。
- `safe_metadata`：只允许白名单诊断字段。

推荐唯一约束：

```text
(evaluation_run_id, trace_id, metric_name, evaluator_version)
```

### 6.3 evaluation_diagnostics

如果诊断字段需要单独查询，可以拆成结构化诊断表：

- `evaluation_run_id`。
- `trace_id`。
- `metric_name`。
- `diagnostic_name`。
- `diagnostic_value`。

只允许写入计数、状态、版本和安全 reason code，不允许写入 query、工具参数、证据正文、最终
回答或 Judge 原始响应。

### 6.4 evaluation_current

可选。保存每个 Trace、指标当前被认可的最新结果，方便业务查询。历史结果仍保留在
`evaluation_results`。

更新 current 结果必须有明确规则，例如：

- 新 evaluator 版本经批准后才能替换旧版本。
- 相同版本只有更高的 `input_revision` 才能替换。
- 历史重算不能无条件覆盖实时结果。

## 7. 如果采用入库 API

建议由数据平台提供批量写入接口，而不是每个指标发一次请求。

示例：

```http
POST /internal/evaluation-runs/{evaluation_run_id}/results:batchUpsert
Authorization: Bearer <service-credential>
Idempotency-Key: <stable-key>
Content-Type: application/json
```

请求体只包含安全结果：

```json
{
  "code_version": "git-commit-or-image-tag",
  "results": [
    {
      "trace_id": "stable-trace-id",
      "mode": "chat",
      "metric_name": "statement_support_rate",
      "evaluator_version": "3.0.0",
      "status": "computed",
      "metric_value": 0.87,
      "reason_code": null,
      "source_schema_version": "canvas-v2",
      "input_revision": "safe-source-digest",
      "safe_metadata": {
        "verifiable_claim_count": 100,
        "supported_claim_count": 87,
        "unsupported_claim_count": 13,
        "judge_version": "rules-1.0.0",
        "prompt_version": "not-applicable",
        "automatic_assessment_coverage": 0.94,
        "evidence_scope_version": "approved-scope-version"
      }
    }
  ]
}
```

接口需要保证：

- 鉴权和最小权限。
- 请求大小限制。
- 字段白名单和类型校验。
- 幂等键校验。
- 单批事务或明确的逐条失败结果。
- 不接受原始日志正文。
- 返回可安全记录的错误码，不返回数据库内部敏感信息。

## 8. 如果采用数据库直写

DBA 应创建专用账号，例如 `evaluation_writer`，权限仅包括：

- 查询必要的版本表。
- 向运行表和结果表插入数据。
- 对指定唯一键执行受控 upsert。
- 不允许建表、删表、修改索引或访问业务用户表。

应用侧需要：

- 使用密钥管理服务注入数据库凭证。
- 使用参数化 SQL，禁止拼接原始字段。
- 使用连接池并设置连接、事务和语句超时。
- 一批结果在同一事务内写入。
- 对死锁、连接中断等瞬态错误做有限次数重试。
- 不对数据约束错误无限重试。

## 9. 实时、准实时和批处理的选择

### 9.1 实时事件驱动

Trace 完结后立即进入消息队列，Worker 通常在秒级或分钟级完成评测。

适合：

- 需要快速质量告警。
- 日志平台能够发出可靠完结事件。
- Judge 调用量和预算可控。

### 9.2 准实时微批

每 5 至 15 分钟查询新增的完整 Trace，组成一个小批次处理。

适合：

- 暂时没有消息队列。
- 对分钟级延迟可以接受。
- 希望减少 Judge 请求和数据库写入次数。

这是从本地文件模式迁移到生产环境时最容易落地的第一步。

### 9.3 离线批处理

每天或每小时扫描增量分区，适合历史统计、全量重算和低优先级指标。

实时告警和昂贵的全量重算可以分开：确定性指标实时运行，Judge 指标进入限流队列或定时批次。

## 10. 迟到事件、重试和重算

### 10.1 迟到事件

如果 Trace 已计算后又收到新的工具结果或 `turn.end`，生成新的 `input_revision` 并创建重算任务。
旧结果保留，不原地静默修改。

### 10.2 任务重试

建议区分：

- 瞬态失败：数据库断连、Judge 超时、网络错误，可以有限次数重试。
- 永久输入问题：缺少 query、结构冲突、证据无法完整拆分，不应自动无限重试。
- Judge 非法输出：可以按照指标契约允许的次数重试，仍失败则落库为
  `not_computable / invalid_judge_output`。
- 程序异常：落库为安全错误状态并进入告警或死信队列。

### 10.3 版本重算

以下变化应创建新的评测运行，而不是覆盖旧结果：

- evaluator 版本变化。
- Judge prompt 版本变化。
- Judge 模型或供应商变化。
- MCP 手册版本变化。
- 证据工具目录版本变化。
- 日志 normalization 规则发生影响结果的变化。

## 11. 监控与告警

至少监控：

- 待处理任务数量和最老任务等待时间。
- 每分钟处理 Trace 数。
- `computed`、`not_computable`、`error` 比例。
- 各 reason code 的数量变化。
- Judge 超时率、非法输出率和请求耗时。
- 数据库写入失败率和事务耗时。
- 相同任务重复投递次数。
- 每个指标的计算覆盖率。
- evaluator、模型和 prompt 版本分布。

如果某个指标的分数正常但计算覆盖率突然下降，也应触发告警。

## 12. 安全要求

- 原始 Trace 保持不可变，并保存在受控日志系统中。
- 评测结果库不保存原始 query、工具参数、工具响应、最终回答、证据正文和 Judge 原始响应。
- `safe_metadata` 使用字段白名单，不能接受任意字典直接落库。
- 日志存储凭证、Judge 密钥和数据库凭证分别管理。
- 所有服务间调用使用服务身份，不使用个人账号。
- 对日志读取、重算和结果覆盖操作保留审计记录。
- 生产环境的证据工具目录必须经过审批并版本化，不能继续使用临时目录作为正式口径。

## 13. 推荐落地步骤

### 第一阶段：确认契约

1. 与日志平台确认聊天和研究模式的生产 schema。
2. 确认 Trace 完结信号和迟到事件处理方式。
3. 确认原始日志存储位置和读取权限。
4. 确认生产指标集合、版本和 Judge 预算。
5. 确认生产证据工具目录。

### 第二阶段：与 DBA 确认数据库边界

1. 提交结果字段字典和数据保留周期。
2. 确定使用入库 API 还是数据库最小权限账号。
3. DBA 审核表、索引、分区和唯一约束。
4. DBA 创建测试环境和服务身份。
5. 完成敏感字段扫描和权限验证。

### 第三阶段：实现生产入口

1. 新增生产日志 input adapter。
2. 新增任务消费者或微批扫描器。
3. 复用现有 collector 和 evaluator。
4. 新增结果 repository 或入库 API client。
5. 实现幂等、重试、死信和运行状态记录。
6. 增加结构化日志和监控指标。

### 第四阶段：影子运行

1. 生产日志只读接入，不影响线上请求。
2. 结果写入测试表或影子表。
3. 将同一批日志的实时结果与本地 JSON 重算结果比较。
4. 检查 Trace 数、状态分布、分母和版本是否一致。
5. 检查数据库中没有敏感正文。

### 第五阶段：正式上线

1. 开启正式结果表写入。
2. 保留影子比对一段时间。
3. 为积压、错误率、Judge 失败和覆盖率配置告警。
4. 固化发布、迁移和回滚流程。
5. 将历史 JSON 通过 `backfill` 运行类型补录，避免与实时结果混淆。

## 14. 当前项目建议新增的组件

结合当前项目结构，后续实现时可以增加：

```text
src/collectors/
  production_trace_source.py      # 从日志 API、对象存储或消息中读取 Trace

src/evaluation/
  runner.py                       # 编排单 Trace 的多个 evaluator
  worker.py                       # 消费实时任务并隔离失败

src/storage/
  evaluation_repository.py        # 结果存储抽象
  database_repository.py          # 数据库直写实现
  evaluation_api_client.py        # 入库 API 实现，可与直写实现二选一

src/reporters/
  metric_aggregation.py           # 跨 Trace 聚合和覆盖率统计

configs/
  production.example.toml         # 不含密钥的生产配置示例
```

具体选用数据库直写还是入库 API 时，只替换 storage 实现，不改变 collector 和 evaluator。

## 15. 最小可行方案

如果希望尽快从本地 JSON 迁移到生产，可以先采用以下方案：

1. 日志平台每 10 分钟提供新增且已完结的 Trace 列表。
2. 一个由评测团队维护的容器化任务读取这些 Trace。
3. 任务复用现有 normalization 和 evaluator。
4. 通过 DBA 提供的最小权限账号批量写入专用评测表。
5. 使用唯一键避免重复入库。
6. 保存运行版本、状态、reason code 和安全计数。
7. Judge 指标限制并发，失败任务进入重试队列。
8. 稳定后再把轮询改成 Trace 完结事件驱动。

这个方案不要求 DBA 运行 Python，也不要求第一天就建设完整的实时平台，同时为后续消息队列、
入库 API 和在线监控保留了清晰的升级路径。
