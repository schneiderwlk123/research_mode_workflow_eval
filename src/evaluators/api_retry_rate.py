"""计算单条 Trace 的框架 API 重试率并输出紧凑 JSON 值(稳定性维度 7.8)。

口径(《初步入库评测指标.md》7.8,2026-09-23 修订):
- `api_retry_rate = 同一 (callID, 入参指纹) 重复请求数 ÷ apiCalls 总条数`。
  2026-09-23 全量实勘发现 requestId 尾序号是"调用内第 N 个请求"的序号而非重试
  计数,故弃用尾序号口径(fingerprint_dup_v2 取代 request_id_suffix_v1);
  计量工具内部传输/业务层的重复请求(重试上界),不是埋点 D 的模型级重试;
  `retry_semantics`(fingerprint_dup_v2 / no_tool_debug_channel)随元数据声明。
- 值列为单元素 JSON 数组(2026-09-21 需求方确认统一 [] 包围,键序固定):
  比率 + 重试/调用计数 + 业务错误数 +
  不可判定数 + 覆盖率;覆盖率必须随值输出(tool.debug 仅覆盖部分工具,
  解读受限)。报告级按 endpoint/业务错误码的聚合矩阵留 reporter 批次,
  错误码分布随元数据保留。
- 无 tool.debug 事件 → NOT_COMPUTABLE / no_tool_debug_events(不代表无重试);
  有事件但无 apiCalls → NOT_COMPUTABLE / no_observable_api_calls;
  结构问题 → ERROR 并携带稳定原因码。
- 守恒校验:重试 <= 调用、业务错误 + 不可判定 <= 调用、错误码分布之和 =
  业务错误数、覆盖率与工具 callID 数互证;违约隔离为 invalid_normalized_trace。
"""

import json
from collections.abc import Mapping, Sequence
from typing import TypeGuard

from evaluators.base import BaseEvaluator, EvaluationResult, EvaluationStatus

_NOT_COMPUTABLE_REASONS = frozenset({"no_tool_debug_events", "no_observable_api_calls"})
_ERROR_REASONS = frozenset({"invalid_chat_trace_structure", "invalid_research_trace_structure"})
_SEMANTICS_BY_MODE = {
    "chat": "no_tool_debug_channel",
    "research": "fingerprint_dup_v2",
}
_DIAGNOSTIC_KEYS = (
    "duplicate_api_call_projection_count",
    "malformed_request_id_count",
    "undeterminable_api_count",
    "orphan_tool_debug_count",
    "missing_debug_call_id_count",
    "malformed_tool_part_count",
)


class ApiRetryRateEvaluator(BaseEvaluator):
    """校验规范化重试证据并输出单字段紧凑 JSON;缺通道不可计算,不猜测。"""

    @property
    def metric_name(self) -> str:
        return "api_retry_rate"

    @property
    def evaluator_version(self) -> str:
        return "2.0.0"

    def evaluate(self, trace: Mapping[str, object]) -> EvaluationResult:
        parsed = _parse_trace(trace)
        if parsed is None:
            return self._result(
                None, EvaluationStatus.ERROR, {"reason": "invalid_normalized_trace"}
            )
        trace_id, mode, counts, diagnostics, error = parsed
        metadata: dict[str, object] = {
            "trace_id": trace_id,
            "mode": mode,
            # _parse_trace 已保证 mode ∈ _SEMANTICS_BY_MODE。
            "retry_semantics": _SEMANTICS_BY_MODE[mode],
            **{key: diagnostics[key] for key in _DIAGNOSTIC_KEYS},
        }
        if error is not None:
            metadata["reason"] = error
            if error in _NOT_COMPUTABLE_REASONS:
                metadata["debug_call_id_count"] = counts["debug_call_id_count"]
                return self._result(None, EvaluationStatus.NOT_COMPUTABLE, metadata)
            if error in _ERROR_REASONS:
                return self._result(None, EvaluationStatus.ERROR, metadata)
            return self._result(
                None, EvaluationStatus.ERROR, {"reason": "invalid_normalized_trace"}
            )

        call_count = counts["api_call_count"]
        retry_count = counts["api_retry_count"]
        # _parse_trace 已保证计算路径下 call_count >= 1 且各计数为合法整数。
        assert isinstance(call_count, int) and call_count >= 1  # noqa: S101
        assert isinstance(retry_count, int)  # noqa: S101
        payload = {
            "api_retry_rate": retry_count / call_count,
            "api_retry_count": retry_count,
            "api_call_count": call_count,
            "api_business_error_count": counts["api_business_error_count"],
            "undeterminable_api_count": diagnostics["undeterminable_api_count"],
            "api_debug_coverage": counts["api_debug_coverage"],
        }
        metadata["tool_call_id_count"] = counts["tool_call_id_count"]
        metadata["debug_call_id_count"] = counts["debug_call_id_count"]
        metadata["business_error_codes"] = counts["business_error_codes"]
        # 工作流深度(调用内最大请求序号),保留旧口径意外捕捉到的串行步数信号。
        metadata["api_request_depth_max"] = counts["api_request_depth_max"]
        # 框架层归因(by_tool / by_endpoint / retry_after_success),供宽表独立列与定位。
        metadata["api_retry_attribution"] = counts["api_retry_attribution"]
        # 单元格统一 [] 包围:单元素数组承载固定键序对象(2026-09-21 需求方确认)。
        value = json.dumps([payload], ensure_ascii=False, separators=(",", ":"))
        return self._result(value, EvaluationStatus.COMPUTED, metadata)

    def _result(
        self,
        value: str | None,
        status: EvaluationStatus,
        metadata: Mapping[str, object],
    ) -> EvaluationResult:
        return EvaluationResult(self.metric_name, self.evaluator_version, value, status, metadata)


def _parse_trace(
    trace: Mapping[str, object],
) -> tuple[str | None, str, dict[str, object], Mapping[str, object], str | None] | None:
    if not isinstance(trace, Mapping):
        return None
    mode = _text(trace.get("mode"))
    if mode not in _SEMANTICS_BY_MODE:
        return None
    trace_id_value = trace.get("trace_id")
    trace_id = _text(trace_id_value)
    if trace_id_value is not None and trace_id is None:
        return None

    diagnostics = trace.get("diagnostics")
    if not isinstance(diagnostics, Mapping):
        return None
    for key in _DIAGNOSTIC_KEYS:
        if not _is_count(diagnostics.get(key)):
            return None

    error_value = trace.get("normalization_error")
    error = _text(error_value)
    if error_value is not None and error is None:
        return None
    if mode == "chat" and error is None:
        # 聊天模式无 API 明细通道,契约上恒携带 no_tool_debug_events。
        return None

    counts = _parse_counts(trace, diagnostics, error)
    if counts is None:
        return None
    return trace_id, mode, counts, diagnostics, error


def _parse_counts(
    trace: Mapping[str, object],
    diagnostics: Mapping[str, object],
    error: str | None,
) -> dict[str, object] | None:
    """校验计数字段;error 路径执行互斥校验,计算路径执行守恒校验。"""

    codes_value = trace.get("business_error_codes")
    debug_call_id_count = trace.get("debug_call_id_count")
    if error is not None:
        # error 与主值互斥:计数与覆盖率必须为 None;business_error_codes 必须为空;
        # debug_call_id_count 允许保留(no_observable_api_calls 携带事件覆盖证据)。
        if (
            trace.get("api_call_count") is not None
            or trace.get("api_retry_count") is not None
            or trace.get("api_business_error_count") is not None
            or trace.get("api_debug_coverage") is not None
            or trace.get("tool_call_id_count") is not None
            or not _is_sequence(codes_value)
            or len(codes_value) != 0
        ):
            return None
        if debug_call_id_count is not None and not _is_count(debug_call_id_count):
            return None
        return {
            "api_call_count": None,
            "api_retry_count": None,
            "api_business_error_count": None,
            "business_error_codes": (),
            "tool_call_id_count": None,
            "debug_call_id_count": debug_call_id_count,
            "api_debug_coverage": None,
            "api_request_depth_max": None,
            "api_retry_attribution": None,
        }

    call_count = trace.get("api_call_count")
    retry_count = trace.get("api_retry_count")
    business_error_count = trace.get("api_business_error_count")
    tool_call_id_count = trace.get("tool_call_id_count")
    coverage = trace.get("api_debug_coverage")
    depth = trace.get("api_request_depth_max")
    if (
        not _is_count(call_count)
        or call_count < 1
        or not _is_count(retry_count)
        or not _is_count(business_error_count)
        or not _is_count(tool_call_id_count)
        or not _is_count(debug_call_id_count)
        or retry_count > call_count
    ):
        return None
    # 深度为"调用内最大请求序号",计算路径必须 >=1;缺省(None)视为违约隔离。
    if depth is not None and (not _is_count(depth) or depth < 1):
        return None
    # 框架层归因守恒:by_tool / by_endpoint 计数之和 = 重试数;
    # retry_after_success <= 重试数(每次"前置成功仍重发"必是一次重试)。
    attribution = trace.get("api_retry_attribution")
    if attribution is not None:
        if not isinstance(attribution, Mapping):
            return None
        bt = attribution.get("by_tool")
        be = attribution.get("by_endpoint")
        ras = attribution.get("retry_after_success")
        if not isinstance(bt, Mapping) or not isinstance(be, Mapping):
            return None
        if not _is_count(ras) or ras > retry_count:
            return None
        if not all(_is_count(v) for v in bt.values()) or not all(_is_count(v) for v in be.values()):
            return None
        if sum(bt.values()) != retry_count or sum(be.values()) != retry_count:
            return None
    undeterminable = diagnostics["undeterminable_api_count"]
    assert isinstance(undeterminable, int)  # noqa: S101 - _parse_trace 已校验
    if business_error_count + undeterminable > call_count:
        return None
    codes = _parse_business_error_codes(codes_value)
    if codes is None or sum(count for _, count in codes) != business_error_count:
        return None
    if tool_call_id_count == 0:
        if coverage is not None:
            return None
    else:
        if (
            isinstance(coverage, bool)
            or not isinstance(coverage, (int, float))
            or not 0.0 <= float(coverage) <= 1.0
        ):
            return None
    return {
        "api_call_count": call_count,
        "api_retry_count": retry_count,
        "api_business_error_count": business_error_count,
        "business_error_codes": codes,
        "tool_call_id_count": tool_call_id_count,
        "debug_call_id_count": debug_call_id_count,
        "api_debug_coverage": float(coverage) if coverage is not None else None,
        "api_request_depth_max": depth,
        "api_retry_attribution": attribution,
    }


def _parse_business_error_codes(value: object) -> tuple[tuple[str, int], ...] | None:
    """校验错误码分布: (非空码, 正整数计数) 对、按码严格升序且不重复。"""

    if not _is_sequence(value):
        return None
    codes: list[tuple[str, int]] = []
    previous: str | None = None
    for item in value:
        if not _is_sequence(item) or len(item) != 2:
            return None
        code = _text(item[0])
        count = item[1]
        if code is None or not _is_count(count) or count < 1:
            return None
        if previous is not None and code <= previous:
            return None
        previous = code
        codes.append((code, count))
    return tuple(codes)


def _is_count(value: object) -> TypeGuard[int]:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _text(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _is_sequence(value: object) -> TypeGuard[Sequence[object]]:
    return isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray))
