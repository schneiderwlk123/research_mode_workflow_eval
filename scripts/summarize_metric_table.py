"""把已注册指标汇总为一行一个 Trace 的 CSV 宽表。"""

import argparse
import hashlib
import json
from collections import defaultdict
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, TypeGuard

from collectors.answer_evidence_grounding import (
    normalize_chat_answer_grounding,
    normalize_research_answer_grounding,
)
from collectors.api_retry import (
    normalize_chat_api_retry,
    normalize_research_api_retry,
)
from collectors.cache_input_token_ratio import (
    normalize_chat_cache_input_token_ratio,
    normalize_research_cache_input_token_ratio,
)
from collectors.compaction_event_count import (
    normalize_chat_compaction_events,
    normalize_research_compaction_events,
)
from collectors.continuation_restart import (
    normalize_chat_continuation_restart,
    normalize_research_continuation_restart,
)
from collectors.end_to_end_latency import (
    normalize_chat_end_to_end_latency,
    normalize_research_end_to_end_latency,
)
from collectors.error_recovery import (
    normalize_chat_error_recovery,
    normalize_research_error_recovery,
)
from collectors.llm_error_count import (
    normalize_chat_llm_errors,
    normalize_research_llm_errors,
)
from collectors.max_tool_latency_share import (
    normalize_chat_max_tool_latency_share,
    normalize_research_max_tool_latency_share,
)
from collectors.output_token_ratio import (
    normalize_chat_output_token_ratio,
    normalize_research_output_token_ratio,
)
from collectors.token_consumption import (
    normalize_chat_token_consumption,
    normalize_research_token_consumption,
)
from collectors.tool_call_latency import (
    normalize_chat_tool_call_latency,
    normalize_research_tool_call_latency,
)
from collectors.tool_call_statistics import (
    normalize_chat_tool_call_statistics,
    normalize_research_tool_call_statistics,
)
from collectors.tool_error_rate import (
    normalize_chat_tool_error_rate,
    normalize_research_tool_error_rate,
)
from collectors.tool_step_tokens import (
    normalize_chat_tool_step_tokens,
    normalize_research_tool_step_tokens,
)
from collectors.tool_token_share import (
    normalize_chat_tool_token_share,
    normalize_research_tool_token_share,
)
from collectors.turn_completion import (
    normalize_chat_turn_completion,
    normalize_research_turn_completion,
)
from evaluators.answer_evidence_grounding import AnswerEvidenceGroundingRateEvaluator
from evaluators.api_retry_rate import ApiRetryRateEvaluator
from evaluators.base import EvaluationResult, EvaluationStatus
from evaluators.cache_input_token_ratio import CacheInputTokenRatioEvaluator
from evaluators.compaction_event_count import CompactionEventCountEvaluator
from evaluators.continuation_restart import ContinuationRestartCountEvaluator
from evaluators.end_to_end_latency import EndToEndLatencyEvaluator
from evaluators.error_recovery import ErrorRecoveryRateEvaluator
from evaluators.llm_error_count import LlmErrorCountEvaluator
from evaluators.max_tool_latency_share import MaxToolLatencyShareEvaluator
from evaluators.max_tool_token_share import MaxToolTokenShareEvaluator
from evaluators.output_token_ratio import OutputTokenRatioEvaluator
from evaluators.per_tool_call_latency import PerToolCallLatencyEvaluator
from evaluators.per_tool_call_tokens import PerToolCallTokensEvaluator
from evaluators.token_consumption import TotalTokenConsumptionEvaluator
from evaluators.tool_calls_count import ToolCallsCountEvaluator
from evaluators.tool_error_rate import ToolErrorRateEvaluator
from evaluators.turn_completion import TurnCompletionRateEvaluator
from reporters.metric_table import (
    MetricColumn,
    TraceMetricRecord,
    build_metric_table,
    write_metric_table_csv,
)


def _evaluate_tool_calls_count(record: TraceMetricRecord) -> EvaluationResult:
    evaluator = ToolCallsCountEvaluator()
    if record.mode == "chat" and isinstance(record.payload, Mapping):
        return evaluator.evaluate(normalize_chat_tool_call_statistics(record.payload))
    if record.mode == "research" and _is_sequence(record.payload):
        return evaluator.evaluate(
            normalize_research_tool_call_statistics(
                record.payload,
                session_id=record.session_id or record.log_id,
            )
        )
    return EvaluationResult(
        evaluator.metric_name,
        evaluator.evaluator_version,
        None,
        EvaluationStatus.ERROR,
        {"reason": "invalid_metric_input"},
    )


def _evaluate_per_tool_call_tokens(record: TraceMetricRecord) -> EvaluationResult:
    evaluator = PerToolCallTokensEvaluator()
    if record.mode == "chat" and isinstance(record.payload, Mapping):
        return evaluator.evaluate(normalize_chat_tool_step_tokens(record.payload))
    if record.mode == "research" and _is_sequence(record.payload):
        return evaluator.evaluate(normalize_research_tool_step_tokens(record.payload))
    return EvaluationResult(
        evaluator.metric_name,
        evaluator.evaluator_version,
        None,
        EvaluationStatus.ERROR,
        {"reason": "invalid_metric_input"},
    )


def _evaluate_total_token_consumption(record: TraceMetricRecord) -> EvaluationResult:
    evaluator = TotalTokenConsumptionEvaluator()
    if record.mode == "chat" and isinstance(record.payload, Mapping):
        return evaluator.evaluate(normalize_chat_token_consumption(record.payload))
    if record.mode == "research" and _is_sequence(record.payload):
        return evaluator.evaluate(normalize_research_token_consumption(record.payload))
    return EvaluationResult(
        evaluator.metric_name,
        evaluator.evaluator_version,
        None,
        EvaluationStatus.ERROR,
        {"reason": "invalid_metric_input"},
    )


def _evaluate_max_tool_token_share(record: TraceMetricRecord) -> EvaluationResult:
    evaluator = MaxToolTokenShareEvaluator()
    if record.mode == "chat" and isinstance(record.payload, Mapping):
        return evaluator.evaluate(normalize_chat_tool_token_share(record.payload))
    if record.mode == "research" and _is_sequence(record.payload):
        return evaluator.evaluate(normalize_research_tool_token_share(record.payload))
    return EvaluationResult(
        evaluator.metric_name,
        evaluator.evaluator_version,
        None,
        EvaluationStatus.ERROR,
        {"reason": "invalid_metric_input"},
    )


def _evaluate_output_token_ratio(record: TraceMetricRecord) -> EvaluationResult:
    evaluator = OutputTokenRatioEvaluator()
    if record.mode == "chat" and isinstance(record.payload, Mapping):
        return evaluator.evaluate(normalize_chat_output_token_ratio(record.payload))
    if record.mode == "research" and _is_sequence(record.payload):
        return evaluator.evaluate(normalize_research_output_token_ratio(record.payload))
    return EvaluationResult(
        evaluator.metric_name,
        evaluator.evaluator_version,
        None,
        EvaluationStatus.ERROR,
        {"reason": "invalid_metric_input"},
    )


def _evaluate_cache_input_token_ratio(record: TraceMetricRecord) -> EvaluationResult:
    evaluator = CacheInputTokenRatioEvaluator()
    if record.mode == "chat" and isinstance(record.payload, Mapping):
        return evaluator.evaluate(normalize_chat_cache_input_token_ratio(record.payload))
    if record.mode == "research" and _is_sequence(record.payload):
        return evaluator.evaluate(normalize_research_cache_input_token_ratio(record.payload))
    return EvaluationResult(
        evaluator.metric_name,
        evaluator.evaluator_version,
        None,
        EvaluationStatus.ERROR,
        {"reason": "invalid_metric_input"},
    )


def _evaluate_per_tool_call_latency(record: TraceMetricRecord) -> EvaluationResult:
    evaluator = PerToolCallLatencyEvaluator()
    if record.mode == "chat" and isinstance(record.payload, Mapping):
        return evaluator.evaluate(normalize_chat_tool_call_latency(record.payload))
    if record.mode == "research" and _is_sequence(record.payload):
        return evaluator.evaluate(
            normalize_research_tool_call_latency(
                record.payload,
                session_id=record.session_id,
                user_message_id=record.log_id,
            )
        )
    return EvaluationResult(
        evaluator.metric_name,
        evaluator.evaluator_version,
        None,
        EvaluationStatus.ERROR,
        {"reason": "invalid_metric_input"},
    )


def _evaluate_end_to_end_latency(record: TraceMetricRecord) -> EvaluationResult:
    evaluator = EndToEndLatencyEvaluator()
    if record.mode == "chat" and isinstance(record.payload, Mapping):
        return evaluator.evaluate(normalize_chat_end_to_end_latency(record.payload))
    if record.mode == "research" and _is_sequence(record.payload):
        return evaluator.evaluate(normalize_research_end_to_end_latency(record.payload))
    return EvaluationResult(
        evaluator.metric_name,
        evaluator.evaluator_version,
        None,
        EvaluationStatus.ERROR,
        {"reason": "invalid_metric_input"},
    )


def _evaluate_max_tool_latency_share(record: TraceMetricRecord) -> EvaluationResult:
    evaluator = MaxToolLatencyShareEvaluator()
    if record.mode == "chat" and isinstance(record.payload, Mapping):
        return evaluator.evaluate(normalize_chat_max_tool_latency_share(record.payload))
    if record.mode == "research" and _is_sequence(record.payload):
        return evaluator.evaluate(
            normalize_research_max_tool_latency_share(
                record.payload,
                session_id=record.session_id,
                user_message_id=record.log_id,
            )
        )
    return EvaluationResult(
        evaluator.metric_name,
        evaluator.evaluator_version,
        None,
        EvaluationStatus.ERROR,
        {"reason": "invalid_metric_input"},
    )


def _evaluate_answer_evidence_grounding(record: TraceMetricRecord) -> EvaluationResult:
    """评估单条日志的数值锚定率；研究模式要求 payload 为单回合事件序列。

    回合切分是入口层职责：多回合文件必须先按 sessionID 分组、再按 turn.start
    切分为逐回合序列后送入，否则跨回合证据会互相污染锚定结果。
    """

    evaluator = AnswerEvidenceGroundingRateEvaluator()
    if record.mode == "chat" and isinstance(record.payload, Mapping):
        return evaluator.evaluate(normalize_chat_answer_grounding(record.payload))
    if record.mode == "research" and _is_sequence(record.payload):
        return evaluator.evaluate(normalize_research_answer_grounding(record.payload))
    return EvaluationResult(
        evaluator.metric_name,
        evaluator.evaluator_version,
        None,
        EvaluationStatus.ERROR,
        {"reason": "invalid_metric_input"},
    )


def _evaluate_turn_completion(record: TraceMetricRecord) -> EvaluationResult:
    """评估单条日志的回合完成 0/1 标记；研究模式要求 payload 为单回合事件序列。

    回合切分是入口层职责（与数值锚定率一致）：续跑段必须先按 sessionID 分组、
    同 userMessageID 合并后送入。聊天模式为 answer 存在性代理口径，语义标签
    随结果元数据输出。
    """

    evaluator = TurnCompletionRateEvaluator()
    if record.mode == "chat" and isinstance(record.payload, Mapping):
        return evaluator.evaluate(normalize_chat_turn_completion(record.payload))
    if record.mode == "research" and _is_sequence(record.payload):
        return evaluator.evaluate(normalize_research_turn_completion(record.payload))
    return EvaluationResult(
        evaluator.metric_name,
        evaluator.evaluator_version,
        None,
        EvaluationStatus.ERROR,
        {"reason": "invalid_metric_input"},
    )


def _evaluate_continuation_restart(record: TraceMetricRecord) -> EvaluationResult:
    """评估单条日志的续跑重启次数；研究模式要求 payload 为单回合事件序列。

    回合切分是入口层职责（与回合完成率一致）：`_noquery` 续跑段必须先按
    sessionID 分组、同 userMessageID 合并后送入，否则跨段 turn.start 会丢失
    重启计数。聊天模式恒 NOT_COMPUTABLE / no_continuation_mechanism。
    """

    evaluator = ContinuationRestartCountEvaluator()
    if record.mode == "chat":
        return evaluator.evaluate(normalize_chat_continuation_restart(record.payload))
    if record.mode == "research" and _is_sequence(record.payload):
        return evaluator.evaluate(normalize_research_continuation_restart(record.payload))
    return EvaluationResult(
        evaluator.metric_name,
        evaluator.evaluator_version,
        None,
        EvaluationStatus.ERROR,
        {"reason": "invalid_metric_input"},
    )


def _evaluate_llm_error_count(record: TraceMetricRecord) -> EvaluationResult:
    """评估单条日志的模型层错误数与分类；研究模式要求 payload 为单回合事件序列。

    回合切分是入口层职责（与数值锚定率一致）；单元格为同时承载整数主值与
    error_classes 分类数组的单字段紧凑 JSON。聊天 canvas 无模型层错误事件通道，
    固定 NOT_COMPUTABLE / no_model_error_events，单元格留空，不用 0 冒充无错误。
    """

    evaluator = LlmErrorCountEvaluator()
    if record.mode == "chat" and isinstance(record.payload, Mapping):
        return evaluator.evaluate(normalize_chat_llm_errors(record.payload))
    if record.mode == "research" and _is_sequence(record.payload):
        return evaluator.evaluate(normalize_research_llm_errors(record.payload))
    return EvaluationResult(
        evaluator.metric_name,
        evaluator.evaluator_version,
        None,
        EvaluationStatus.ERROR,
        {"reason": "invalid_metric_input"},
    )


def _evaluate_tool_error_rate(record: TraceMetricRecord) -> EvaluationResult:
    """评估单条日志的工具错误率；研究模式要求 payload 为单回合事件序列。

    回合切分是入口层职责（与回合完成率一致）。聊天为载荷代理终态口径
    （payload_proxy_v1），研究为显式终态口径（explicit_status_v1），单元格为
    同时承载比率、分子分母与四类归因计数的单字段紧凑 JSON；两种语义禁止
    混比，terminal_semantics 随结果元数据声明。
    """

    evaluator = ToolErrorRateEvaluator()
    if record.mode == "chat" and isinstance(record.payload, Mapping):
        return evaluator.evaluate(normalize_chat_tool_error_rate(record.payload))
    if record.mode == "research" and _is_sequence(record.payload):
        return evaluator.evaluate(
            normalize_research_tool_error_rate(
                record.payload,
                session_id=record.session_id,
                user_message_id=record.log_id,
            )
        )
    return EvaluationResult(
        evaluator.metric_name,
        evaluator.evaluator_version,
        None,
        EvaluationStatus.ERROR,
        {"reason": "invalid_metric_input"},
    )


def _evaluate_error_recovery(record: TraceMetricRecord) -> EvaluationResult:
    """评估单条日志的错误自恢复标记；研究模式要求 payload 为单回合事件序列。

    回合切分是入口层职责（与回合完成率一致）。聊天无错误事件通道恒
    NOT_COMPUTABLE / no_error_events_observable（禁止载荷代理）；研究为 5.1 的
    降级实现（recovery_semantics = degraded_v1），单元格为承载 error_recovered
    主值、had_error 与两层错误计数、loop 级重试配对计数的单字段紧凑 JSON；
    had_error = 0 的回合写空（不进报告级分母，不得记 1）。
    """

    evaluator = ErrorRecoveryRateEvaluator()
    if record.mode == "chat" and isinstance(record.payload, Mapping):
        return evaluator.evaluate(normalize_chat_error_recovery(record.payload))
    if record.mode == "research" and _is_sequence(record.payload):
        return evaluator.evaluate(normalize_research_error_recovery(record.payload))
    return EvaluationResult(
        evaluator.metric_name,
        evaluator.evaluator_version,
        None,
        EvaluationStatus.ERROR,
        {"reason": "invalid_metric_input"},
    )


def _evaluate_compaction_event_count(record: TraceMetricRecord) -> EvaluationResult:
    """评估单条日志的上下文压缩事件数；研究模式要求 payload 为单回合事件序列。

    回合切分是入口层职责（与 7.4 一致）：多回合文件必须先按 sessionID 分组、
    续跑段合并后送入。聊天模式无压缩事件通道，恒 NOT_COMPUTABLE /
    no_compaction_events（通道不存在不等于无压缩，禁止记 0）。
    """

    evaluator = CompactionEventCountEvaluator()
    if record.mode == "chat" and isinstance(record.payload, Mapping):
        return evaluator.evaluate(normalize_chat_compaction_events(record.payload))
    if record.mode == "research" and _is_sequence(record.payload):
        return evaluator.evaluate(normalize_research_compaction_events(record.payload))
    return EvaluationResult(
        evaluator.metric_name,
        evaluator.evaluator_version,
        None,
        EvaluationStatus.ERROR,
        {"reason": "invalid_metric_input"},
    )


def _evaluate_api_retry_rate(record: TraceMetricRecord) -> EvaluationResult:
    """评估单条日志的框架 API 重试率；研究模式要求 payload 为单回合事件序列。

    回合切分是入口层职责（与 7.4/7.6 一致）。聊天 tool_result 无 API 调用
    明细通道，恒 NOT_COMPUTABLE / no_tool_debug_events（通道不存在不等于无
    重试，禁止记 0）；研究单元格为承载比率、分子分母、业务错误数、不可
    判定数与覆盖率的单字段紧凑 JSON（覆盖率必须随值输出，tool.debug 仅
    覆盖部分工具）。本指标计量工具内部传输/业务层框架重试，不是埋点 D
    的模型级重试，两者禁止互相代理。
    """

    evaluator = ApiRetryRateEvaluator()
    if record.mode == "chat" and isinstance(record.payload, Mapping):
        return evaluator.evaluate(normalize_chat_api_retry(record.payload))
    if record.mode == "research" and _is_sequence(record.payload):
        return evaluator.evaluate(normalize_research_api_retry(record.payload))
    return EvaluationResult(
        evaluator.metric_name,
        evaluator.evaluator_version,
        None,
        EvaluationStatus.ERROR,
        {"reason": "invalid_metric_input"},
    )


def _topk(counter: dict[str, int], k: int = 5) -> list[dict[str, object]]:
    """把计数字典压成 Top-K 清单(按次数降序、同名稳定序),供归因单元格使用。"""
    items = sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))[:k]
    return [{"name": name, "count": cnt} for name, cnt in items]


def _counter_items(counter: Mapping[str, int], k: int | None) -> list[dict[str, object]]:
    """把计数字典压成 `[{name, count}]` 清单:次数降序,同名次按名称升序。

    `k=None` 表示不截断。两种策略共用这一个排序实现,避免两列口径漂移:
    `api_retry_attribution` 取 Top5(来源集中度高,长尾无解读价值);
    `tool_error_attribution` 全量(长尾正是要定位的对象)。
    """

    items: list[tuple[str, int]] = sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))
    if k is not None:
        items = items[:k]
    return [{"name": name, "count": cnt} for name, cnt in items]


def _topk_all(counter: Mapping[str, int]) -> list[dict[str, object]]:
    """与 `_topk` 同序但不截断,用于需保留全部长尾条目的归因单元格。"""

    return _counter_items(counter, None)


def build_attribution_cell(attribution: dict[str, object] | None) -> str:
    """把框架层归因 dict 压成单元素 JSON 数组单元格(与宽表其它 JSON 列同形态)。

    键序固定 by_tool / by_endpoint / retry_after_success;空归因返回空串(该列留空)。
    2026-09-24 需求方确认:归因仅框架层,Top5。
    """
    if not attribution:
        return ""
    bt = attribution.get("by_tool") or {}
    be = attribution.get("by_endpoint") or {}
    payload = {
        "by_tool": _topk(bt if isinstance(bt, dict) else {}),
        "by_endpoint": _topk(be if isinstance(be, dict) else {}),
        "retry_after_success": attribution.get("retry_after_success", 0),
    }
    return json.dumps([payload], ensure_ascii=False, separators=(",", ":"))


def _evaluate_api_retry_attribution(record: TraceMetricRecord) -> EvaluationResult:
    """评估单条日志的框架层重试归因,产出宽表独立列 api_retry_attribution。

    复用 api_retry 的规范化与评测器,从 metadata 读归因(by_tool / by_endpoint /
    retry_after_success)压成单元格;不可计算(聊天无通道 / 无 tool.debug)留空。
    归因仅框架层(同 callID 内同指纹重复),不含模型层跨 callID 重复。
    """
    evaluator = ApiRetryRateEvaluator()
    if record.mode == "chat" and isinstance(record.payload, Mapping):
        result = evaluator.evaluate(normalize_chat_api_retry(record.payload))
    elif record.mode == "research" and _is_sequence(record.payload):
        result = evaluator.evaluate(normalize_research_api_retry(record.payload))
    else:
        return EvaluationResult(
            "api_retry_attribution",
            evaluator.evaluator_version,
            None,
            EvaluationStatus.ERROR,
            {"reason": "invalid_metric_input"},
        )
    if result.status is not EvaluationStatus.COMPUTED:
        return EvaluationResult(
            "api_retry_attribution",
            result.evaluator_version,
            None,
            result.status,
            result.metadata,
        )
    attr = result.metadata.get("api_retry_attribution")
    return EvaluationResult(
        "api_retry_attribution",
        result.evaluator_version,
        build_attribution_cell(attr if isinstance(attr, dict) else None),
        result.status,
        result.metadata,
    )


def build_tool_error_attribution_cell(
    error_calls: object,
) -> str:
    """把逐次工具错误明细压成单元素 JSON 数组单元格。

    `error_calls` 为评测器 metadata 的 `(tool_name, attribution)` 元组序列;
    空序列(无工具错误)返回空串,该列留空——空不等于零。

    键序固定 `by_tool` / `by_attribution`。**不做 Top-K 截断**(与
    `api_retry_attribution` 的 Top5 口径刻意不同,2026-09-24 需求方确认):
    本样本 `error_call_count` 中位 2、p99 17、最大 59,截断只会砍掉"撞墙最严重"
    的那几条长尾日志的尾部工具,而定位失败工具正是本列的唯一目的。
    by_tool 与 by_attribution 各自的计数之和恒等于错误总数,供与值列的
    `error_call_count` 交叉校验。

    缺工具名或归因时用占位符保守计数,不丢弃该次错误(丢弃会破坏上述守恒)。
    """

    if not isinstance(error_calls, (list, tuple)) or not error_calls:
        return ""
    by_tool: dict[str, int] = defaultdict(int)
    by_attribution: dict[str, int] = defaultdict(int)
    for call in error_calls:
        if not isinstance(call, Mapping):
            continue
        tool = call.get("tool_name")
        attribution = call.get("attribution")
        by_tool[tool if isinstance(tool, str) and tool else "<unmapped>"] += 1
        by_attribution[
            attribution if isinstance(attribution, str) and attribution else "unclassified"
        ] += 1
    payload = {
        "by_tool": _topk_all(by_tool),
        "by_attribution": _topk_all(by_attribution),
    }
    return json.dumps([payload], ensure_ascii=False, separators=(",", ":"))


def _evaluate_tool_error_attribution(record: TraceMetricRecord) -> EvaluationResult:
    """评估单条日志的工具错误归因,产出宽表独立列 tool_error_attribution。

    复用 `tool_error_rate` 的规范化与评测器,从 metadata 读 `error_calls`
    (逐次失败的工具名与归因)压成单元格;聊天与研究同一路径,因为评测器的
    metadata 在两个模式下都产出该字段。不可计算(无工具调用 / 无终态 /
    结构违约)时透传状态,该列留空。

    与 `api_retry_attribution` 的关系:后者定位"框架层重复请求打到了哪个工具/
    接口",本列定位"工具调用最后失败在哪个工具"——两者是不同层的证据,
    不可互相代理。
    """

    evaluator = ToolErrorRateEvaluator()
    if record.mode == "chat" and isinstance(record.payload, Mapping):
        result = evaluator.evaluate(normalize_chat_tool_error_rate(record.payload))
    elif record.mode == "research" and _is_sequence(record.payload):
        result = evaluator.evaluate(
            normalize_research_tool_error_rate(
                record.payload,
                session_id=record.session_id,
                user_message_id=record.log_id,
            )
        )
    else:
        return EvaluationResult(
            "tool_error_attribution",
            evaluator.evaluator_version,
            None,
            EvaluationStatus.ERROR,
            {"reason": "invalid_metric_input"},
        )
    if result.status is not EvaluationStatus.COMPUTED:
        return EvaluationResult(
            "tool_error_attribution",
            result.evaluator_version,
            None,
            result.status,
            result.metadata,
        )
    return EvaluationResult(
        "tool_error_attribution",
        result.evaluator_version,
        build_tool_error_attribution_cell(result.metadata.get("error_calls")),
        result.status,
        result.metadata,
    )


def build_metric_columns() -> tuple[MetricColumn, ...]:
    """构造全部已注册指标列；列集变更必须同批更新 docs/arch/code-map.md。

    `per_tool_repeat_call_count` 与 `mcp_tool_call_inflation_rate` 已于 2026-09-20
    按需求退出宽表注册（评测器与测试保留），MCP 手册目录加载与 `--mcp-manual`
    入口随膨胀率列一并移除，宽表入口不再依赖 `mcp_tool_manual.json`。
    `citation_traceability_rate` 已于 2026-09-21 按需求退出宽表注册（评测器、
    collector 与测试保留）。
    """

    return (
        MetricColumn("tool_calls_count", _evaluate_tool_calls_count),
        MetricColumn("per_tool_call_tokens", _evaluate_per_tool_call_tokens),
        MetricColumn("total_token_consumption", _evaluate_total_token_consumption),
        MetricColumn("max_tool_token_share", _evaluate_max_tool_token_share),
        MetricColumn("output_token_ratio", _evaluate_output_token_ratio),
        MetricColumn("cache_input_token_ratio", _evaluate_cache_input_token_ratio),
        MetricColumn("per_tool_call_latency_ms", _evaluate_per_tool_call_latency),
        MetricColumn("end_to_end_latency_ms", _evaluate_end_to_end_latency),
        MetricColumn("max_tool_latency_share", _evaluate_max_tool_latency_share),
        MetricColumn("answer_evidence_grounding_rate", _evaluate_answer_evidence_grounding),
        MetricColumn("turn_completion_rate", _evaluate_turn_completion),
        MetricColumn("continuation_restart_count", _evaluate_continuation_restart),
        MetricColumn("llm_error_count", _evaluate_llm_error_count),
        MetricColumn("tool_error_rate", _evaluate_tool_error_rate),
        MetricColumn("tool_error_attribution", _evaluate_tool_error_attribution),
        MetricColumn("error_recovery_rate", _evaluate_error_recovery),
        MetricColumn("compaction_event_count", _evaluate_compaction_event_count),
        MetricColumn("api_retry_rate", _evaluate_api_retry_rate),
        MetricColumn("api_retry_attribution", _evaluate_api_retry_attribution),
    )


# 默认列在模块导入时构建一次，流式入口复用同一注册表。
METRIC_COLUMNS: tuple[MetricColumn, ...] = build_metric_columns()


def summarize_logs(
    *,
    chat_paths: Sequence[Path] = (),
    research_paths: Sequence[Path] = (),
    output_path: Path,
) -> dict[str, int]:
    """读取日志、运行已注册指标并写出宽表，返回不含正文的运行摘要。"""

    records: list[TraceMetricRecord] = []
    for path in chat_paths:
        payload = _load_json(path)
        if not isinstance(payload, list):
            raise ValueError(f"聊天日志必须是 JSON 数组: {path}")
        source_key = _source_fingerprint(path, length=12)
        for index, trace in enumerate(payload, start=1):
            trace_id = _chat_trace_id(trace) or f"chat:{source_key}:{index:06d}"
            user_id, session_id = _chat_identity(trace)
            records.append(
                TraceMetricRecord(
                    trace_id,
                    "chat",
                    trace,
                    user_id=user_id,
                    session_id=session_id,
                )
            )

    for path in research_paths:
        payload = _load_json(path)
        if not isinstance(payload, list):
            raise ValueError(f"研究日志必须是 JSON 数组: {path}")
        source_key = _source_fingerprint(path, length=16)
        records.append(build_research_record(payload, fallback_id=f"research:{source_key}"))

    table = build_metric_table(records, METRIC_COLUMNS)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8", newline="") as stream:
        write_metric_table_csv(table, stream)
    return {
        "row_count": len(table.rows),
        "issue_count": len(table.issues),
        "metric_count": len(METRIC_COLUMNS),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--chat-log", action="append", type=Path, default=[])
    parser.add_argument("--research-log", action="append", type=Path, default=[])
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if not args.chat_log and not args.research_log:
        parser.error("至少需要一个 --chat-log 或 --research-log")
    summary = summarize_logs(
        chat_paths=tuple(args.chat_log),
        research_paths=tuple(args.research_log),
        output_path=args.output,
    )
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))


def _load_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as stream:
        return json.load(stream)


def _source_fingerprint(path: Path, *, length: int) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()[:length]


def _chat_trace_id(trace: object) -> str | None:
    if not isinstance(trace, Mapping):
        return None
    canvas = trace.get("canvas")
    if not isinstance(canvas, Mapping):
        return None
    meta = canvas.get("meta")
    if not isinstance(meta, Mapping):
        return None
    return _text(meta.get("traceId"))


def _chat_identity(trace: object) -> tuple[str | None, str | None]:
    """读取聊天 Trace 的用户与会话标识；消息级 ID 在当前日志中不可观测。"""

    if not isinstance(trace, Mapping):
        return None, None
    user_id = _text(trace.get("userId"))
    canvas = trace.get("canvas")
    if not isinstance(canvas, Mapping):
        return user_id, None
    meta = canvas.get("meta")
    if not isinstance(meta, Mapping):
        return user_id, None
    return user_id, _text(meta.get("sessionId"))


def build_research_record(events: list[Any], fallback_id: str) -> TraceMetricRecord:
    """把研究模式事件列表包装为待评测记录;供批量文件入口与流式入口复用。

    log_id 优先取日志内的用户级 userMessageID(兼容 body.messageID),
    提取不到时回退为调用方提供的稳定 fallback_id,保证行可追溯。
    """
    user_id, session_id, message_id = _research_identity(events)
    return TraceMetricRecord(
        message_id or fallback_id,
        "research",
        events,
        user_id=user_id,
        session_id=session_id,
    )


def _research_identity(
    events: Sequence[object],
) -> tuple[str | None, str | None, str | None]:
    """提取研究请求身份；用户消息 ID 标识整条研究日志。"""

    user_id = _first_nested_text(events, "headers", "userid")
    session_id = _first_top_level_text(events, "sessionID") or _first_nested_text(
        events, "body", "sessionId"
    )
    message_id = _first_top_level_text(events, "userMessageID") or _first_nested_text(
        events, "body", "messageID"
    )
    return user_id, session_id, message_id


def _first_top_level_text(events: Sequence[object], key: str) -> str | None:
    for event in events:
        if isinstance(event, Mapping) and (value := _text(event.get(key))) is not None:
            return value
    return None


def _first_nested_text(events: Sequence[object], container_key: str, value_key: str) -> str | None:
    for event in events:
        if not isinstance(event, Mapping):
            continue
        container = event.get(container_key)
        if (
            isinstance(container, Mapping)
            and (value := _text(container.get(value_key))) is not None
        ):
            return value
    return None


def _text(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _is_sequence(value: object) -> TypeGuard[Sequence[object]]:
    return isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray))


if __name__ == "__main__":
    main()
