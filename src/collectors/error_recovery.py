"""规范化错误自恢复率证据(稳定性维度 7.5),组合四个既有口径的单一事实来源。

业务规则(《初步入库评测指标.md》7.5):
- 本指标是 5.1 `error_recovery_completion_rate` 的研究侧降级实现,判定完全基于
  现有字段,`recovery_semantics = degraded_v1` 随结果声明;埋点 C/D 落地后对齐
  升版,不得静默沿用降级口径。
- `had_error = 1` 当且仅当 `llm.error` 事件数 > 0(7.4 口径)或存在
  `status='error'` 的工具调用(7.7 口径,callID 合并后)。
- 恢复证据 = 闭环(7.1 的 `turn_completed`)+ 非空最终正文(6.1 的正文通道
  write 产物 > edit 片段 > 最后 assistant 文本);主值 error_recovered 由评测器
  合成,本 collector 只输出规范化证据。
- 聊天模式无 llm.error、无工具终态 status(埋点 C 缺口),had_error 不可判定,
  固定返回 `no_error_events_observable`;禁止以业务失败载荷代理(载荷失败
  不等价调用层失败,7.7 的载荷代理口径在此不适用)。
- loop 级重试配对(唯一新增逻辑):事件时间序中 `llm.error(loop=N)` 之后出现
  `llm.response(loop=N)` 且 `result.finishReason` 为非空文本且 != "error"
  → 该条 error 计入 `retry_recovered_error_count`;按原始事件条数计(同 loop
  多条 error 各自独立配对,与 7.4 主值口径一致)。error 缺 loop(实勘 2/9)或
  响应侧 finishReason 缺失均不可配对,只进诊断不估算。
- 正文与错误文本只在组件内部内存流转,本结果仅保留 0/1 标记、通道标签与
  安全计数。
"""

from collections.abc import Mapping, Sequence
from typing import Literal, TypedDict, TypeGuard

from collectors.answer_evidence_grounding import normalize_research_answer_grounding
from collectors.llm_error_count import normalize_research_llm_errors
from collectors.tool_error_rate import normalize_research_tool_error_rate
from collectors.turn_completion import normalize_research_turn_completion

RECOVERY_SEMANTICS = "degraded_v1"

# 6.1 正文通道的三个有效交付通道;"missing" 表示 turn.end 前无非空最终正文。
_BODY_PRESENT_CHANNELS = frozenset({"write_artifact", "edit_artifact", "final_text"})

# 组件结构错误:透传为 ERROR 类原因码,禁止静默降级为无错误。
_STRUCTURAL_ERROR_REASONS = frozenset(
    {
        "invalid_research_trace_structure",
        "conflicting_turn_boundary",
        "missing_call_id",
        "missing_tool_name",
        "conflicting_call_definition",
    }
)


class ErrorRecoveryDiagnostics(TypedDict):
    """不含正文或错误文本的安全计数。"""

    llm_error_event_count: int
    tool_error_call_count: int
    retry_recovered_error_count: int
    retry_unpaired_error_count: int
    llm_loop_missing_error_count: int


class NormalizedErrorRecoveryTrace(TypedDict):
    """供错误自恢复率评测器消费的最小规范化 Trace。"""

    trace_id: str | None
    mode: Literal["chat", "research"]
    recovery_semantics: str | None
    had_error: int | None
    turn_completed: int | None
    body_present: int | None
    body_channel: str | None
    diagnostics: ErrorRecoveryDiagnostics
    normalization_error: str | None


def normalize_chat_error_recovery(
    trace: Mapping[str, object],
) -> NormalizedErrorRecoveryTrace:
    """聊天模式固定不可计算:无模型层错误通道、无工具终态 status。

    只做最小结构校验(canvas/meta 缺失或非法 → ERROR),不解析业务内容;
    通道不存在不等于无错误,禁止记 0 或 1。
    """

    canvas = trace.get("canvas")
    meta = canvas.get("meta") if isinstance(canvas, Mapping) else None
    if not isinstance(canvas, Mapping) or not isinstance(meta, Mapping):
        return _empty("chat", None, "invalid_chat_trace_structure")
    trace_id = _text(meta.get("traceId"))
    return _empty("chat", trace_id, "no_error_events_observable")


def normalize_research_error_recovery(
    events: Sequence[object],
) -> NormalizedErrorRecoveryTrace:
    """组合四组件规范化研究回合的恢复证据;输入契约为单逻辑回合事件序列。

    回合切分与 `_noquery` 续跑段合并是入口层职责(与 7.1/7.4 一致)。错误传播
    优先级:输入非法 > 组件结构错误 > 缺回合边界 > 无错误事件 > 可计算。
    """

    if not _is_sequence(events):
        return _empty("research", None, "invalid_research_trace_structure")

    completion = normalize_research_turn_completion(events)
    trace_id = completion["trace_id"]
    completion_error = completion["normalization_error"]
    if completion_error in _STRUCTURAL_ERROR_REASONS:
        return _empty("research", trace_id, completion_error)
    if completion_error is not None:
        # missing_turn_boundary:闭环不可证明,恢复判定失去锚点。
        return _empty("research", trace_id, completion_error)

    llm_errors = normalize_research_llm_errors(events)
    if llm_errors["normalization_error"] is not None:
        return _empty("research", trace_id, llm_errors["normalization_error"])
    llm_error_count = llm_errors["llm_error_count"] or 0

    tool_errors = normalize_research_tool_error_rate(events)
    tool_error_reason = tool_errors["normalization_error"]
    if tool_error_reason is not None and tool_error_reason in _STRUCTURAL_ERROR_REASONS:
        return _empty("research", trace_id, tool_error_reason)
    # no_tool_calls / no_terminal_tool_calls 只表示无工具层错误证据,计数为 0。
    tool_error_call_count = sum(
        1 for call in tool_errors["tool_calls"] if call["terminal_state"] == "error"
    )

    grounding = normalize_research_answer_grounding(events)
    grounding_error = grounding["normalization_error"]
    if grounding_error is not None and grounding_error not in {
        "missing_final_answer",
        "missing_turn_boundary",
    }:
        return _empty("research", trace_id, grounding_error)
    body_channel = grounding["body_channel"]
    body_present = 1 if body_channel in _BODY_PRESENT_CHANNELS else 0

    retry_recovered, retry_unpaired, loop_missing = _retry_pair_counts(events)

    diagnostics: ErrorRecoveryDiagnostics = {
        "llm_error_event_count": llm_error_count,
        "tool_error_call_count": tool_error_call_count,
        "retry_recovered_error_count": retry_recovered,
        "retry_unpaired_error_count": retry_unpaired,
        "llm_loop_missing_error_count": loop_missing,
    }
    had_error = 1 if (llm_error_count > 0 or tool_error_call_count > 0) else 0
    turn_completed = completion["turn_completed"]
    error: str | None = None if had_error else "no_error_events"
    return {
        "trace_id": trace_id,
        "mode": "research",
        "recovery_semantics": RECOVERY_SEMANTICS,
        "had_error": had_error,
        "turn_completed": turn_completed,
        "body_present": body_present,
        "body_channel": body_channel,
        "diagnostics": diagnostics,
        "normalization_error": error,
    }


def _retry_pair_counts(events: Sequence[object]) -> tuple[int, int, int]:
    """loop 级重试配对:返回 (配对成功条数, 未配对条数, error 缺 loop 条数)。

    配对证据 = 同序列中严格靠后的 `llm.response`,其顶层 `loop` 与 error 相同
    且 `result.finishReason` 为非空文本且不等于 "error"(实证值域 stop /
    tool-calls / other / error)。响应缺 loop 或 finishReason 不可证明成功,
    不参与配对;逐条原始 llm.error 事件独立计数(含重复投影,与 7.4 一致)。
    """

    success_positions: dict[int, list[int]] = {}
    for position, event in enumerate(events):
        if not isinstance(event, Mapping) or event.get("type") != "llm.response":
            continue
        loop = event.get("loop")
        if not _is_loop(loop):
            continue
        result = event.get("result")
        if not isinstance(result, Mapping):
            continue
        finish = _text(result.get("finishReason"))
        if finish is None or finish.lower() == "error":
            continue
        success_positions.setdefault(loop, []).append(position)

    recovered = 0
    unpaired = 0
    loop_missing = 0
    for position, event in enumerate(events):
        if not isinstance(event, Mapping) or event.get("type") != "llm.error":
            continue
        loop = event.get("loop")
        if not _is_loop(loop):
            loop_missing += 1
            continue
        if any(pos > position for pos in success_positions.get(loop, ())):
            recovered += 1
        else:
            unpaired += 1
    return recovered, unpaired, loop_missing


def _empty(
    mode: Literal["chat", "research"],
    trace_id: str | None,
    error: str,
) -> NormalizedErrorRecoveryTrace:
    return {
        "trace_id": trace_id,
        "mode": mode,
        "recovery_semantics": RECOVERY_SEMANTICS if mode == "research" else None,
        "had_error": None,
        "turn_completed": None,
        "body_present": None,
        "body_channel": None,
        "diagnostics": {
            "llm_error_event_count": 0,
            "tool_error_call_count": 0,
            "retry_recovered_error_count": 0,
            "retry_unpaired_error_count": 0,
            "llm_loop_missing_error_count": 0,
        },
        "normalization_error": error,
    }


def _is_loop(value: object) -> TypeGuard[int]:
    return isinstance(value, int) and not isinstance(value, bool)


def _text(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _is_sequence(value: object) -> TypeGuard[Sequence[object]]:
    return isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray))
