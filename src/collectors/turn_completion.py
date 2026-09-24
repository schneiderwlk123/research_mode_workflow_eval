"""规范化聊天与研究模式的回合闭环证据(稳定性维度 7.1)。

聊天模式无回合边界事件,采用显式声明的代理口径: 最终回答非空且不命中版本化
固定服务失败模板即视为闭环;研究模式以合并逻辑回合内的 turn.end 事件为闭环
锚点。本模块只输出二值标记与安全诊断计数,不保留 query、回答正文或模板原文,
避免敏感内容进入持久化结果。
"""

from collections.abc import Mapping, Sequence
from typing import Literal, TypedDict, TypeGuard

# 固定服务失败模板目录 v1: 全文精确匹配(剥首尾空白)。新增模板必须升版并记录出处。
FAILURE_TEMPLATE_CATALOG_VERSION = "failure_template_catalog_v1"
_FAILURE_TEMPLATES_V1: tuple[str, ...] = ("模型服务暂时不可用，请稍后重试",)


class TurnCompletionDiagnostics(TypedDict):
    """闭环判定的安全诊断计数;聊天项与研究项互不混用,未涉及的模式恒为 0/None。"""

    failure_template_hit_count: int
    missing_or_empty_answer_count: int
    duplicate_turn_end_count: int
    mismatched_turn_end_count: int
    duration_diagnostic: str | None


class NormalizedTurnCompletionTrace(TypedDict):
    """供回合完成率评测器消费的最小规范化 Trace。"""

    trace_id: str | None
    mode: Literal["chat", "research"]
    turn_completed: int | None
    diagnostics: TurnCompletionDiagnostics
    normalization_error: str | None


def normalize_chat_turn_completion(trace: Mapping[str, object]) -> NormalizedTurnCompletionTrace:
    """按代理口径判定聊天 Trace 是否闭环: 回答非空且不命中固定失败模板。

    回答缺失、空白或非字符串记 0(合法失败值,不是不可计算);canvas 结构非法
    才是 ERROR。代理口径无法识别静默截断,语义差由评测器随值声明。
    """

    canvas = trace.get("canvas") if isinstance(trace, Mapping) else None
    if not isinstance(canvas, Mapping) or not isinstance(canvas.get("meta"), Mapping):
        return _empty("chat", None, "invalid_chat_trace_structure")
    meta = canvas["meta"]
    trace_id = _text(meta.get("traceId"))

    answer = canvas.get("answer")
    answer_text: str | None = None
    if isinstance(answer, Mapping):
        raw = answer.get("answer")
        if isinstance(raw, str) and raw.strip():
            answer_text = raw.strip()

    diagnostics = _zero_diagnostics()
    if answer_text is None:
        diagnostics["missing_or_empty_answer_count"] = 1
        return _result("chat", trace_id, 0, diagnostics, None)
    if answer_text in _FAILURE_TEMPLATES_V1:
        diagnostics["failure_template_hit_count"] = 1
        return _result("chat", trace_id, 0, diagnostics, None)
    return _result("chat", trace_id, 1, diagnostics, None)


def normalize_research_turn_completion(events: Sequence[object]) -> NormalizedTurnCompletionTrace:
    """按 turn.end 判定单个合并逻辑回合是否闭环。

    输入契约与数值锚定率一致: 入口层先按 sessionID 分组、按 turn.start 切分并
    合并 `_noquery` 续跑段,因此同一序列允许多个同 ID 的 turn.start(续跑重启),
    但出现不同 ID 的 turn.start 说明切分违约,按 conflicting_turn_boundary 隔离。
    闭环判定不依赖 durationMs 取值,耗时缺失/非法只进诊断。
    """

    if not _is_sequence(events):
        return _empty("research", None, "invalid_research_trace_structure")

    diagnostics = _zero_diagnostics()
    turn_key: tuple[str | None, str | None] | None = None
    saw_turn_start = False
    closed = False
    seen_end_keys: set[tuple[str | None, str | None, object]] = set()

    for event in events:
        if not isinstance(event, Mapping):
            continue
        event_type = event.get("type")
        session_id = _text(event.get("sessionID"))
        message_id = _text(event.get("userMessageID"))
        if event_type == "turn.start":
            saw_turn_start = True
            if turn_key is None:
                turn_key = (session_id, message_id)
            elif turn_key != (session_id, message_id):
                return _empty(
                    "research",
                    _trace_id(*turn_key),
                    "conflicting_turn_boundary",
                )
        elif event_type == "turn.end":
            if turn_key is not None and (session_id, message_id) != turn_key:
                # ID 不匹配的 turn.end 不证明本回合闭环;只诊断,不静默丢弃。
                diagnostics["mismatched_turn_end_count"] += 1
                continue
            duration = event.get("durationMs")
            end_key = (session_id, message_id, _duration_key(duration))
            if end_key in seen_end_keys:
                diagnostics["duplicate_turn_end_count"] += 1
                continue
            if any(k[:2] == end_key[:2] for k in seen_end_keys):
                # 同回合出现耗时不同的多个 turn.end,边界证据冲突,隔离整条。
                return _empty(
                    "research",
                    _trace_id(session_id, message_id),
                    "conflicting_turn_boundary",
                    diagnostics,
                )
            seen_end_keys.add(end_key)
            closed = True
            diagnostic = _duration_diagnostic(duration)
            if diagnostic is not None and diagnostics["duration_diagnostic"] is None:
                diagnostics["duration_diagnostic"] = diagnostic
            if turn_key is None:
                turn_key = (session_id, message_id)

    if not saw_turn_start or turn_key is None:
        # 无 turn.start: 回合边界不可证明(含仅有孤立 turn.end 的情形)。
        # saw_turn_start 为真时 turn_key 必已赋值,合并条件只为类型收窄。
        trace_id = _trace_id(*turn_key) if turn_key is not None else None
        return _empty("research", trace_id, "missing_turn_boundary", diagnostics)
    return _result(
        "research",
        _trace_id(*turn_key),
        1 if closed else 0,
        diagnostics,
        None,
    )


def _duration_key(duration: object) -> object:
    """把耗时观测规范化为可比较键: 相同值去重,不同值触发冲突隔离。"""

    if duration is None:
        return ("invalid", "None")
    if isinstance(duration, bool) or not isinstance(duration, (int, float, str)):
        return ("invalid", repr(duration))
    return ("value", duration)


def _duration_diagnostic(duration: object) -> str | None:
    if duration is None or (isinstance(duration, str) and not duration.strip()):
        return "missing_duration_ms"
    if isinstance(duration, bool) or not isinstance(duration, (int, float)):
        return "invalid_duration_ms"
    if duration < 0:
        return "invalid_duration_ms"
    return None


def _zero_diagnostics() -> TurnCompletionDiagnostics:
    return {
        "failure_template_hit_count": 0,
        "missing_or_empty_answer_count": 0,
        "duplicate_turn_end_count": 0,
        "mismatched_turn_end_count": 0,
        "duration_diagnostic": None,
    }


def _empty(
    mode: Literal["chat", "research"],
    trace_id: str | None,
    error: str,
    diagnostics: TurnCompletionDiagnostics | None = None,
) -> NormalizedTurnCompletionTrace:
    return _result(mode, trace_id, None, diagnostics or _zero_diagnostics(), error)


def _result(
    mode: Literal["chat", "research"],
    trace_id: str | None,
    turn_completed: int | None,
    diagnostics: TurnCompletionDiagnostics,
    error: str | None,
) -> NormalizedTurnCompletionTrace:
    return {
        "trace_id": trace_id,
        "mode": mode,
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
