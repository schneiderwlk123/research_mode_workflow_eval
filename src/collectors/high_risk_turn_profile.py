"""规范化高危回合画像证据(稳定性维度 7.11),组合既有口径的单一事实来源。

业务规则(《初步入库评测指标.md》7.11,2026-09-21 需求方确认双条件阈值):

    high_risk_turn = (step 数 > 30) ∧ (api_retry_rate > 0.4)

- 报告级组合规则,非单值指标,不进宽表单值列;阈值判定由评测器执行,本
  collector 只输出规范化特征证据。
- 四组件组合(7.5 范式):7.8 `normalize_*_api_retry` 提供重试率(硬依赖,
  不可计算即透传——no_tool_debug_events 不代表无重试,step 数再高也不可
  判定);7.6 压缩计数与 7.1 闭环标记降级为特征清单诊断项;step 数为
  step-start 按 messageID 去重的确定性计数(与 7.10 同口径,未来 7.10
  立项后复用本实现)。
- 空结果数(7.9)特征项本批省略——该指标未立项,2026-09-21 需求方确认;
  7.9 实现后复用其 normalizer 补入,禁止提前内联复制规则。
- 闭环是特征不是条件:缺回合边界(missing_turn_boundary)时 turn_completed
  为 None,画像照常可计算;边界证据冲突(conflicting_turn_boundary)与压缩
  证据冲突(conflicting_compaction_definition)属结构损坏,透传隔离。
- 聊天模式依赖 7.8 聊天侧不可计算,透传同一原因;聊天侧替代画像需另行
  立项,禁止以 round/调用数特征静默替代。

结果只保留计数、比率与标识,不保留事件正文、错误文本或工具输出。
"""

from collections.abc import Mapping, Sequence
from typing import Literal, TypedDict, TypeGuard

from collectors.api_retry import normalize_chat_api_retry, normalize_research_api_retry
from collectors.compaction_event_count import normalize_research_compaction_events
from collectors.turn_completion import normalize_research_turn_completion

# 规则版本: v2 = 2026-09-21 确认的双条件口径(v1 四条件公式实测 0 命中已废弃)。
HIGH_RISK_PROFILE_VERSION = "high_risk_profile_v2"

# 闭环特征的边界缺失不是结构损坏,不阻断画像主值。
_NON_BLOCKING_COMPLETION_ERRORS = frozenset({"missing_turn_boundary"})


class HighRiskProfileDiagnostics(TypedDict):
    """step 计数的安全诊断;聊天模式各项恒为 0。"""

    duplicate_step_start_projection_count: int
    missing_step_message_id_count: int


class NormalizedHighRiskTurnProfileTrace(TypedDict):
    """供高危回合画像评测器消费的最小规范化 Trace。"""

    trace_id: str | None
    mode: Literal["chat", "research"]
    profile_version: str | None
    step_count: int | None
    api_retry_rate: float | None
    api_retry_count: int | None
    api_call_count: int | None
    compaction_event_count: int | None
    turn_completed: int | None
    diagnostics: HighRiskProfileDiagnostics
    normalization_error: str | None


def normalize_chat_high_risk_turn_profile(
    trace: Mapping[str, object],
) -> NormalizedHighRiskTurnProfileTrace:
    """聊天模式固定不可计算: 依赖的 7.8 聊天侧无 API 明细通道。

    原因码透传 7.8 聊天契约(no_tool_debug_events / invalid_chat_trace_structure),
    不新造原因码,保证画像与依赖指标的不可计算语义一致。
    """

    retry = normalize_chat_api_retry(trace)
    error = retry["normalization_error"]
    assert error is not None  # noqa: S101 - 7.8 聊天契约恒携带原因码
    return _empty("chat", retry["trace_id"], error)


def normalize_research_high_risk_turn_profile(
    events: Sequence[object],
) -> NormalizedHighRiskTurnProfileTrace:
    """组合四组件规范化研究回合的画像特征;输入契约为单逻辑回合事件序列。

    回合切分与续跑段合并是入口层职责(与 7.1/7.4/7.6 一致)。错误传播
    优先级: 输入非法 > 7.8 依赖不可计算/结构错误 > 闭环边界冲突 >
    压缩证据冲突 > 可计算。
    """

    if not _is_sequence(events):
        return _empty("research", None, "invalid_research_trace_structure")

    retry = normalize_research_api_retry(events)
    retry_error = retry["normalization_error"]
    if retry_error is not None:
        # 依赖不可计算(no_tool_debug_events / no_observable_api_calls)或
        # 结构错误(invalid_research_trace_structure)均透传,不以 step 数替代。
        return _empty("research", retry["trace_id"], retry_error)
    api_call_count = retry["api_call_count"]
    api_retry_count = retry["api_retry_count"]
    if api_call_count is None or api_retry_count is None:
        # 7.8 计算路径契约保证非 None;防御性隔离,不带病合成。
        return _empty("research", retry["trace_id"], "invalid_research_trace_structure")

    completion = normalize_research_turn_completion(events)
    completion_error = completion["normalization_error"]
    if completion_error is not None and completion_error not in _NON_BLOCKING_COMPLETION_ERRORS:
        return _empty("research", retry["trace_id"], completion_error)

    compaction = normalize_research_compaction_events(events)
    compaction_error = compaction["normalization_error"]
    if compaction_error is not None:
        return _empty("research", retry["trace_id"], compaction_error)
    compaction_count = compaction["compaction_event_count"]
    if compaction_count is None:
        return _empty("research", retry["trace_id"], "invalid_research_trace_structure")

    step_count, diagnostics = _count_model_steps(events)
    return {
        "trace_id": retry["trace_id"],
        "mode": "research",
        "profile_version": HIGH_RISK_PROFILE_VERSION,
        "step_count": step_count,
        "api_retry_rate": api_retry_count / api_call_count,
        "api_retry_count": api_retry_count,
        "api_call_count": api_call_count,
        "compaction_event_count": compaction_count,
        "turn_completed": completion["turn_completed"],
        "diagnostics": diagnostics,
        "normalization_error": None,
    }


def _count_model_steps(
    events: Sequence[object],
) -> tuple[int, HighRiskProfileDiagnostics]:
    """step-start 按 messageID 去重计数(7.10 同口径)。

    缺 messageID 无法去重: 保守计为独立 step(宁多计不漏计,与 7.3/7.6/7.8
    缺失键同方向),进缺失诊断;同 messageID 重复投影只计一次,进重复诊断。
    """

    seen: set[str] = set()
    diagnostics: HighRiskProfileDiagnostics = {
        "duplicate_step_start_projection_count": 0,
        "missing_step_message_id_count": 0,
    }
    for event in events:
        if not isinstance(event, Mapping) or event.get("type") != "message.part.updated":
            continue
        part = event.get("part")
        if not isinstance(part, Mapping) or part.get("type") != "step-start":
            continue
        message_id = _text(part.get("messageID"))
        if message_id is None:
            diagnostics["missing_step_message_id_count"] += 1
            continue
        if message_id in seen:
            diagnostics["duplicate_step_start_projection_count"] += 1
        else:
            seen.add(message_id)
    missing = diagnostics["missing_step_message_id_count"]
    return len(seen) + missing, diagnostics


def _empty(
    mode: Literal["chat", "research"],
    trace_id: str | None,
    error: str,
) -> NormalizedHighRiskTurnProfileTrace:
    return {
        "trace_id": trace_id,
        "mode": mode,
        "profile_version": HIGH_RISK_PROFILE_VERSION if mode == "research" else None,
        "step_count": None,
        "api_retry_rate": None,
        "api_retry_count": None,
        "api_call_count": None,
        "compaction_event_count": None,
        "turn_completed": None,
        "diagnostics": {
            "duplicate_step_start_projection_count": 0,
            "missing_step_message_id_count": 0,
        },
        "normalization_error": error,
    }


def _text(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _is_sequence(value: object) -> TypeGuard[Sequence[object]]:
    return isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray))
