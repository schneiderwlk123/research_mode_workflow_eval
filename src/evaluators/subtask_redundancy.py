"""计算规则识别出的高置信度子任务冗余率。"""

from collections.abc import Mapping
from typing import cast

from collectors.subtask_redundancy import (
    ALGORITHM_VERSION,
    MAX_NOVELTY_RATIO,
    SIMILARITY_THRESHOLD,
)
from evaluators.base import BaseEvaluator, EvaluationResult, EvaluationStatus


class SubtaskRedundancyEvaluator(BaseEvaluator):
    """使用安全聚合计数计算子任务冗余率，不接触或持久化任务正文。"""

    @property
    def metric_name(self) -> str:
        return "subtask_redundancy_rate"

    @property
    def evaluator_version(self) -> str:
        return "1.1.0"

    def evaluate(self, trace: Mapping[str, object]) -> EvaluationResult:
        parsed = _parse_trace(trace)
        if parsed is None:
            return self._result(
                None,
                EvaluationStatus.ERROR,
                {"reason": "invalid_normalized_trace"},
            )
        metadata, error = parsed
        if error is not None:
            metadata["reason"] = error
            return self._result(None, EvaluationStatus.NOT_COMPUTABLE, metadata)
        denominator = cast(int, metadata["total_subtask_count"])
        if denominator == 0:
            metadata["reason"] = "no_subtasks"
            return self._result(None, EvaluationStatus.NOT_COMPUTABLE, metadata)
        return self._result(
            cast(int, metadata["redundant_subtask_count"]) / denominator,
            EvaluationStatus.COMPUTED,
            metadata,
        )

    def _result(
        self,
        value: float | None,
        status: EvaluationStatus,
        metadata: Mapping[str, object],
    ) -> EvaluationResult:
        return EvaluationResult(self.metric_name, self.evaluator_version, value, status, metadata)


def _parse_trace(
    trace: Mapping[str, object],
) -> tuple[dict[str, object], str | None] | None:
    mode = trace.get("mode")
    trace_id = trace.get("trace_id")
    total = _non_negative_int(trace.get("total_subtask_count"))
    redundant = _non_negative_int(trace.get("redundant_subtask_count"))
    diagnostics = trace.get("diagnostics")
    error = trace.get("normalization_error")
    if (
        not isinstance(mode, str)
        or mode not in {"chat", "research"}
        or (trace_id is not None and not isinstance(trace_id, str))
        or total is None
        or redundant is None
        or not isinstance(diagnostics, Mapping)
        or trace.get("algorithm_version") != ALGORITHM_VERSION
        or trace.get("similarity_threshold") != SIMILARITY_THRESHOLD
        or trace.get("max_novelty_ratio") != MAX_NOVELTY_RATIO
        or (error is not None and (not isinstance(error, str) or not error.strip()))
    ):
        return None
    exact = _non_negative_int(diagnostics.get("exact_duplicate_subtask_count"))
    similar = _non_negative_int(diagnostics.get("similar_redundant_subtask_count"))
    malformed = _non_negative_int(diagnostics.get("malformed_event_count"))
    if (
        exact is None
        or similar is None
        or malformed is None
        or redundant > total
        or exact + similar != redundant
    ):
        return None
    metadata: dict[str, object] = {
        "trace_id": trace_id,
        "mode": mode,
        "total_subtask_count": total,
        "redundant_subtask_count": redundant,
        "exact_duplicate_subtask_count": exact,
        "similar_redundant_subtask_count": similar,
        "malformed_event_count": malformed,
        "algorithm_version": ALGORITHM_VERSION,
        "similarity_threshold": SIMILARITY_THRESHOLD,
        "max_novelty_ratio": MAX_NOVELTY_RATIO,
    }
    return metadata, error.strip() if isinstance(error, str) else None


def _non_negative_int(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value
