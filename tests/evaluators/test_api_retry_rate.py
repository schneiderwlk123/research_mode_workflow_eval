"""框架 API 重试率评测器行为测试(稳定性维度 7.8)。

覆盖: 紧凑 JSON 单元格键序与值、零重试合法值、覆盖率 null 形态、两级
不可计算原因、结构错误原因、未知原因与聊天无 error 契约违约隔离、
守恒校验(重试≤调用、错误码分布之和=业务错误数、错误+不可判定≤调用、
覆盖率与工具 callID 数互证)、非法类型拒绝、语义标签与诊断透传、
敏感正文不入结果。
"""

import json
from copy import deepcopy

from evaluators.api_retry_rate import ApiRetryRateEvaluator
from evaluators.base import EvaluationStatus

_CELL_KEYS = [
    "api_retry_rate",
    "api_retry_count",
    "api_call_count",
    "api_business_error_count",
    "undeterminable_api_count",
    "api_debug_coverage",
]

_ZERO_DIAGNOSTICS = {
    "duplicate_api_call_projection_count": 0,
    "malformed_request_id_count": 0,
    "undeterminable_api_count": 0,
    "orphan_tool_debug_count": 0,
    "missing_debug_call_id_count": 0,
    "malformed_tool_part_count": 0,
}


def _research_trace(**overrides: object) -> dict[str, object]:
    trace: dict[str, object] = {
        "trace_id": "ses-1:msg-1",
        "mode": "research",
        "api_call_count": 10,
        "api_retry_count": 4,
        "api_business_error_count": 2,
        "business_error_codes": (("11410", 1), ("11443", 1)),
        "tool_call_id_count": 4,
        "debug_call_id_count": 2,
        "api_debug_coverage": 0.5,
        "api_request_depth_max": 6,
        "diagnostics": {**_ZERO_DIAGNOSTICS, "undeterminable_api_count": 3},
        "normalization_error": None,
    }
    trace.update(overrides)
    return trace


def _chat_trace(error: str = "no_tool_debug_events") -> dict[str, object]:
    return {
        "trace_id": "trace-1",
        "mode": "chat",
        "api_call_count": None,
        "api_retry_count": None,
        "api_business_error_count": None,
        "business_error_codes": (),
        "tool_call_id_count": None,
        "debug_call_id_count": None,
        "api_debug_coverage": None,
        "api_request_depth_max": None,
        "diagnostics": dict(_ZERO_DIAGNOSTICS),
        "normalization_error": error,
    }


def test_computed_cell_is_compact_json_with_fixed_key_order() -> None:
    evaluator = ApiRetryRateEvaluator()
    trace = _research_trace()
    original = deepcopy(trace)

    result = evaluator.evaluate(trace)

    assert result.status is EvaluationStatus.COMPUTED
    assert isinstance(result.value, str)
    array = json.loads(result.value)
    # 2026-09-21 需求方确认:单元格统一 [] 包围,单元素 JSON 数组承载固定键序对象。
    assert isinstance(array, list) and len(array) == 1
    cell = array[0]
    assert list(cell.keys()) == _CELL_KEYS
    assert cell["api_retry_rate"] == 0.4
    assert cell["api_retry_count"] == 4
    assert cell["api_call_count"] == 10
    assert cell["api_business_error_count"] == 2
    assert cell["undeterminable_api_count"] == 3
    assert cell["api_debug_coverage"] == 0.5
    assert result.metadata["retry_semantics"] == "fingerprint_dup_v2"
    assert result.metadata["api_request_depth_max"] == 6
    assert result.metadata["business_error_codes"] == (("11410", 1), ("11443", 1))
    assert trace == original


def test_zero_retry_is_legal_computed_value() -> None:
    evaluator = ApiRetryRateEvaluator()
    trace = _research_trace(
        api_retry_count=0,
        api_business_error_count=0,
        business_error_codes=(),
        diagnostics=dict(_ZERO_DIAGNOSTICS),
    )

    result = evaluator.evaluate(trace)

    assert result.status is EvaluationStatus.COMPUTED
    cell = json.loads(str(result.value))[0]
    assert cell["api_retry_rate"] == 0.0
    assert cell["api_retry_count"] == 0


def test_coverage_null_without_tool_call_ids() -> None:
    evaluator = ApiRetryRateEvaluator()
    trace = _research_trace(tool_call_id_count=0, api_debug_coverage=None)

    result = evaluator.evaluate(trace)

    assert result.status is EvaluationStatus.COMPUTED
    cell = json.loads(str(result.value))[0]
    assert cell["api_debug_coverage"] is None


def test_chat_channel_reason_is_not_computable() -> None:
    evaluator = ApiRetryRateEvaluator()

    result = evaluator.evaluate(_chat_trace())

    assert result.status is EvaluationStatus.NOT_COMPUTABLE
    assert result.value is None
    assert result.metadata["reason"] == "no_tool_debug_events"
    assert result.metadata["retry_semantics"] == "no_tool_debug_channel"


def test_no_observable_api_calls_is_not_computable() -> None:
    evaluator = ApiRetryRateEvaluator()
    trace = _chat_trace("no_observable_api_calls")
    trace["mode"] = "research"
    trace["debug_call_id_count"] = 1

    result = evaluator.evaluate(trace)

    assert result.status is EvaluationStatus.NOT_COMPUTABLE
    assert result.metadata["reason"] == "no_observable_api_calls"
    assert result.metadata["debug_call_id_count"] == 1


def test_structural_error_reasons_are_errors() -> None:
    evaluator = ApiRetryRateEvaluator()
    for reason in ("invalid_chat_trace_structure", "invalid_research_trace_structure"):
        result = evaluator.evaluate(_chat_trace(reason))
        assert result.status is EvaluationStatus.ERROR
        assert result.metadata["reason"] == reason


def test_unknown_reason_and_chat_without_error_are_isolated() -> None:
    evaluator = ApiRetryRateEvaluator()
    result = evaluator.evaluate(_chat_trace("weird_reason"))
    assert result.status is EvaluationStatus.ERROR
    assert result.metadata["reason"] == "invalid_normalized_trace"

    result = evaluator.evaluate(_chat_trace(None))  # type: ignore[arg-type]
    assert result.status is EvaluationStatus.ERROR
    assert result.metadata["reason"] == "invalid_normalized_trace"


def test_conservation_violations_are_isolated() -> None:
    evaluator = ApiRetryRateEvaluator()
    violations = [
        _research_trace(api_retry_count=11),  # 重试 > 调用
        _research_trace(
            api_business_error_count=8,
            diagnostics={**_ZERO_DIAGNOSTICS, "undeterminable_api_count": 3},
        ),  # 错误 + 不可判定 > 调用
        _research_trace(business_error_codes=(("11410", 1),)),  # 分布之和 != 业务错误数
        _research_trace(business_error_codes=(("11443", 1), ("11410", 1))),  # 未排序
        _research_trace(business_error_codes=(("11410", 1), ("11410", 1))),  # 重复码
        _research_trace(api_debug_coverage=None),  # 有工具 callID 却缺覆盖率
        _research_trace(tool_call_id_count=0),  # 无工具 callID 却携带覆盖率
        _research_trace(api_debug_coverage=1.5),  # 覆盖率越界
        _research_trace(api_call_count=0),  # 分母为零必须走不可计算
        _research_trace(api_call_count=True),  # 布尔混入
        _research_trace(api_retry_count=-1),
        _research_trace(business_error_codes=(("11410", 0), ("11443", 2))),  # 零计数码
        _research_trace(diagnostics={**_ZERO_DIAGNOSTICS, "orphan_tool_debug_count": -1}),
    ]
    for trace in violations:
        result = evaluator.evaluate(trace)
        assert result.status is EvaluationStatus.ERROR, trace
        assert result.metadata["reason"] == "invalid_normalized_trace", trace


def test_error_state_must_not_carry_counts() -> None:
    evaluator = ApiRetryRateEvaluator()
    trace = _chat_trace("no_tool_debug_events")
    trace["api_call_count"] = 3

    result = evaluator.evaluate(trace)

    assert result.status is EvaluationStatus.ERROR
    assert result.metadata["reason"] == "invalid_normalized_trace"


def test_non_mapping_trace_is_isolated() -> None:
    evaluator = ApiRetryRateEvaluator()
    for trace in ("junk", None, [1, 2]):
        result = evaluator.evaluate(trace)  # type: ignore[arg-type]
        assert result.status is EvaluationStatus.ERROR
        assert result.metadata["reason"] == "invalid_normalized_trace"
