"""计算单条日志的模型层错误数、错误分类、模型归因与模型调用分布(稳定性维度 7.4)。

稳定指标名 `llm_error_count`。宽表单元格为单元素 JSON 数组(2026-09-21 需求方
确认统一 [] 包围);1.3.0(2026-09-23 需求方确认)在既有三个键之后追加
`total_call_count` 与 `model_calls`,使报告可以直接算"每个模型的出错率",
并把 `error_models` 内的 provider 去掉(模型名与 provider 实勘一一对应,按
provider 分组只会把一个模型拆成多行):

    [{"llm_error_count":3,
      "error_classes":[{"class":"provider_timeout","count":2},
                       {"class":"context_overflow","count":1}],
      "error_models":[{"model":"pro","count":3}],
      "total_call_count":644,
      "model_calls":[{"model":"pro","count":640},{"model":"glm-5.3","count":4}]}]

`error_classes` 按 count 降序、class 升序稳定排序,`error_models` / `model_calls`
按 count 降序、model 升序稳定排序,均仅含非零项;零错误输出合法零值 JSON
(COMPUTED,不是不可计算)。聊天模式无模型层错误事件通道,返回
NOT_COMPUTABLE / no_model_error_events,宽表单元格留空。

模型归因边界:`llm.error` 事件不携带模型标识,collector 仅在回合恰有唯一模型时
把全部错误归因到该模型;多模型/无模型回合 `error_models` 为空数组,错误数进
`model_unattributed_error_count` 诊断。归因守恒:
Σ error_models.count + model_unattributed_error_count == llm_error_count。

模型调用边界:调用数按 collector 侧"唯一 assistant 消息"计数,
Σ model_calls.count + model_call_unattributed_count == total_call_count
(分母不因归因失败而缩小)。每个出现在 `error_models` 里的模型必须同时出现在
`model_calls` 里——两者取自同一路由通道,违反说明通道漂移,隔离不猜测。

评测器只做契约校验与序列化:计数、分类与归因由 collector 产出;分类标签必须
落在版本化目录白名单内,分类计数之和必须等于主值,违约隔离为
ERROR / invalid_normalized_trace。
"""

import json
from collections.abc import Mapping, Sequence

from collectors.llm_error_count import (
    LLM_ERROR_CLASS_CATALOG_VERSION,
    LLM_ERROR_CLASSES,
    LlmErrorModelCount,
    LlmModelCallCount,
)
from evaluators.base import BaseEvaluator, EvaluationResult, EvaluationStatus

_NOT_COMPUTABLE_REASONS = frozenset({"no_model_error_events"})
_ERROR_REASONS = frozenset({"invalid_chat_trace_structure", "invalid_research_trace_structure"})
_DIAGNOSTIC_KEYS = (
    "duplicate_error_event_count",
    "message_error_projection_count",
    "finish_reason_error_count",
    "loop_missing_count",
    "unclassified_count",
    "model_route_count",
    "model_unattributed_error_count",
    "model_call_unattributed_count",
    "missing_message_id_count",
    "conflicting_model_route_count",
    "non_assistant_routed_message_count",
)
_SEMANTICS_BY_MODE = {
    "chat": "no_model_error_channel",
    "research": "llm_error_event",
}


class LlmErrorCountEvaluator(BaseEvaluator):
    """校验规范化错误证据并输出单字段紧凑 JSON;缺通道不可计算,不猜测。"""

    @property
    def metric_name(self) -> str:
        return "llm_error_count"

    @property
    def evaluator_version(self) -> str:
        return "1.3.0"

    def evaluate(self, trace: Mapping[str, object]) -> EvaluationResult:
        parsed = _parse_trace(trace)
        if parsed is None:
            return self._result(
                None, EvaluationStatus.ERROR, {"reason": "invalid_normalized_trace"}
            )
        (
            trace_id,
            mode,
            count,
            class_counts,
            model_counts,
            total_call_count,
            model_call_counts,
            diagnostics,
            error,
        ) = parsed
        metadata: dict[str, object] = {
            "trace_id": trace_id,
            "mode": mode,
            # _parse_trace 已保证 mode ∈ _SEMANTICS_BY_MODE。
            "error_event_semantics": _SEMANTICS_BY_MODE[mode],
            "llm_error_class_version": LLM_ERROR_CLASS_CATALOG_VERSION,
            "model_attribution": "single_route_turn" if model_counts else "unattributed",
            **{key: diagnostics[key] for key in _DIAGNOSTIC_KEYS},
        }
        if error is not None:
            metadata["reason"] = error
            if error in _NOT_COMPUTABLE_REASONS:
                return self._result(None, EvaluationStatus.NOT_COMPUTABLE, metadata)
            if error in _ERROR_REASONS:
                return self._result(None, EvaluationStatus.ERROR, metadata)
            return self._result(
                None, EvaluationStatus.ERROR, {"reason": "invalid_normalized_trace"}
            )
        # error 为 None 时 _parse_trace 已保证计数非负、分类守恒、归因守恒、调用守恒。
        assert count is not None and class_counts is not None  # noqa: S101 - 规范化契约保证
        assert model_counts is not None  # noqa: S101 - 规范化契约保证
        assert total_call_count is not None and model_call_counts is not None  # noqa: S101
        classes = sorted(class_counts.items(), key=lambda item: (-item[1], item[0]))
        models = sorted(model_counts, key=lambda item: (-item["count"], item["model"]))
        calls = sorted(model_call_counts, key=lambda item: (-item["count"], item["model"]))
        payload = {
            "llm_error_count": count,
            "error_classes": [{"class": name, "count": n} for name, n in classes],
            "error_models": [{"model": item["model"], "count": item["count"]} for item in models],
            "total_call_count": total_call_count,
            "model_calls": [{"model": item["model"], "count": item["count"]} for item in calls],
        }
        # 单元格统一 [] 包围:单元素数组承载固定键序对象(2026-09-21 需求方确认)。
        value = json.dumps([payload], ensure_ascii=False, separators=(",", ":"))
        return self._result(value, EvaluationStatus.COMPUTED, metadata)

    def _result(
        self, value: str | None, status: EvaluationStatus, metadata: Mapping[str, object]
    ) -> EvaluationResult:
        return EvaluationResult(self.metric_name, self.evaluator_version, value, status, metadata)


_ParsedTrace = tuple[
    str | None,
    str,
    int | None,
    Mapping[str, int] | None,
    Sequence[LlmErrorModelCount] | None,
    int | None,
    Sequence[LlmModelCallCount] | None,
    Mapping[str, object],
    str | None,
]


def _parse_trace(trace: Mapping[str, object]) -> _ParsedTrace | None:
    if not isinstance(trace, Mapping):
        return None
    mode = _text(trace.get("mode"))
    if mode not in _SEMANTICS_BY_MODE:
        return None
    trace_id_value = trace.get("trace_id")
    trace_id = _text(trace_id_value)
    if trace_id_value is not None and trace_id is None:
        return None

    count_value = trace.get("llm_error_count")
    count: int | None
    if count_value is None:
        count = None
    elif isinstance(count_value, int) and not isinstance(count_value, bool) and count_value >= 0:
        count = count_value
    else:
        return None

    raw_classes = trace.get("error_class_counts")
    class_counts: Mapping[str, int] | None
    if raw_classes is None:
        class_counts = None
    elif isinstance(raw_classes, Mapping):
        validated: dict[str, int] = {}
        for key, value in raw_classes.items():
            name = _text(key)
            # 标签必须落在版本化目录白名单内;计数必须为正整数(仅非零类进入映射)。
            if (
                name is None
                or name not in LLM_ERROR_CLASSES
                or isinstance(value, bool)
                or not isinstance(value, int)
                or value <= 0
            ):
                return None
            validated[name] = value
        class_counts = validated
        # 守恒校验:分类计数之和必须等于主值,违反说明 collector 契约被破坏。
        if count is not None and sum(validated.values()) != count:
            return None
    else:
        return None

    raw_models = trace.get("error_model_counts")
    model_counts: Sequence[LlmErrorModelCount] | None
    # 跨段校验用:能归因到错误的模型名集合(在下方调用分项校验里与调用模型取包含关系)。
    attributed_model_names: set[str] = set()
    if raw_models is None:
        model_counts = None
    elif isinstance(raw_models, Sequence) and not isinstance(raw_models, (str, bytes, bytearray)):
        validated_models: list[LlmErrorModelCount] = []
        seen: set[str] = set()
        for item in raw_models:
            if not isinstance(item, Mapping):
                return None
            model = _text(item.get("model"))
            item_count = item.get("count")
            # 1.3.0 起仅按 model 归因(provider 已从契约移除)。
            if model is None or model in seen:
                return None
            if isinstance(item_count, bool) or not isinstance(item_count, int) or item_count <= 0:
                return None
            seen.add(model)
            validated_models.append({"model": model, "count": item_count})
        model_counts = validated_models
        attributed_model_names = seen
        # 归因守恒:已归因计数 + 未归因诊断 == 主值;已归因时必须是单模型全量归因。
        if count is not None:
            diagnostics = trace.get("diagnostics")
            unattributed = (
                diagnostics.get("model_unattributed_error_count")
                if isinstance(diagnostics, Mapping)
                else None
            )
            attributed = sum(item["count"] for item in validated_models)
            if not isinstance(unattributed, int) or attributed + unattributed != count:
                return None
            if validated_models and (len(validated_models) != 1 or attributed != count):
                return None
    else:
        return None

    total_value = trace.get("total_model_call_count")
    total_call_count: int | None
    if total_value is None:
        total_call_count = None
    elif isinstance(total_value, int) and not isinstance(total_value, bool) and total_value >= 0:
        total_call_count = total_value
    else:
        return None

    raw_calls = trace.get("model_call_counts")
    model_call_counts: Sequence[LlmModelCallCount] | None
    if raw_calls is None:
        model_call_counts = None
    elif isinstance(raw_calls, Sequence) and not isinstance(raw_calls, (str, bytes, bytearray)):
        validated_calls: list[LlmModelCallCount] = []
        seen_call_models: set[str] = set()
        for item in raw_calls:
            if not isinstance(item, Mapping):
                return None
            model = _text(item.get("model"))
            item_count = item.get("count")
            if model is None or model in seen_call_models:
                return None
            if isinstance(item_count, bool) or not isinstance(item_count, int) or item_count <= 0:
                return None
            seen_call_models.add(model)
            validated_calls.append({"model": model, "count": item_count})
        model_call_counts = validated_calls
        # 调用守恒:已归因调用 + 未归因诊断 == 总调用数;分母不因归因失败而缩小,
        # 因此分项之和允许小于总数,但不允许超过。
        if total_call_count is not None:
            diagnostics = trace.get("diagnostics")
            call_unattributed = (
                diagnostics.get("model_call_unattributed_count")
                if isinstance(diagnostics, Mapping)
                else None
            )
            call_attributed = sum(item["count"] for item in validated_calls)
            if (
                not isinstance(call_unattributed, int)
                or call_unattributed < 0
                or call_attributed + call_unattributed != total_call_count
            ):
                return None
            # 交叉校验:能归因到错误的模型必然被调用过(两者同源),否则通道漂移。
            if not seen_call_models >= attributed_model_names:
                return None
    else:
        return None

    diagnostics = trace.get("diagnostics")
    if not isinstance(diagnostics, Mapping):
        return None
    for key in _DIAGNOSTIC_KEYS:
        value = diagnostics.get(key)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            return None

    error_value = trace.get("normalization_error")
    error = _text(error_value)
    if error_value is not None and error is None:
        return None
    if (error is None) != (
        count is not None
        and class_counts is not None
        and model_counts is not None
        and total_call_count is not None
        and model_call_counts is not None
    ):
        # error 与计数/分类/归因/调用五者互斥:有 error 不得携带,无 error 必须齐备。
        return None
    return (
        trace_id,
        mode,
        count,
        class_counts,
        model_counts,
        total_call_count,
        model_call_counts,
        diagnostics,
        error,
    )


def _text(value: object) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None
