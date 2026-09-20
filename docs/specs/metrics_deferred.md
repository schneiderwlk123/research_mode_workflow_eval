# 暂缓指标候选（来源与合规层）

> 状态：2026-09-14 评审后暂缓。本文件保存 7 项聚焦"证据来源与答案质量"层的候选指标规格。当前批次转向 Agent 能力四维度（任务完成 / 工具使用 / 推理规划 / 容错纠错，见根目录《初步入库评测指标.md》）。恢复任一项前，按仓库规则重新展示公式、字段映射、异常边界和模拟计算并等待确认。

## A.1 答案证据锚定率（answer_evidence_grounding_rate）

暂缓原因：属答案质量层，不在本批次四维度范围；恢复时的首选项。

- 定义：最终回答中可提取的数值与直接引述，能在本次 Trace 成功工具结果载荷中找到精确出处的比例。**单向包含边界**：找到 = 强支持进分子；找不到 = `not_assessable` 覆盖诊断（可能是计算/换算所得，不判错）。
- 公式：

```text
answer_evidence_grounding_rate =
  grounding_supported_count ÷ assessable_span_count
```

- 字段映射：聊天 `canvas.answer.answer` × 成功 `tool_result[].result/rawResult`；研究 turn.end 前 assistant text × `part.state.output/raw`。正文与匹配仅内存流转。
- 边界：`no_extractable_spans` / `no_successful_tool_evidence` / `empty_final_answer` 不可计算；数值 span 连同紧邻单位提取，归一化后精确匹配，不做语义换算推导。
- 埋点：无强制依赖；埋点 B（引用结构）落地后升级为按引用锚定。
- 价值：零标注、全量、确定性覆盖金融数值风险面；是此前删除的"数值忠实度"的单向口径重构。

## A.2 来源构成画像（source_mix_profile）

暂缓原因：依赖业务确认的工具-来源映射表，尚未启动。

- 定义：回答依赖证据的来源类型分布（结构化数据库 / 路演纪要 / 网络文章）。
- 归因路径：`toolName → mcp_tool_manual.json → 工具-来源类型映射表`（版本化业务确认项）；未登记工具进 `unmapped_source_tools` 诊断，不猜测归类。
- 字段映射：双模式复用调用唯一化口径，工具级 DIRECTLY_COMPUTABLE（映射表确认后）；块级归因需埋点 A。
- 价值：按来源切片一切质量指标的地基；数字型问题大量依赖网络文章来源本身即风险信号。

## A.3 召回-利用比（recall_to_use_ratio）

暂缓原因：临时口径依赖 Embedding"使用"判定，灰区重；权威口径需埋点 B。

- 定义：召回证据单元中被最终回答实际使用的比例，区分检索失败（该召回的没召回）与整合失败（召回了一堆全没用）。
- 临时口径：复用 statement_support 证据抽取 + Embedding 相似度判定"使用"，灰区不计分母；权威口径：回答引用 `citations[].source_id` 直接判定。
- 与已取消的上下文膨胀率严格区分：后者衡量 Token 规模增长，本指标衡量证据选择行为。
- 价值：低利用 + 大召回 = 整合失败；低利用 + 小召回 = 检索失败，修复责任方不同。

## A.4 证据时效偏差（evidence_freshness）

暂缓原因：数据日期级依赖埋点 A/D，窗口级价值需与业务确认。

- 定义：回答所依据数据的数据日期相对 query 时间的偏差，分两层级：

```text
窗口级（确定性，可先行）：requested_window_days / window_offset_days，读 arguments 显式时间参数
数据日期级（需埋点）：staleness_days = query 日期 - 数据实际日期（period_end 或 source_date）
```

- 与 time_range_accuracy 不重叠：该指标管模型是否理解时间要求，本指标管数据本身是否过旧。
- 价值：暴露"用去年的价格答今天的问题"，报告级可输出"依赖超过 N 天旧数据的 Trace 占比"。

## A.5 预期/实际口径混淆（guidance_actual_confusion）

暂缓原因：Judge 类指标；预期/实际词典需业务确认。

- 定义：路演纪要等来源中的预期/指引数字被当作已披露实际值陈述（或反之）的混淆检出率。
- 两阶段：版本化词典确定性预筛（预计/指引/展望 vs 已实现/披露）+ 结构化 Judge 判定数值相近语句对。
- 价值：路演纪要场景特有高风险错误，直接影响投资判断，风险等级对齐 v1.8 的 F_high 性质。

## A.6 跨来源冲突信号率（cross_source_conflict_rate）

暂缓原因：实体对齐灰区重；来源类型区分依赖 A.2 映射表。

- 定义：不同来源对同一（实体, 指标, 期间）三元组给出不同数值的信号率。只让冲突可见，不裁决来源对错。
- 抽取复用 deterministic_statement_support 的数值/时间/实体匹配；单位归一后按业务容差比较；不可对齐样本计 `not_assessable`。
- 价值：暴露"数据库与网络文章打架被 LLM 静默缝合"这一整合层最隐蔽的失败。

## A.7 合规风险信号率（compliance_signal_rate）

暂缓原因：合规词典与处置口径需合规确认，未确认前不得编码。

- 定义：回答命中合规敏感模式的信号率（无免责声明的前瞻性陈述、个股买卖建议措辞、PII/密钥模式）。信号率非违规率：命中进人工复核队列，不走加权平均。
- 字段映射：answer 文本内存扫描，双模式 DIRECTLY_COMPUTABLE（词典确认后）。
- 价值：客户端金融产品硬底线；v1.8 合规红线的规则化前置。
