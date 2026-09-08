"""使用一次结构化 Judge 判定同时计算路由 Precision 与 Recall。"""

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol, TypedDict, TypeGuard, cast

from collectors.routing import CapabilityCatalog, CapabilityDefinition
from evaluators.base import BaseEvaluator, EvaluationResult, EvaluationStatus
from evaluators.openai_compatible_judge_runtime import judge_error_metadata


@dataclass(frozen=True, slots=True)
class ReusableIntentRouteMapping:
    """可持久化的去实体化意图签名与能力映射。"""

    intent_signature: tuple[str, ...]
    subtask_type: str
    capability_ids: tuple[str, ...]
    schema_version: str
    judge_name: str
    judge_version: str
    prompt_version: str


@dataclass(frozen=True, slots=True)
class RoutingJudgeRequest:
    """路由 Judge 的内存请求；意图原文只允许存在于本次调用生命周期内。"""

    trace_id: str | None
    mode: str
    intent_fragments: tuple[str, ...]
    capabilities: tuple[CapabilityDefinition, ...]
    reusable_mappings: tuple[ReusableIntentRouteMapping, ...] = ()
    validation_feedback: str | None = None


class RoutingJudge(Protocol):
    judge_name: str
    judge_version: str
    prompt_version: str

    def judge(self, request: RoutingJudgeRequest) -> Mapping[str, object]: ...


class IntentRouteMappingStore(Protocol):
    def load_recent(self, *, limit: int) -> Sequence[ReusableIntentRouteMapping]: ...

    def append(self, mappings: Sequence[ReusableIntentRouteMapping]) -> None: ...


class RoutingAssessment(TypedDict):
    trace_id: str | None
    mode: str
    schema_version: str
    judge_name: str
    judge_version: str
    prompt_version: str
    actual_route_count: int
    expected_route_count: int
    true_positive_count: int
    false_positive_count: int
    false_negative_count: int
    assumed_matching_mcp_route_count: int
    malformed_event_count: int
    out_of_scope_tool_call_count: int
    unknown_mcp_tool_call_count: int
    duplicate_call_definition_count: int
    judge_validation_retry_count: int
    judge_output_error: str | None
    mapping_store_read_failed: bool
    mapping_store_write_failed: bool
    normalization_error: str | None


_SAFE_TOKEN = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


def build_routing_assessment(
    candidate: Mapping[str, object],
    catalog: CapabilityCatalog,
    judge: RoutingJudge,
    *,
    mapping_store: IntentRouteMappingStore | None = None,
) -> RoutingAssessment:
    """调用一次 Judge，校验标准能力集合并生成两个指标共享的安全计数。"""

    parsed = _parse_candidate(candidate)
    if parsed is None:
        return _empty_assessment(catalog, judge, error="invalid_normalized_trace")
    (
        trace_id,
        mode,
        intents,
        actual_ids,
        assumed_matching_count,
        diagnostics,
        normalization_error,
    ) = parsed
    if not actual_ids.issubset(catalog.capabilities):
        return _empty_assessment(
            catalog,
            judge,
            trace_id=trace_id,
            mode=mode,
            diagnostics=diagnostics,
            error="invalid_normalized_trace",
        )
    base = _empty_assessment(
        catalog,
        judge,
        trace_id=trace_id,
        mode=mode,
        actual_count=len(actual_ids) + assumed_matching_count,
        assumed_matching_count=assumed_matching_count,
        diagnostics=diagnostics,
        error=normalization_error,
    )
    if normalization_error is not None:
        return base

    reusable: tuple[ReusableIntentRouteMapping, ...] = ()
    if mapping_store is not None:
        try:
            reusable = tuple(mapping_store.load_recent(limit=50))
        except Exception:
            base["mapping_store_read_failed"] = True

    request = RoutingJudgeRequest(
        trace_id=trace_id,
        mode=mode,
        intent_fragments=intents,
        capabilities=tuple(catalog.capabilities.values()),
        reusable_mappings=reusable,
    )
    try:
        raw_response = judge.judge(request)
    except Exception as judge_error:
        reason, _ = judge_error_metadata(judge_error)
        base["normalization_error"] = reason
        return base

    parsed_response, output_error = _parse_judge_response(raw_response, catalog, judge)
    if parsed_response is None:
        base["judge_validation_retry_count"] = 1
        retry_request = RoutingJudgeRequest(
            trace_id=trace_id,
            mode=mode,
            intent_fragments=intents,
            capabilities=tuple(catalog.capabilities.values()),
            reusable_mappings=reusable,
            validation_feedback=output_error,
        )
        try:
            retry_response = judge.judge(retry_request)
        except Exception as judge_error:
            reason, _ = judge_error_metadata(judge_error)
            base["normalization_error"] = reason
            return base
        parsed_response, output_error = _parse_judge_response(retry_response, catalog, judge)
        if parsed_response is None:
            if output_error in {
                "invalid_intent_signature",
                "invalid_subtask_type",
                "duplicate_intent_signature_token",
            }:
                parsed_response, _ = _parse_judge_response(
                    retry_response,
                    catalog,
                    judge,
                    allow_invalid_mapping_metadata=True,
                )
            if parsed_response is None:
                base["normalization_error"] = "invalid_judge_output"
                base["judge_output_error"] = output_error
                return base
            base["judge_output_error"] = output_error
    expected_ids, mappings = parsed_response
    true_positive_count = len(actual_ids & expected_ids) + assumed_matching_count
    base["expected_route_count"] = len(expected_ids) + assumed_matching_count
    base["true_positive_count"] = true_positive_count
    base["false_positive_count"] = len(actual_ids - expected_ids)
    base["false_negative_count"] = len(expected_ids - actual_ids)
    if mapping_store is not None and mappings:
        try:
            mapping_store.append(mappings)
        except Exception:
            base["mapping_store_write_failed"] = True
    return base


class RoutingPrecisionEvaluator(BaseEvaluator):
    @property
    def metric_name(self) -> str:
        return "routing_precision"

    @property
    def evaluator_version(self) -> str:
        return "1.3.0"

    def evaluate(self, trace: Mapping[str, object]) -> EvaluationResult:
        parsed = _parse_assessment(trace)
        if parsed is None:
            return self._result(None, EvaluationStatus.ERROR, {"reason": "invalid_assessment"})
        metadata, error = parsed
        if error is not None:
            metadata["reason"] = error
            return self._result(None, EvaluationStatus.NOT_COMPUTABLE, metadata)
        denominator = cast(int, metadata["actual_route_count"])
        if denominator == 0:
            metadata["reason"] = "no_actual_routes"
            return self._result(None, EvaluationStatus.NOT_COMPUTABLE, metadata)
        return self._result(
            cast(int, metadata["true_positive_count"]) / denominator,
            EvaluationStatus.COMPUTED,
            metadata,
        )

    def _result(
        self, value: float | None, status: EvaluationStatus, metadata: Mapping[str, object]
    ) -> EvaluationResult:
        return EvaluationResult(self.metric_name, self.evaluator_version, value, status, metadata)


class RoutingRecallEvaluator(BaseEvaluator):
    @property
    def metric_name(self) -> str:
        return "routing_recall"

    @property
    def evaluator_version(self) -> str:
        return "1.3.0"

    def evaluate(self, trace: Mapping[str, object]) -> EvaluationResult:
        parsed = _parse_assessment(trace)
        if parsed is None:
            return self._result(None, EvaluationStatus.ERROR, {"reason": "invalid_assessment"})
        metadata, error = parsed
        if error is not None:
            metadata["reason"] = error
            return self._result(None, EvaluationStatus.NOT_COMPUTABLE, metadata)
        denominator = cast(int, metadata["expected_route_count"])
        if denominator == 0:
            metadata["reason"] = "no_expected_routes"
            return self._result(None, EvaluationStatus.NOT_COMPUTABLE, metadata)
        return self._result(
            cast(int, metadata["true_positive_count"]) / denominator,
            EvaluationStatus.COMPUTED,
            metadata,
        )

    def _result(
        self, value: float | None, status: EvaluationStatus, metadata: Mapping[str, object]
    ) -> EvaluationResult:
        return EvaluationResult(self.metric_name, self.evaluator_version, value, status, metadata)


def _parse_judge_response(
    response: Mapping[str, object],
    catalog: CapabilityCatalog,
    judge: RoutingJudge,
    *,
    allow_invalid_mapping_metadata: bool = False,
) -> tuple[
    tuple[set[str], tuple[ReusableIntentRouteMapping, ...]] | None,
    str | None,
]:
    raw_subtasks = response.get("subtasks")
    if not _is_sequence(raw_subtasks):
        return None, "invalid_routing_structure"

    mappings: list[ReusableIntentRouteMapping] = []
    expected_ids: set[str] = set()
    for raw_subtask in raw_subtasks:
        if not isinstance(raw_subtask, Mapping):
            return None, "invalid_routing_structure"
        raw_signature = raw_subtask.get("intent_signature")
        raw_ids = raw_subtask.get("required_capability_ids")
        if not _is_sequence(raw_signature) or not _is_sequence(raw_ids):
            return None, "invalid_routing_structure"
        capability_ids = _safe_capability_ids(raw_ids, catalog)
        if capability_ids is None:
            return None, "unknown_capability_id"
        expected_ids.update(capability_ids)

        subtask_type = _safe_token(raw_subtask.get("subtask_type"))
        signature = tuple(
            token for item in raw_signature if (token := _safe_token(item)) is not None
        )
        mapping_error: str | None = None
        if subtask_type is None:
            mapping_error = "invalid_subtask_type"
        elif len(signature) != len(raw_signature) or not signature:
            mapping_error = "invalid_intent_signature"
        elif len(set(signature)) != len(signature):
            mapping_error = "duplicate_intent_signature_token"
        if mapping_error is not None:
            if allow_invalid_mapping_metadata:
                continue
            return None, mapping_error
        assert subtask_type is not None
        mappings.append(
            ReusableIntentRouteMapping(
                intent_signature=tuple(sorted(signature)),
                subtask_type=subtask_type,
                capability_ids=tuple(sorted(capability_ids)),
                schema_version=catalog.schema_version,
                judge_name=judge.judge_name,
                judge_version=judge.judge_version,
                prompt_version=judge.prompt_version,
            )
        )
    return (expected_ids, tuple(mappings)), None


def _safe_capability_ids(values: Sequence[object], catalog: CapabilityCatalog) -> set[str] | None:
    result: set[str] = set()
    for value in values:
        capability_id = value.strip() if isinstance(value, str) and value.strip() else None
        if capability_id is None or capability_id not in catalog.capabilities:
            return None
        result.add(capability_id)
    return result


def _parse_candidate(
    candidate: Mapping[str, object],
) -> (
    tuple[
        str | None,
        str,
        tuple[str, ...],
        set[str],
        int,
        dict[str, int],
        str | None,
    ]
    | None
):
    trace_id = candidate.get("trace_id")
    mode = candidate.get("mode")
    raw_intents = candidate.get("intent_fragments")
    raw_actual = candidate.get("actual_capability_ids")
    assumed_matching_count = _non_negative_int(candidate.get("assumed_matching_mcp_route_count", 0))
    raw_diagnostics = candidate.get("diagnostics")
    error = candidate.get("normalization_error")
    if (
        (trace_id is not None and not isinstance(trace_id, str))
        or not isinstance(mode, str)
        or mode not in {"chat", "research"}
        or not _is_sequence(raw_intents)
        or not _is_sequence(raw_actual)
        or assumed_matching_count is None
        or not isinstance(raw_diagnostics, Mapping)
        or (error is not None and (not isinstance(error, str) or not error.strip()))
    ):
        return None
    intents = tuple(item.strip() for item in raw_intents if isinstance(item, str) and item.strip())
    actual: set[str] = set()
    for item in raw_actual:
        capability_id = item.strip() if isinstance(item, str) and item.strip() else None
        if capability_id is None:
            return None
        actual.add(capability_id)
    if len(intents) != len(raw_intents):
        return None
    diagnostics = {
        key: _non_negative_int(raw_diagnostics.get(key))
        for key in (
            "malformed_event_count",
            "out_of_scope_tool_call_count",
            "unknown_mcp_tool_call_count",
            "duplicate_call_definition_count",
        )
    }
    if raw_diagnostics.get("unknown_mcp_tool_call_count") is None:
        diagnostics["unknown_mcp_tool_call_count"] = 0
    if any(value is None for value in diagnostics.values()):
        return None
    if assumed_matching_count > cast(int, diagnostics["unknown_mcp_tool_call_count"]):
        return None
    return (
        trace_id.strip() if isinstance(trace_id, str) else None,
        mode,
        intents,
        actual,
        assumed_matching_count,
        cast(dict[str, int], diagnostics),
        error.strip() if isinstance(error, str) else None,
    )


def _parse_assessment(
    trace: Mapping[str, object],
) -> tuple[dict[str, object], str | None] | None:
    count_names = (
        "actual_route_count",
        "expected_route_count",
        "true_positive_count",
        "false_positive_count",
        "false_negative_count",
        "assumed_matching_mcp_route_count",
        "malformed_event_count",
        "out_of_scope_tool_call_count",
        "unknown_mcp_tool_call_count",
        "duplicate_call_definition_count",
        "judge_validation_retry_count",
    )
    counts = {name: _non_negative_int(trace.get(name)) for name in count_names}
    error = trace.get("normalization_error")
    if any(value is None for value in counts.values()) or (
        error is not None and (not isinstance(error, str) or not error.strip())
    ):
        return None
    actual = cast(int, counts["actual_route_count"])
    expected = cast(int, counts["expected_route_count"])
    true_positive = cast(int, counts["true_positive_count"])
    if (
        true_positive > actual
        or true_positive > expected
        or counts["false_positive_count"] != actual - true_positive
        or counts["false_negative_count"] != expected - true_positive
    ):
        return None
    metadata = {key: value for key, value in trace.items() if key != "normalization_error"}
    return metadata, error.strip() if isinstance(error, str) else None


def _empty_assessment(
    catalog: CapabilityCatalog,
    judge: RoutingJudge,
    *,
    trace_id: str | None = None,
    mode: str = "unknown",
    actual_count: int = 0,
    assumed_matching_count: int = 0,
    diagnostics: Mapping[str, int] | None = None,
    error: str | None,
) -> RoutingAssessment:
    safe_diagnostics = diagnostics or {}
    return {
        "trace_id": trace_id,
        "mode": mode,
        "schema_version": catalog.schema_version,
        "judge_name": judge.judge_name,
        "judge_version": judge.judge_version,
        "prompt_version": judge.prompt_version,
        "actual_route_count": actual_count,
        "expected_route_count": 0,
        "true_positive_count": 0,
        "false_positive_count": actual_count,
        "false_negative_count": 0,
        "assumed_matching_mcp_route_count": assumed_matching_count,
        "malformed_event_count": safe_diagnostics.get("malformed_event_count", 0),
        "out_of_scope_tool_call_count": safe_diagnostics.get("out_of_scope_tool_call_count", 0),
        "unknown_mcp_tool_call_count": safe_diagnostics.get("unknown_mcp_tool_call_count", 0),
        "duplicate_call_definition_count": safe_diagnostics.get(
            "duplicate_call_definition_count", 0
        ),
        "judge_validation_retry_count": 0,
        "judge_output_error": None,
        "mapping_store_read_failed": False,
        "mapping_store_write_failed": False,
        "normalization_error": error,
    }


def _safe_token(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized if _SAFE_TOKEN.fullmatch(normalized) else None


def _non_negative_int(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _is_sequence(value: object) -> TypeGuard[Sequence[object]]:
    return isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray))
