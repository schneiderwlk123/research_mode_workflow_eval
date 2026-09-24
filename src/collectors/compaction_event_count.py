"""规范化聊天与研究模式的上下文压缩事件证据(稳定性维度 7.6)。

聊天 canvas 无压缩事件通道,通道不存在不等于无压缩,固定返回不可计算
(禁止记 0,也不复活已取消的 usageTokens 增长代理口径);结构非法才是 ERROR。

研究模式消费入口层已合并的单逻辑回合事件序列(与 7.4 同一信任契约:不重复
校验回合边界,边界完整性由 7.1 负责),统计 `message.part.updated` 中
`part.type == "compaction"` 且 `auto == true` 的唯一压缩 part 数:

- 去重键为 `part.id`(同一 part 的重复投影只计一次,进重复诊断)。
- `auto == false` 是手动/显式压缩: 不进主值,单独诊断(权威口径 7.6 要求
  手动压缩不混入主值)。
- `auto` 缺失或非布尔无法证明是自动压缩: 不猜测,不进主值也不进手动计数,
  单独诊断。
- `part.id` 缺失无法去重: 保守计为独立事件(宁多计不漏计,与 7.3 ts 缺失
  同方向),进缺失 id 诊断。
- 同一 `part.id` 出现不同 auto 分类属证据冲突,整条按
  conflicting_compaction_definition 隔离。
- `message.updated` 的 `info.mode == "compaction"` 摘要是跨通道投影
  (实勘: 压缩 part 之后跟随摘要消息),只进诊断不计数,防止未来误把消息
  通道并入主值。

结果只保留计数与标识,不保留事件正文、消息 ID 或时间戳原值。
"""

from collections.abc import Mapping, Sequence
from typing import Literal, TypedDict, TypeGuard


class CompactionEventDiagnostics(TypedDict):
    """压缩事件判定的安全诊断计数;聊天模式各项恒为 0。"""

    duplicate_compaction_projection_count: int
    manual_compaction_count: int
    auto_flag_invalid_count: int
    missing_compaction_part_id_count: int
    message_compaction_projection_count: int


class NormalizedCompactionEventTrace(TypedDict):
    """供上下文压缩事件数评测器消费的最小规范化 Trace。"""

    trace_id: str | None
    mode: Literal["chat", "research"]
    compaction_event_count: int | None
    diagnostics: CompactionEventDiagnostics
    normalization_error: str | None


def normalize_chat_compaction_events(trace: Mapping[str, object]) -> NormalizedCompactionEventTrace:
    """聊天模式固定不可计算: canvas 无压缩事件,上下文管理过程不可见。

    canvas 结构非法才是 ERROR;结构合法即返回 no_compaction_events——通道不存在
    不等于无压缩,禁止记 0(与 7.4 聊天侧同一契约边界)。
    """

    canvas = trace.get("canvas") if isinstance(trace, Mapping) else None
    if not isinstance(canvas, Mapping) or not isinstance(canvas.get("meta"), Mapping):
        return _empty("chat", None, "invalid_chat_trace_structure")
    trace_id = _text(canvas["meta"].get("traceId"))
    return _empty("chat", trace_id, "no_compaction_events")


def normalize_research_compaction_events(
    events: Sequence[object],
) -> NormalizedCompactionEventTrace:
    """统计单个合并逻辑回合内 auto == true 的唯一压缩 part 数。

    零压缩是合法观测值(count=0),不是不可计算;信任入口层单回合契约,
    trace_id 尽力从 turn.start 提取,缺失时回退到事件级 sessionID。
    """

    if not _is_sequence(events):
        return _empty("research", None, "invalid_research_trace_structure")

    diagnostics = _zero_diagnostics()
    # part.id -> auto 分类;同 id 不同分类即证据冲突。
    seen_classifications: dict[str, str] = {}
    unique_auto_count = 0
    full_trace_id: str | None = None
    fallback_trace_id: str | None = None

    for event in events:
        if not isinstance(event, Mapping):
            continue
        session_id = _text(event.get("sessionID"))
        event_type = event.get("type")
        if event_type == "turn.start" and full_trace_id is None:
            full_trace_id = _trace_id(session_id, _text(event.get("userMessageID")))
        elif event_type == "message.updated":
            info = event.get("info")
            if isinstance(info, Mapping) and info.get("mode") == "compaction":
                diagnostics["message_compaction_projection_count"] += 1
        if event_type != "message.part.updated":
            continue
        part = event.get("part")
        if not isinstance(part, Mapping) or part.get("type") != "compaction":
            continue
        if fallback_trace_id is None and session_id is not None:
            fallback_trace_id = session_id

        classification = _auto_classification(part.get("auto"))
        part_id = _text(part.get("id"))
        if part_id is None:
            # 无 id 无法去重: 保守计为独立事件,分类照常分流。
            diagnostics["missing_compaction_part_id_count"] += 1
            unique_auto_count += _tally(classification, diagnostics)
            continue
        previous = seen_classifications.get(part_id)
        if previous is None:
            seen_classifications[part_id] = classification
            unique_auto_count += _tally(classification, diagnostics)
        elif previous != classification:
            return _empty(
                "research",
                full_trace_id or fallback_trace_id,
                "conflicting_compaction_definition",
                diagnostics,
            )
        else:
            diagnostics["duplicate_compaction_projection_count"] += 1

    return _result(
        "research",
        full_trace_id or fallback_trace_id,
        unique_auto_count,
        diagnostics,
        None,
    )


def _auto_classification(auto: object) -> str:
    """把 auto 观测分类;布尔之外一律 invalid,不猜测真值。"""

    if auto is True:
        return "auto_true"
    if auto is False:
        return "manual"
    return "invalid"


def _tally(classification: str, diagnostics: CompactionEventDiagnostics) -> int:
    """按分类累加诊断并返回进入主值的增量(仅 auto_true 计 1)。"""

    if classification == "manual":
        diagnostics["manual_compaction_count"] += 1
        return 0
    if classification == "invalid":
        diagnostics["auto_flag_invalid_count"] += 1
        return 0
    return 1


def _zero_diagnostics() -> CompactionEventDiagnostics:
    return {
        "duplicate_compaction_projection_count": 0,
        "manual_compaction_count": 0,
        "auto_flag_invalid_count": 0,
        "missing_compaction_part_id_count": 0,
        "message_compaction_projection_count": 0,
    }


def _empty(
    mode: Literal["chat", "research"],
    trace_id: str | None,
    error: str,
    diagnostics: CompactionEventDiagnostics | None = None,
) -> NormalizedCompactionEventTrace:
    return _result(mode, trace_id, None, diagnostics or _zero_diagnostics(), error)


def _result(
    mode: Literal["chat", "research"],
    trace_id: str | None,
    compaction_event_count: int | None,
    diagnostics: CompactionEventDiagnostics,
    error: str | None,
) -> NormalizedCompactionEventTrace:
    return {
        "trace_id": trace_id,
        "mode": mode,
        "compaction_event_count": compaction_event_count,
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
