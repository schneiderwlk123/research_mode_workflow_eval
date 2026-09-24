"""规范化聊天与研究模式的续跑重启证据(稳定性维度 7.3)。

聊天 canvas 无续跑/重启机制,单条 Trace 即完整回合,恒返回固定不可计算原因,
不做结构校验也不估算。研究模式消费入口层已合并的单逻辑回合事件序列(与
回合完成率/数值锚定率同一输入契约),以去重后的 turn.start 事件数减一计量
框架级断点续跑重启次数,并复用回合完成率(7.1)的研究口径附带 turn_completed
闭环标记——两因素一起回答"重启了几次、最终救回来没有"(2026-09-21 需求方
确认双因素输出)。

去重键口径(2026-09-21 需求方确认): 同一合并回合内 turn.start 全部共享
sessionID + userMessageID,不能按 ID 去重;按解析后的毫秒时间戳区分真重启
与同一事件的重复投影。ts 缺失/非法的 turn.start 无法证明是重复投影,保守
计为独立事件(宁可多计重启,不静默吞掉)并进入诊断计数。

结果只保留计数、闭环标记与标识,不保留 query、事件正文或时间戳原值。
"""

import math
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Literal, TypedDict, TypeGuard

from collectors.turn_completion import normalize_research_turn_completion


class ContinuationRestartDiagnostics(TypedDict):
    """续跑重启判定的安全诊断计数;聊天模式各项恒为 0。"""

    turn_start_count: int
    duplicate_turn_start_count: int
    invalid_ts_turn_start_count: int


class NormalizedContinuationRestartTrace(TypedDict):
    """供续跑重启次数评测器消费的最小规范化 Trace。

    turn_completed 仅在研究模式计算成功时携带(0/1,复用 7.1 turn.end 事件
    口径);聊天模式与全部错误/不可计算路径恒为 None。
    """

    trace_id: str | None
    mode: Literal["chat", "research"]
    restart_count: int | None
    turn_completed: int | None
    diagnostics: ContinuationRestartDiagnostics
    normalization_error: str | None


def normalize_chat_continuation_restart(trace: object) -> NormalizedContinuationRestartTrace:
    """聊天模式恒不可计算: 无续跑机制是模式语义事实,与结构合法性无关。

    trace_id 尽力从 canvas.meta.traceId 提取用于结果关联;提取失败不升级为
    结构错误,原因码保持 no_continuation_mechanism。聊天闭环代理口径(7.1)
    不进入本指标,避免两种 completion 语义在同一单元格混比。
    """

    trace_id: str | None = None
    if isinstance(trace, Mapping):
        canvas = trace.get("canvas")
        if isinstance(canvas, Mapping) and isinstance(canvas.get("meta"), Mapping):
            trace_id = _text(canvas["meta"].get("traceId"))
    return _result("chat", trace_id, None, None, _zero_diagnostics(), "no_continuation_mechanism")


def normalize_research_continuation_restart(
    events: Sequence[object],
) -> NormalizedContinuationRestartTrace:
    """计量单个合并逻辑回合的续跑重启次数并附带 7.1 闭环标记。

    输入契约与回合完成率一致: 入口层先按 sessionID 分组、同 userMessageID
    合并 `_noquery` 续跑段,因此序列内出现不同 ID 的 turn.start 属于切分违约,
    按 conflicting_turn_boundary 隔离;无任何 turn.start 时回合边界不可证明,
    返回 missing_turn_boundary,不得记 0。

    闭环标记复用 normalize_research_turn_completion 在同一序列上的判定结果;
    其边界错误(turn.end 耗时冲突等)连带隔离整条——两因素共享回合边界证据,
    闭环判定不可靠时重启计数也不单独输出。
    """

    if not _is_sequence(events):
        return _empty("research", None, "invalid_research_trace_structure")

    diagnostics = _zero_diagnostics()
    turn_key: tuple[str | None, str | None] | None = None
    seen_start_keys: set[tuple[str | None, str | None, int]] = set()
    unique_start_count = 0

    for event in events:
        if not isinstance(event, Mapping) or event.get("type") != "turn.start":
            continue
        session_id = _text(event.get("sessionID"))
        message_id = _text(event.get("userMessageID"))
        if turn_key is None:
            turn_key = (session_id, message_id)
        elif turn_key != (session_id, message_id):
            return _empty(
                "research",
                _trace_id(*turn_key),
                "conflicting_turn_boundary",
                diagnostics,
            )
        ts_state, ts_ms = _timestamp_ms(event.get("ts"))
        if ts_state != "valid" or ts_ms is None:
            # 无有效时间戳: 无法证明是重复投影,保守计为独立重启段。
            diagnostics["invalid_ts_turn_start_count"] += 1
            unique_start_count += 1
            continue
        start_key = (session_id, message_id, ts_ms)
        if start_key in seen_start_keys:
            diagnostics["duplicate_turn_start_count"] += 1
            continue
        seen_start_keys.add(start_key)
        unique_start_count += 1

    diagnostics["turn_start_count"] = unique_start_count
    if turn_key is None:
        return _empty("research", None, "missing_turn_boundary", diagnostics)

    completion = normalize_research_turn_completion(events)
    completion_error = completion.get("normalization_error")
    if completion_error is not None or completion.get("turn_completed") is None:
        # 7.1 已保证无 error 时 turn_completed ∈ {0,1};防御性合并两个条件。
        return _empty(
            "research",
            _trace_id(*turn_key),
            completion_error or "missing_turn_boundary",
            diagnostics,
        )
    return _result(
        "research",
        _trace_id(*turn_key),
        unique_start_count - 1,
        completion["turn_completed"],
        diagnostics,
        None,
    )


def _timestamp_ms(value: object) -> tuple[str, int | None]:
    """把 ts 观测解析为毫秒时间戳;口径与 end_to_end_latency 保持一致。

    去重只依赖解析结果的相等性,重复投影的相同原始值必然解析为相同毫秒数;
    数值型按量级启发式区分秒/毫秒(与既有 collector 相同阈值)。
    """

    if value is None or (isinstance(value, str) and not value.strip()):
        return "missing", None
    if isinstance(value, bool):
        return "invalid", None
    if isinstance(value, (int, float)):
        try:
            numeric = float(value)
        except OverflowError:
            return "invalid", None
        if not math.isfinite(numeric):
            return "invalid", None
        seconds = numeric / 1000 if abs(numeric) >= 100_000_000_000 else numeric
        try:
            parsed = datetime.fromtimestamp(seconds, tz=UTC)
        except (OverflowError, OSError, ValueError):
            return "invalid", None
        return "valid", round(parsed.timestamp() * 1000)
    if not isinstance(value, str):
        return "invalid", None
    try:
        parsed_iso = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return "invalid", None
    # 上游无时区字符串沿用既有 collector 约定按 UTC 解释,避免依赖运行机器时区。
    if parsed_iso.tzinfo is None:
        parsed_iso = parsed_iso.replace(tzinfo=UTC)
    return "valid", round(parsed_iso.timestamp() * 1000)


def _zero_diagnostics() -> ContinuationRestartDiagnostics:
    return {
        "turn_start_count": 0,
        "duplicate_turn_start_count": 0,
        "invalid_ts_turn_start_count": 0,
    }


def _empty(
    mode: Literal["chat", "research"],
    trace_id: str | None,
    error: str,
    diagnostics: ContinuationRestartDiagnostics | None = None,
) -> NormalizedContinuationRestartTrace:
    return _result(mode, trace_id, None, None, diagnostics or _zero_diagnostics(), error)


def _result(
    mode: Literal["chat", "research"],
    trace_id: str | None,
    restart_count: int | None,
    turn_completed: int | None,
    diagnostics: ContinuationRestartDiagnostics,
    error: str | None,
) -> NormalizedContinuationRestartTrace:
    return {
        "trace_id": trace_id,
        "mode": mode,
        "restart_count": restart_count,
        "turn_completed": turn_completed,
        "diagnostics": diagnostics,
        "normalization_error": error,
    }


def _trace_id(session_id: str | None, message_id: str | None) -> str | None:
    if session_id and message_id:
        return f"{session_id}:{message_id}"
    return session_id or message_id


def _text(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _is_sequence(value: object) -> TypeGuard[Sequence[object]]:
    return isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray))
