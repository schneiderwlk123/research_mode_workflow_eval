"""工具错误率评测器的行为测试。

覆盖:紧凑 JSON 值列结构、合法零值、不可计算原因码、输入错误隔离、诊断计数
交叉校验、逐调用契约(attribution 与 terminal_state 的互斥关系)、双模式语义
标签与版本声明校验、输入不变,以及与双模式 collector 的端到端一致性。
"""

import copy
import json
from typing import cast

import pytest

from collectors.tool_error_rate import (
    normalize_chat_tool_error_rate,
    normalize_research_tool_error_rate,
)
from evaluators.base import EvaluationStatus
from evaluators.tool_error_rate import ToolErrorRateEvaluator

_VALUE_KEYS = (
    "tool_error_rate",
    "error_call_count",
    "terminal_call_count",
    "model_misuse_error_count",
    "infrastructure_error_count",
    "data_availability_error_count",
    "degraded_result_count",
    "unclassified_error_count",
)


def _call(
    call_id: str,
    *,
    tool_name: str = "tool_a",
    terminal_state: str = "success",
    attribution: str | None = None,
) -> dict[str, object]:
    return {
        "call_id": call_id,
        "tool_name": tool_name,
        "terminal_state": terminal_state,
        "attribution": attribution,
    }


def _normalized(**overrides: object) -> dict[str, object]:
    """构造 8 个终态调用(2 error + 1 degraded + 5 success)的合法规范化输入。"""

    calls = (
        _call("c-1", terminal_state="error", attribution="model_misuse"),
        _call(
            "c-2", tool_name="tool_b", terminal_state="error", attribution="infrastructure_failure"
        ),
        _call("c-3", tool_name="tool_c", terminal_state="degraded"),
        _call("c-4"),
        _call("c-5"),
        _call("c-6"),
        _call("c-7"),
        _call("c-8"),
    )
    trace: dict[str, object] = {
        "trace_id": "chat-1",
        "mode": "chat",
        "terminal_semantics": "payload_proxy_v1",
        "attribution_catalog_version": "tool_error_attribution_v1",
        "failure_signal_rules_version": "chat_failure_signals_v1",
        "tool_calls": calls,
        "diagnostics": {
            "model_misuse_error_count": 1,
            "infrastructure_error_count": 1,
            "data_availability_error_count": 0,
            "unclassified_error_count": 0,
            "degraded_result_count": 1,
            "terminal_missing_count": 1,
            "undeterminable_payload_count": 0,
            "duplicate_observation_count": 0,
            "orphan_result_count": 0,
            "conflicting_terminal_state_count": 0,
            "excluded_agent_delegation_count": 0,
            "excluded_agent_return_count": 0,
            "malformed_event_count": 0,
        },
        "normalization_error": None,
    }
    trace.update(overrides)
    return trace


def test_computes_rate_and_compact_json_value() -> None:
    result = ToolErrorRateEvaluator().evaluate(_normalized())

    assert result.status is EvaluationStatus.COMPUTED
    assert result.metric_name == "tool_error_rate"
    assert result.evaluator_version == "1.1.0"
    assert isinstance(result.value, str)
    cell = json.loads(result.value)
    # 2026-09-21 需求方确认:单元格统一 [] 包围,单元素 JSON 数组承载固定键序对象。
    assert isinstance(cell, list) and len(cell) == 1
    parsed = cell[0]
    assert tuple(parsed) == _VALUE_KEYS
    assert parsed["tool_error_rate"] == pytest.approx(2 / 8)
    assert parsed["error_call_count"] == 2
    assert parsed["terminal_call_count"] == 8
    assert parsed["model_misuse_error_count"] == 1
    assert parsed["infrastructure_error_count"] == 1
    assert parsed["degraded_result_count"] == 1
    # 值列不含逐项局部状态;归因明细留给元数据与 reporter。
    assert result.metadata["terminal_semantics"] == "payload_proxy_v1"
    assert result.metadata["error_calls"] == (
        {"tool_name": "tool_a", "attribution": "model_misuse"},
        {"tool_name": "tool_b", "attribution": "infrastructure_failure"},
    )


def test_zero_error_rate_is_computed() -> None:
    trace = _normalized()
    trace["tool_calls"] = (_call("c-1"), _call("c-2"))
    diagnostics = dict(cast(dict[str, int], trace["diagnostics"]))
    diagnostics.update(
        {
            "model_misuse_error_count": 0,
            "infrastructure_error_count": 0,
            "degraded_result_count": 0,
            "terminal_missing_count": 0,
        }
    )
    trace["diagnostics"] = diagnostics

    result = ToolErrorRateEvaluator().evaluate(trace)

    assert result.status is EvaluationStatus.COMPUTED
    assert json.loads(result.value)[0]["tool_error_rate"] == 0.0  # type: ignore[arg-type]


def test_not_computable_reasons_are_preserved() -> None:
    evaluator = ToolErrorRateEvaluator()
    for reason in ("no_tool_calls", "no_terminal_tool_calls"):
        result = evaluator.evaluate(_normalized(tool_calls=(), normalization_error=reason))
        assert result.status is EvaluationStatus.NOT_COMPUTABLE
        assert result.value is None
        assert result.metadata["reason"] == reason


def test_unsupported_mode_is_not_computable() -> None:
    result = ToolErrorRateEvaluator().evaluate(_normalized(mode="hybrid"))

    assert result.status is EvaluationStatus.NOT_COMPUTABLE
    assert result.metadata["reason"] == "unsupported_trace_mode"


@pytest.mark.parametrize(
    "reason",
    (
        "invalid_chat_trace_structure",
        "invalid_research_trace_structure",
        "missing_trace_id",
        "missing_call_id",
        "missing_tool_name",
        "conflicting_call_definition",
    ),
)
def test_input_error_reasons_map_to_error_status(reason: str) -> None:
    result = ToolErrorRateEvaluator().evaluate(
        _normalized(tool_calls=(), normalization_error=reason)
    )

    assert result.status is EvaluationStatus.ERROR
    assert result.metadata["reason"] == reason


def test_unknown_normalization_error_is_invalid_trace() -> None:
    result = ToolErrorRateEvaluator().evaluate(
        _normalized(tool_calls=(), normalization_error="something_else")
    )

    assert result.status is EvaluationStatus.ERROR
    assert result.metadata["reason"] == "invalid_normalized_trace"


def test_empty_calls_without_reason_is_invalid_trace() -> None:
    result = ToolErrorRateEvaluator().evaluate(_normalized(tool_calls=()))

    assert result.status is EvaluationStatus.ERROR
    assert result.metadata["reason"] == "invalid_normalized_trace"


def test_diagnostics_mismatch_is_rejected() -> None:
    trace = _normalized()
    diagnostics = dict(cast(dict[str, int], trace["diagnostics"]))
    diagnostics["model_misuse_error_count"] = 5
    trace["diagnostics"] = diagnostics

    result = ToolErrorRateEvaluator().evaluate(trace)

    assert result.status is EvaluationStatus.ERROR
    assert result.metadata["reason"] == "invalid_normalized_trace"


def test_call_contract_violations_are_rejected() -> None:
    evaluator = ToolErrorRateEvaluator()
    variants = (
        # success 调用不得携带归因。
        _normalized(tool_calls=(_call("c-1", attribution="model_misuse"),)),
        # error 调用必须携带目录内归因。
        _normalized(tool_calls=(_call("c-1", terminal_state="error"),)),
        _normalized(tool_calls=(_call("c-1", terminal_state="error", attribution="vendor_bug"),)),
        # 未知终态。
        _normalized(tool_calls=(_call("c-1", terminal_state="timeout"),)),
        # 重复调用 ID。
        _normalized(tool_calls=(_call("c-1"), _call("c-1"))),
    )
    for trace in variants:
        result = evaluator.evaluate(trace)
        assert result.status is EvaluationStatus.ERROR
        assert result.metadata["reason"] == "invalid_normalized_trace"


def test_semantics_and_version_declarations_are_validated() -> None:
    evaluator = ToolErrorRateEvaluator()
    wrong_semantics = evaluator.evaluate(_normalized(terminal_semantics="explicit_status_v1"))
    missing_failure_rules = evaluator.evaluate(_normalized(failure_signal_rules_version=None))
    research_with_failure_rules = evaluator.evaluate(
        _normalized(
            mode="research",
            terminal_semantics="explicit_status_v1",
            failure_signal_rules_version="chat_failure_signals_v1",
        )
    )
    research_valid = evaluator.evaluate(
        _normalized(
            mode="research",
            terminal_semantics="explicit_status_v1",
            failure_signal_rules_version=None,
        )
    )

    assert wrong_semantics.status is EvaluationStatus.ERROR
    assert wrong_semantics.metadata["reason"] == "invalid_normalized_trace"
    assert missing_failure_rules.status is EvaluationStatus.ERROR
    assert research_with_failure_rules.status is EvaluationStatus.ERROR
    assert research_valid.status is EvaluationStatus.COMPUTED
    assert research_valid.metadata["failure_signal_rules_version"] is None


def test_research_mode_end_to_end_with_collector() -> None:
    events = [
        {
            "type": "message.part.updated",
            "part": {
                "type": "tool",
                "callID": "call-1",
                "tool": "edit",
                "state": {"status": "error", "error": "Found multiple matches for oldString"},
            },
        },
        {
            "type": "message.part.updated",
            "part": {
                "type": "tool",
                "callID": "call-2",
                "tool": "grep",
                "state": {"status": "completed"},
            },
        },
    ]

    result = ToolErrorRateEvaluator().evaluate(
        normalize_research_tool_error_rate(events, session_id="ses-1", user_message_id="msg-1")
    )

    assert result.status is EvaluationStatus.COMPUTED
    parsed = json.loads(result.value)[0]  # type: ignore[arg-type]
    assert parsed["tool_error_rate"] == pytest.approx(0.5)
    assert parsed["model_misuse_error_count"] == 1
    assert result.metadata["mode"] == "research"
    assert result.metadata["terminal_semantics"] == "explicit_status_v1"


def test_chat_mode_end_to_end_with_collector() -> None:
    trace = {
        "canvas": {
            "meta": {"traceId": "chat-1"},
            "agents": [
                {
                    "messages": [
                        {
                            "content": {
                                "tool_call": [
                                    {"toolId": "c-1", "toolName": "tool_a", "arguments": {}},
                                    {"toolId": "c-2", "toolName": "tool_b", "arguments": {}},
                                ],
                                "tool_result": [
                                    {
                                        "toolId": "c-1",
                                        "toolName": "tool_a",
                                        "result": {"error_code": ""},
                                    },
                                    {
                                        "toolId": "c-2",
                                        "toolName": "tool_b",
                                        "result": {"errorcode": "500", "msg": "所有年报提取均失败"},
                                    },
                                ],
                            }
                        }
                    ]
                }
            ],
        }
    }

    result = ToolErrorRateEvaluator().evaluate(normalize_chat_tool_error_rate(trace))

    assert result.status is EvaluationStatus.COMPUTED
    parsed = json.loads(result.value)[0]  # type: ignore[arg-type]
    # error_code:"" 硬边界:call-1 为成功,只有 call-2 进入分子。
    assert parsed["tool_error_rate"] == pytest.approx(0.5)
    assert parsed["infrastructure_error_count"] == 1


def test_input_is_not_mutated() -> None:
    trace = _normalized()
    original = copy.deepcopy(trace)

    ToolErrorRateEvaluator().evaluate(trace)

    assert trace == original
