from collections.abc import Mapping, Sequence

import pytest

from collectors.routing import CapabilityCatalog, build_capability_catalog
from evaluators.base import EvaluationStatus
from evaluators.routing import (
    ReusableIntentRouteMapping,
    RoutingJudgeRequest,
    RoutingPrecisionEvaluator,
    RoutingRecallEvaluator,
    build_routing_assessment,
)


class FakeJudge:
    judge_name = "fake-routing-judge"
    judge_version = "test-model"
    prompt_version = "1.0.0"

    def __init__(self, response: object) -> None:
        self.response = response
        self.requests: list[RoutingJudgeRequest] = []

    def judge(self, request: RoutingJudgeRequest) -> Mapping[str, object]:
        self.requests.append(request)
        if isinstance(self.response, Exception):
            raise self.response
        assert isinstance(self.response, Mapping)
        return self.response


class FakeMappingStore:
    def __init__(self) -> None:
        self.saved: tuple[ReusableIntentRouteMapping, ...] = ()
        self.loaded = (
            ReusableIntentRouteMapping(
                intent_signature=("market_data",),
                subtask_type="quote_lookup",
                capability_ids=("quote",),
                schema_version="0.1.0",
                judge_name="old-judge",
                judge_version="old-model",
                prompt_version="0.9.0",
            ),
        )

    def load_recent(self, *, limit: int) -> Sequence[ReusableIntentRouteMapping]:
        assert limit == 50
        return self.loaded

    def append(self, mappings: Sequence[ReusableIntentRouteMapping]) -> None:
        self.saved = tuple(mappings)


class FailingMappingStore:
    def load_recent(self, *, limit: int) -> Sequence[ReusableIntentRouteMapping]:
        del limit
        raise OSError("read failure")

    def append(self, mappings: Sequence[ReusableIntentRouteMapping]) -> None:
        del mappings
        raise OSError("write failure")


def _catalog() -> CapabilityCatalog:
    return build_capability_catalog(
        {
            "manual_version": "0.1.0",
            "tools": [
                {"name": "quote", "description": "查询行情。", "parameters": []},
                {"name": "news", "description": "检索新闻。", "parameters": []},
                {"name": "report", "description": "检索研报。", "parameters": []},
                {
                    "name": "pricePerformance",
                    "description": "查询证券价格表现。",
                    "parameters": [],
                },
            ],
        }
    )


def _candidate(*actual: str) -> dict[str, object]:
    return {
        "trace_id": "trace-1",
        "mode": "chat",
        "intent_fragments": ("分析行情和新闻", "查询行情", "检索相关新闻"),
        "actual_capability_ids": tuple(actual),
        "diagnostics": {
            "malformed_event_count": 0,
            "out_of_scope_tool_call_count": 0,
            "duplicate_call_definition_count": 0,
        },
        "normalization_error": None,
    }


def _judge_response(*required: str) -> dict[str, object]:
    return {
        "subtasks": [
            {
                "intent_signature": ["market_data", "news_review"],
                "subtask_type": "investment_research",
                "required_capability_ids": list(required),
            }
        ],
        "required_capability_ids": list(required),
    }


def test_one_judge_assessment_drives_precision_recall_and_reusable_table() -> None:
    judge = FakeJudge(_judge_response("quote", "news", "report"))
    store = FakeMappingStore()

    assessment = build_routing_assessment(
        _candidate("quote", "news"), _catalog(), judge, mapping_store=store
    )
    precision = RoutingPrecisionEvaluator().evaluate(assessment)
    recall = RoutingRecallEvaluator().evaluate(assessment)

    assert len(judge.requests) == 1
    assert judge.requests[0].reusable_mappings == store.loaded
    assert precision.metric_name == "routing_precision"
    assert precision.value == 1.0
    assert recall.metric_name == "routing_recall"
    assert recall.value == pytest.approx(2 / 3)
    assert precision.metadata["true_positive_count"] == 2
    assert precision.metadata["false_positive_count"] == 0
    assert recall.metadata["false_negative_count"] == 1
    assert store.saved[0].intent_signature == ("market_data", "news_review")
    assert "分析行情和新闻" not in repr(store.saved)


def test_zero_denominators_are_handled_independently() -> None:
    no_actual = build_routing_assessment(
        _candidate(), _catalog(), FakeJudge(_judge_response("quote"))
    )
    no_expected = build_routing_assessment(
        _candidate("quote"), _catalog(), FakeJudge(_judge_response())
    )

    precision = RoutingPrecisionEvaluator().evaluate(no_actual)
    recall = RoutingRecallEvaluator().evaluate(no_actual)
    assert precision.status is EvaluationStatus.NOT_COMPUTABLE
    assert precision.metadata["reason"] == "no_actual_routes"
    assert recall.status is EvaluationStatus.COMPUTED
    assert recall.value == 0.0

    precision = RoutingPrecisionEvaluator().evaluate(no_expected)
    recall = RoutingRecallEvaluator().evaluate(no_expected)
    assert precision.status is EvaluationStatus.COMPUTED
    assert precision.value == 0.0
    assert recall.status is EvaluationStatus.NOT_COMPUTABLE
    assert recall.metadata["reason"] == "no_expected_routes"


def test_capability_ids_preserve_schema_case() -> None:
    assessment = build_routing_assessment(
        _candidate("pricePerformance"),
        _catalog(),
        FakeJudge(_judge_response("pricePerformance")),
    )

    precision = RoutingPrecisionEvaluator().evaluate(assessment)
    recall = RoutingRecallEvaluator().evaluate(assessment)

    assert precision.value == 1.0
    assert recall.value == 1.0


@pytest.mark.parametrize(
    "response",
    [
        {"subtasks": [], "required_capability_ids": ["quote"]},
        {
            "subtasks": [
                {
                    "intent_signature": ["包含公司名称贵州茅台"],
                    "subtask_type": "quote_lookup",
                    "required_capability_ids": ["quote"],
                }
            ],
            "required_capability_ids": ["quote"],
        },
        {
            "subtasks": [
                {
                    "intent_signature": ["market_data"],
                    "subtask_type": "quote_lookup",
                    "required_capability_ids": ["unknown"],
                }
            ],
            "required_capability_ids": ["unknown"],
        },
        {
            "subtasks": [
                {
                    "intent_signature": ["market_data"],
                    "subtask_type": "quote_lookup",
                    "required_capability_ids": ["quote"],
                }
            ],
            "required_capability_ids": ["news"],
        },
    ],
)
def test_invalid_judge_output_is_not_computable(response: Mapping[str, object]) -> None:
    assessment = build_routing_assessment(_candidate("quote"), _catalog(), FakeJudge(response))

    result = RoutingPrecisionEvaluator().evaluate(assessment)

    assert result.status is EvaluationStatus.NOT_COMPUTABLE
    assert result.metadata["reason"] == "invalid_judge_output"


def test_judge_failure_does_not_echo_intent() -> None:
    assessment = build_routing_assessment(
        _candidate("quote"), _catalog(), FakeJudge(RuntimeError("secret query"))
    )

    result = RoutingRecallEvaluator().evaluate(assessment)

    assert result.status is EvaluationStatus.NOT_COMPUTABLE
    assert result.metadata["reason"] == "judge_failure"
    assert "secret query" not in repr(result.metadata)


def test_mapping_store_failure_does_not_change_metric_result() -> None:
    assessment = build_routing_assessment(
        _candidate("quote"),
        _catalog(),
        FakeJudge(_judge_response("quote")),
        mapping_store=FailingMappingStore(),
    )

    result = RoutingPrecisionEvaluator().evaluate(assessment)

    assert result.status is EvaluationStatus.COMPUTED
    assert result.value == 1.0
    assert result.metadata["mapping_store_read_failed"] is True
    assert result.metadata["mapping_store_write_failed"] is True
