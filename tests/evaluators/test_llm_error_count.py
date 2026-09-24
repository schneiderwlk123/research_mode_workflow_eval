"""模型层错误数 evaluator 行为测试(稳定性维度 7.4)。

覆盖:JSON 单元格形态(主值 + 分类 + 出错模型 + 总调用数 + 模型调用分布)与稳定
排序、合法零值、聊天不可计算、分类守恒、模型归因守恒、单模型归因约束、调用守恒
(分母不因归因失败而缩小)、错误模型必在调用分项内的交叉校验、白名单校验、
诊断透传、目录版本随结果输出、非法规范化结构隔离。
"""

import json

from collectors.llm_error_count import (
    LLM_ERROR_CLASS_CATALOG_VERSION,
    normalize_chat_llm_errors,
    normalize_research_llm_errors,
)
from evaluators.base import EvaluationStatus
from evaluators.llm_error_count import LlmErrorCountEvaluator


def _diagnostics(**overrides: int) -> dict[str, int]:
    base = {
        "duplicate_error_event_count": 0,
        "message_error_projection_count": 0,
        "finish_reason_error_count": 0,
        "loop_missing_count": 0,
        "unclassified_count": 0,
        "model_route_count": 0,
        "model_unattributed_error_count": 0,
        "model_call_unattributed_count": 0,
        "missing_message_id_count": 0,
        "conflicting_model_route_count": 0,
        "non_assistant_routed_message_count": 0,
    }
    base.update(overrides)
    return base


def _normalized(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "trace_id": "session-r:message-r",
        "mode": "research",
        "llm_error_count": 0,
        "error_class_counts": {},
        "error_model_counts": [],
        "total_model_call_count": 0,
        "model_call_counts": [],
        "diagnostics": _diagnostics(),
        "normalization_error": None,
    }
    base.update(overrides)
    return base


def test_computed_value_is_single_field_compact_json() -> None:
    result = LlmErrorCountEvaluator().evaluate(
        _normalized(
            llm_error_count=3,
            error_class_counts={"context_overflow": 1, "provider_timeout": 2},
            error_model_counts=[{"model": "pro", "count": 3}],
            total_model_call_count=644,
            model_call_counts=[{"model": "pro", "count": 640}, {"model": "glm-5.3", "count": 4}],
            diagnostics=_diagnostics(model_route_count=2),
        )
    )
    assert result.status is EvaluationStatus.COMPUTED
    assert result.value == (
        '[{"llm_error_count":3,"error_classes":['
        '{"class":"provider_timeout","count":2},'
        '{"class":"context_overflow","count":1}],'
        '"error_models":[{"model":"pro","count":3}],'
        '"total_call_count":644,'
        '"model_calls":[{"model":"pro","count":640},{"model":"glm-5.3","count":4}]}]'
    )
    payload = json.loads(str(result.value))[0]
    assert payload["llm_error_count"] == 3
    assert sum(item["count"] for item in payload["error_classes"]) == 3
    assert sum(item["count"] for item in payload["error_models"]) == 3
    assert sum(item["count"] for item in payload["model_calls"]) == 644
    assert result.metadata["model_attribution"] == "single_route_turn"


def test_model_calls_sorted_by_count_desc_then_model_asc() -> None:
    result = LlmErrorCountEvaluator().evaluate(
        _normalized(
            total_model_call_count=9,
            model_call_counts=[
                {"model": "lite", "count": 3},
                {"model": "glm-5.3", "count": 3},
                {"model": "pro", "count": 3},
            ],
        )
    )
    payload = json.loads(str(result.value))[0]
    assert [item["model"] for item in payload["model_calls"]] == ["glm-5.3", "lite", "pro"]


def test_error_classes_sorted_by_count_desc_then_class_asc() -> None:
    result = LlmErrorCountEvaluator().evaluate(
        _normalized(
            llm_error_count=5,
            error_class_counts={
                "queue_timeout": 2,
                "billing_failure": 2,
                "context_overflow": 1,
            },
            diagnostics=_diagnostics(model_unattributed_error_count=5),
        )
    )
    payload = json.loads(str(result.value))[0]
    assert [item["class"] for item in payload["error_classes"]] == [
        "billing_failure",
        "queue_timeout",
        "context_overflow",
    ]
    assert payload["error_models"] == []
    assert result.metadata["model_attribution"] == "unattributed"


def test_zero_errors_is_computed_legal_zero_json() -> None:
    result = LlmErrorCountEvaluator().evaluate(_normalized())
    assert result.status is EvaluationStatus.COMPUTED
    assert result.value == (
        '[{"llm_error_count":0,"error_classes":[],"error_models":[],'
        '"total_call_count":0,"model_calls":[]}]'
    )


def test_zero_errors_with_calls_still_reports_call_distribution() -> None:
    # 零错误不等于零调用:出错率的分母必须在零错误日志上照常有值。
    result = LlmErrorCountEvaluator().evaluate(
        _normalized(
            total_model_call_count=5,
            model_call_counts=[{"model": "pro", "count": 5}],
        )
    )
    payload = json.loads(str(result.value))[0]
    assert payload["llm_error_count"] == 0
    assert payload["total_call_count"] == 5
    assert payload["model_calls"] == [{"model": "pro", "count": 5}]


def test_chat_mode_is_not_computable() -> None:
    trace = {"canvas": {"meta": {"traceId": "trace-1"}, "agents": []}}
    result = LlmErrorCountEvaluator().evaluate(normalize_chat_llm_errors(trace))
    assert result.status is EvaluationStatus.NOT_COMPUTABLE
    assert result.value is None
    assert result.metadata["reason"] == "no_model_error_events"
    assert result.metadata["error_event_semantics"] == "no_model_error_channel"


def test_research_end_to_end_from_collector() -> None:
    events = [
        {"type": "turn.start", "sessionID": "s1", "userMessageID": "m1", "ts": 1},
        {
            "type": "message.updated",
            "info": {"id": "msg-1", "role": "assistant", "modelID": "pro"},
        },
        {
            "type": "llm.error",
            "sessionID": "s1",
            "userMessageID": "m1",
            "loop": 13,
            "error": {
                "value": {
                    "error": {
                        "name": "APIError",
                        "message": "prompt is too long: 1078568 > 1000000",
                    }
                }
            },
        },
        {"type": "turn.end", "durationMs": 1000},
    ]
    result = LlmErrorCountEvaluator().evaluate(normalize_research_llm_errors(events))
    assert result.status is EvaluationStatus.COMPUTED
    assert result.value == (
        '[{"llm_error_count":1,"error_classes":[{"class":"context_overflow","count":1}],'
        '"error_models":[{"model":"pro","count":1}],'
        '"total_call_count":1,"model_calls":[{"model":"pro","count":1}]}]'
    )
    assert result.metadata["error_event_semantics"] == "llm_error_event"
    assert result.metadata["llm_error_class_version"] == LLM_ERROR_CLASS_CATALOG_VERSION
    assert result.metadata["model_attribution"] == "single_route_turn"


def test_research_end_to_end_call_distribution_from_collector() -> None:
    # 混合形状的端到端:同 id 重复投影只计一次,不同模型分别计数,无路由调用只进诊断。
    events = [
        {"type": "turn.start", "sessionID": "s1", "userMessageID": "m1", "ts": 1},
        {"type": "message.updated", "info": {"id": "a", "role": "assistant", "modelID": "pro"}},
        {"type": "message.updated", "info": {"id": "a", "role": "assistant", "modelID": "pro"}},
        {
            "type": "message.updated",
            "info": {"id": "b", "role": "assistant", "model": {"modelID": "glm-5.3"}},
        },
        {"type": "message.updated", "info": {"id": "c", "role": "assistant"}},
    ]
    result = LlmErrorCountEvaluator().evaluate(normalize_research_llm_errors(events))
    payload = json.loads(str(result.value))[0]
    assert payload["total_call_count"] == 3
    assert payload["model_calls"] == [
        {"model": "glm-5.3", "count": 1},
        {"model": "pro", "count": 1},
    ]
    assert result.metadata["model_call_unattributed_count"] == 1


def test_conservation_violation_is_isolated_error() -> None:
    result = LlmErrorCountEvaluator().evaluate(
        _normalized(llm_error_count=5, error_class_counts={"provider_timeout": 2})
    )
    assert result.status is EvaluationStatus.ERROR
    assert result.value is None
    assert result.metadata["reason"] == "invalid_normalized_trace"


def test_model_attribution_conservation_violation_is_isolated_error() -> None:
    # 已归因 2 + 未归因 0 != 主值 3
    result = LlmErrorCountEvaluator().evaluate(
        _normalized(
            llm_error_count=3,
            error_class_counts={"provider_timeout": 3},
            error_model_counts=[{"model": "pro", "count": 2}],
            model_call_counts=[{"model": "pro", "count": 2}],
            total_model_call_count=2,
            diagnostics=_diagnostics(model_route_count=1),
        )
    )
    assert result.status is EvaluationStatus.ERROR
    # 已归因计数不等于主值(单模型必须全量归因)
    result_partial = LlmErrorCountEvaluator().evaluate(
        _normalized(
            llm_error_count=3,
            error_class_counts={"provider_timeout": 3},
            error_model_counts=[{"model": "pro", "count": 2}, {"model": "glm", "count": 1}],
            model_call_counts=[{"model": "pro", "count": 2}, {"model": "glm", "count": 1}],
            total_model_call_count=2,
            diagnostics=_diagnostics(model_route_count=2),
        )
    )
    assert result_partial.status is EvaluationStatus.ERROR


def test_unattributed_must_match_total_when_no_models() -> None:
    result = LlmErrorCountEvaluator().evaluate(
        _normalized(
            llm_error_count=2,
            error_class_counts={"provider_timeout": 2},
            error_model_counts=[],
            diagnostics=_diagnostics(model_unattributed_error_count=1),
        )
    )
    assert result.status is EvaluationStatus.ERROR


def test_call_conservation_violation_is_isolated_error() -> None:
    evaluator = LlmErrorCountEvaluator()
    # 分项 1 + 未归因 0 != 总数 3
    assert (
        evaluator.evaluate(
            _normalized(
                total_model_call_count=3,
                model_call_counts=[{"model": "pro", "count": 1}],
            )
        ).status
        is EvaluationStatus.ERROR
    )
    # 分项之和超过总数
    assert (
        evaluator.evaluate(
            _normalized(
                total_model_call_count=1,
                model_call_counts=[{"model": "pro", "count": 5}],
            )
        ).status
        is EvaluationStatus.ERROR
    )
    # 未归因诊断与总数不自洽
    assert (
        evaluator.evaluate(
            _normalized(
                total_model_call_count=3,
                model_call_counts=[{"model": "pro", "count": 1}],
                diagnostics=_diagnostics(model_call_unattributed_count=3),
            )
        ).status
        is EvaluationStatus.ERROR
    )


def test_error_model_must_appear_in_call_distribution() -> None:
    # 能归因到错误的模型必然被调用过(两者同源取路由通道);否则说明通道漂移。
    result = LlmErrorCountEvaluator().evaluate(
        _normalized(
            llm_error_count=1,
            error_class_counts={"provider_timeout": 1},
            error_model_counts=[{"model": "pro", "count": 1}],
            total_model_call_count=1,
            model_call_counts=[{"model": "glm-5.3", "count": 1}],
            diagnostics=_diagnostics(model_route_count=2),
        )
    )
    assert result.status is EvaluationStatus.ERROR


def test_invalid_call_entry_shapes_are_isolated_error() -> None:
    evaluator = LlmErrorCountEvaluator()
    # 模型名缺失
    assert (
        evaluator.evaluate(
            _normalized(
                total_model_call_count=1,
                model_call_counts=[{"model": "", "count": 1}],
            )
        ).status
        is EvaluationStatus.ERROR
    )
    # 计数为 0
    assert (
        evaluator.evaluate(
            _normalized(
                total_model_call_count=1,
                model_call_counts=[{"model": "pro", "count": 0}],
                diagnostics=_diagnostics(model_call_unattributed_count=1),
            )
        ).status
        is EvaluationStatus.ERROR
    )
    # 同模型重复条目
    assert (
        evaluator.evaluate(
            _normalized(
                total_model_call_count=2,
                model_call_counts=[{"model": "pro", "count": 1}, {"model": "pro", "count": 1}],
            )
        ).status
        is EvaluationStatus.ERROR
    )
    # 负数总调用数
    assert (
        evaluator.evaluate(_normalized(total_model_call_count=-1)).status is EvaluationStatus.ERROR
    )


def test_missing_call_distribution_is_isolated_error() -> None:
    evaluator = LlmErrorCountEvaluator()
    assert evaluator.evaluate(_normalized(model_call_counts=None)).status is EvaluationStatus.ERROR
    assert (
        evaluator.evaluate(_normalized(total_model_call_count=None)).status
        is EvaluationStatus.ERROR
    )


def test_unknown_class_label_is_isolated_error() -> None:
    result = LlmErrorCountEvaluator().evaluate(
        _normalized(llm_error_count=1, error_class_counts={"hallucinated_class": 1})
    )
    assert result.status is EvaluationStatus.ERROR
    assert result.metadata["reason"] == "invalid_normalized_trace"


def test_zero_or_negative_class_count_is_isolated_error() -> None:
    result = LlmErrorCountEvaluator().evaluate(
        _normalized(llm_error_count=0, error_class_counts={"provider_timeout": 0})
    )
    assert result.status is EvaluationStatus.ERROR
    result_negative = LlmErrorCountEvaluator().evaluate(
        _normalized(llm_error_count=1, error_class_counts={"provider_timeout": -1})
    )
    assert result_negative.status is EvaluationStatus.ERROR


def test_invalid_model_entry_shapes_are_isolated_error() -> None:
    evaluator = LlmErrorCountEvaluator()
    base = {"llm_error_count": 1, "error_class_counts": {"provider_timeout": 1}}
    # 模型名缺失
    assert (
        evaluator.evaluate(
            _normalized(
                **base,
                error_model_counts=[{"model": "", "count": 1}],
                total_model_call_count=1,
                model_call_counts=[{"model": "", "count": 1}],
            )
        ).status
        is EvaluationStatus.ERROR
    )
    # 计数非法
    assert (
        evaluator.evaluate(
            _normalized(
                **base,
                error_model_counts=[{"model": "m", "count": 0}],
                total_model_call_count=1,
                model_call_counts=[{"model": "m", "count": 1}],
            )
        ).status
        is EvaluationStatus.ERROR
    )
    # 同模型重复条目
    assert (
        evaluator.evaluate(
            _normalized(
                llm_error_count=2,
                error_class_counts={"provider_timeout": 2},
                error_model_counts=[{"model": "m", "count": 1}, {"model": "m", "count": 1}],
                total_model_call_count=2,
                model_call_counts=[{"model": "m", "count": 2}],
                diagnostics=_diagnostics(model_route_count=1),
            )
        ).status
        is EvaluationStatus.ERROR
    )


def test_negative_total_count_is_isolated_error() -> None:
    result = LlmErrorCountEvaluator().evaluate(_normalized(llm_error_count=-1))
    assert result.status is EvaluationStatus.ERROR


def test_diagnostics_and_versions_pass_through_metadata() -> None:
    result = LlmErrorCountEvaluator().evaluate(
        _normalized(
            llm_error_count=2,
            error_class_counts={"unclassified": 2},
            error_model_counts=[{"model": "glm-5.3", "count": 2}],
            total_model_call_count=3,
            model_call_counts=[{"model": "glm-5.3", "count": 2}],
            diagnostics=_diagnostics(
                duplicate_error_event_count=1,
                message_error_projection_count=4,
                finish_reason_error_count=1,
                loop_missing_count=2,
                unclassified_count=2,
                model_route_count=1,
                model_call_unattributed_count=1,
                conflicting_model_route_count=1,
            ),
        )
    )
    assert result.status is EvaluationStatus.COMPUTED
    assert result.metadata["duplicate_error_event_count"] == 1
    assert result.metadata["message_error_projection_count"] == 4
    assert result.metadata["finish_reason_error_count"] == 1
    assert result.metadata["loop_missing_count"] == 2
    assert result.metadata["unclassified_count"] == 2
    assert result.metadata["model_route_count"] == 1
    assert result.metadata["model_unattributed_error_count"] == 0
    assert result.metadata["model_call_unattributed_count"] == 1
    assert result.metadata["conflicting_model_route_count"] == 1
    assert result.metadata["llm_error_class_version"] == LLM_ERROR_CLASS_CATALOG_VERSION
    assert result.metadata["mode"] == "research"


def test_invalid_normalized_structures_are_isolated() -> None:
    evaluator = LlmErrorCountEvaluator()
    assert evaluator.evaluate({}).status is EvaluationStatus.ERROR
    assert evaluator.evaluate(_normalized(mode="unknown")).status is EvaluationStatus.ERROR
    # error 与计数互斥:有 error 不得携带计数
    assert (
        evaluator.evaluate(
            _normalized(normalization_error="no_model_error_events", llm_error_count=1)
        ).status
        is EvaluationStatus.ERROR
    )
    # 无 error 必须同时携带计数、分类、归因与调用分布
    assert evaluator.evaluate(_normalized(llm_error_count=None)).status is EvaluationStatus.ERROR
    assert evaluator.evaluate(_normalized(error_class_counts=None)).status is EvaluationStatus.ERROR
    assert evaluator.evaluate(_normalized(error_model_counts=None)).status is EvaluationStatus.ERROR
    assert (
        evaluator.evaluate(_normalized(total_model_call_count=None)).status
        is EvaluationStatus.ERROR
    )
    assert evaluator.evaluate(_normalized(model_call_counts=None)).status is EvaluationStatus.ERROR
    # 未知 normalization_error 统一隔离
    assert (
        evaluator.evaluate(
            _normalized(
                normalization_error="mystery",
                llm_error_count=None,
                error_class_counts=None,
                error_model_counts=None,
                total_model_call_count=None,
                model_call_counts=None,
            )
        ).status
        is EvaluationStatus.ERROR
    )
    # 诊断计数非法
    assert (
        evaluator.evaluate(
            _normalized(diagnostics=_diagnostics(duplicate_error_event_count=-1))
        ).status
        is EvaluationStatus.ERROR
    )
    # 新增调用诊断键缺失同样非法
    for key in (
        "model_route_count",
        "model_call_unattributed_count",
        "missing_message_id_count",
        "conflicting_model_route_count",
        "non_assistant_routed_message_count",
    ):
        partial = _diagnostics()
        del partial[key]
        assert evaluator.evaluate(_normalized(diagnostics=partial)).status is EvaluationStatus.ERROR


def test_metric_contract_is_stable() -> None:
    evaluator = LlmErrorCountEvaluator()
    assert evaluator.metric_name == "llm_error_count"
    assert evaluator.evaluator_version == "1.3.0"
