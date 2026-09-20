"""输出单条 Trace 内发生重复调用的逐工具计数。"""

import json
from collections.abc import Mapping, Sequence
from typing import TypedDict, cast

from evaluators.base import BaseEvaluator, EvaluationResult, EvaluationStatus
from evaluators.tool_calls_count import ToolCallsCountEvaluator


class _ToolRepeatCount(TypedDict):
    """宽表 JSON 单元中的单工具重复调用结果。"""

    tool_name: str
    call_count: int
    repeat_call_count: int


class PerToolRepeatCallCountEvaluator(BaseEvaluator):
    """复用唯一调用统计，仅输出首次调用之后的额外调用次数。"""

    @property
    def metric_name(self) -> str:
        return "per_tool_repeat_call_count"

    @property
    def evaluator_version(self) -> str:
        return "1.0.0"

    def evaluate(self, trace: Mapping[str, object]) -> EvaluationResult:
        # 两个指标消费同一份规范化数据，先复用首指标的严格结构校验，避免口径漂移。
        validated = ToolCallsCountEvaluator().evaluate(trace)
        metadata = dict(validated.metadata)
        if validated.status is not EvaluationStatus.COMPUTED:
            return EvaluationResult(
                self.metric_name,
                self.evaluator_version,
                None,
                validated.status,
                metadata,
            )

        raw_statistics = validated.metadata.get("per_tool_statistics")
        if not isinstance(raw_statistics, Sequence):
            return self._invalid_result(metadata)

        repeated: list[_ToolRepeatCount] = []
        for raw_item in raw_statistics:
            if not isinstance(raw_item, Mapping):
                return self._invalid_result(metadata)
            repeated_count = raw_item.get("repeat_call_count")
            if isinstance(repeated_count, int) and repeated_count > 0:
                repeated.append(
                    {
                        "tool_name": cast(str, raw_item["tool_name"]),
                        "call_count": cast(int, raw_item["call_count"]),
                        "repeat_call_count": repeated_count,
                    }
                )

        repeated.sort(
            key=lambda item: (
                -item["repeat_call_count"],
                -item["call_count"],
                item["tool_name"],
            )
        )
        metadata["repeated_tool_name_count"] = len(repeated)
        metadata["total_repeat_call_count"] = sum(item["repeat_call_count"] for item in repeated)
        value = json.dumps(repeated, ensure_ascii=False, separators=(",", ":"))
        return EvaluationResult(
            self.metric_name,
            self.evaluator_version,
            value,
            EvaluationStatus.COMPUTED,
            metadata,
        )

    def _invalid_result(self, metadata: dict[str, object]) -> EvaluationResult:
        metadata["reason"] = "invalid_normalized_trace"
        return EvaluationResult(
            self.metric_name,
            self.evaluator_version,
            None,
            EvaluationStatus.ERROR,
            metadata,
        )
