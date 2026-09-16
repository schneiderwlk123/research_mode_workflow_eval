"""把已注册指标汇总为一行一个 Trace 的 CSV 宽表。"""

import argparse
import hashlib
import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, TypeGuard

from collectors.cache_input_token_ratio import (
    normalize_chat_cache_input_token_ratio,
    normalize_research_cache_input_token_ratio,
)
from collectors.end_to_end_latency import (
    normalize_chat_end_to_end_latency,
    normalize_research_end_to_end_latency,
)
from collectors.max_tool_latency_share import (
    normalize_chat_max_tool_latency_share,
    normalize_research_max_tool_latency_share,
)
from collectors.output_token_ratio import (
    normalize_chat_output_token_ratio,
    normalize_research_output_token_ratio,
)
from collectors.token_consumption import (
    normalize_chat_token_consumption,
    normalize_research_token_consumption,
)
from collectors.tool_call_latency import (
    normalize_chat_tool_call_latency,
    normalize_research_tool_call_latency,
)
from collectors.tool_call_statistics import (
    normalize_chat_tool_call_statistics,
    normalize_research_tool_call_statistics,
)
from collectors.tool_step_tokens import (
    normalize_chat_tool_step_tokens,
    normalize_research_tool_step_tokens,
)
from collectors.tool_token_share import (
    normalize_chat_tool_token_share,
    normalize_research_tool_token_share,
)
from evaluators.base import EvaluationResult, EvaluationStatus
from evaluators.cache_input_token_ratio import CacheInputTokenRatioEvaluator
from evaluators.end_to_end_latency import EndToEndLatencyEvaluator
from evaluators.max_tool_latency_share import MaxToolLatencyShareEvaluator
from evaluators.max_tool_token_share import MaxToolTokenShareEvaluator
from evaluators.output_token_ratio import OutputTokenRatioEvaluator
from evaluators.per_tool_call_latency import PerToolCallLatencyEvaluator
from evaluators.per_tool_call_tokens import PerToolCallTokensEvaluator
from evaluators.per_tool_repeat_call_count import PerToolRepeatCallCountEvaluator
from evaluators.token_consumption import TotalTokenConsumptionEvaluator
from evaluators.tool_calls_count import ToolCallsCountEvaluator
from reporters.metric_table import (
    MetricColumn,
    TraceMetricRecord,
    build_metric_table,
    write_metric_table_csv,
)


def _evaluate_tool_calls_count(record: TraceMetricRecord) -> EvaluationResult:
    evaluator = ToolCallsCountEvaluator()
    if record.mode == "chat" and isinstance(record.payload, Mapping):
        return evaluator.evaluate(normalize_chat_tool_call_statistics(record.payload))
    if record.mode == "research" and _is_sequence(record.payload):
        return evaluator.evaluate(
            normalize_research_tool_call_statistics(
                record.payload,
                session_id=record.session_id or record.log_id,
            )
        )
    return EvaluationResult(
        evaluator.metric_name,
        evaluator.evaluator_version,
        None,
        EvaluationStatus.ERROR,
        {"reason": "invalid_metric_input"},
    )


def _evaluate_per_tool_repeat_call_count(record: TraceMetricRecord) -> EvaluationResult:
    evaluator = PerToolRepeatCallCountEvaluator()
    if record.mode == "chat" and isinstance(record.payload, Mapping):
        return evaluator.evaluate(normalize_chat_tool_call_statistics(record.payload))
    if record.mode == "research" and _is_sequence(record.payload):
        return evaluator.evaluate(
            normalize_research_tool_call_statistics(
                record.payload,
                session_id=record.session_id or record.log_id,
            )
        )
    return EvaluationResult(
        evaluator.metric_name,
        evaluator.evaluator_version,
        None,
        EvaluationStatus.ERROR,
        {"reason": "invalid_metric_input"},
    )


def _evaluate_per_tool_call_tokens(record: TraceMetricRecord) -> EvaluationResult:
    evaluator = PerToolCallTokensEvaluator()
    if record.mode == "chat" and isinstance(record.payload, Mapping):
        return evaluator.evaluate(normalize_chat_tool_step_tokens(record.payload))
    if record.mode == "research" and _is_sequence(record.payload):
        return evaluator.evaluate(normalize_research_tool_step_tokens(record.payload))
    return EvaluationResult(
        evaluator.metric_name,
        evaluator.evaluator_version,
        None,
        EvaluationStatus.ERROR,
        {"reason": "invalid_metric_input"},
    )


def _evaluate_total_token_consumption(record: TraceMetricRecord) -> EvaluationResult:
    evaluator = TotalTokenConsumptionEvaluator()
    if record.mode == "chat" and isinstance(record.payload, Mapping):
        return evaluator.evaluate(normalize_chat_token_consumption(record.payload))
    if record.mode == "research" and _is_sequence(record.payload):
        return evaluator.evaluate(normalize_research_token_consumption(record.payload))
    return EvaluationResult(
        evaluator.metric_name,
        evaluator.evaluator_version,
        None,
        EvaluationStatus.ERROR,
        {"reason": "invalid_metric_input"},
    )


def _evaluate_max_tool_token_share(record: TraceMetricRecord) -> EvaluationResult:
    evaluator = MaxToolTokenShareEvaluator()
    if record.mode == "chat" and isinstance(record.payload, Mapping):
        return evaluator.evaluate(normalize_chat_tool_token_share(record.payload))
    if record.mode == "research" and _is_sequence(record.payload):
        return evaluator.evaluate(normalize_research_tool_token_share(record.payload))
    return EvaluationResult(
        evaluator.metric_name,
        evaluator.evaluator_version,
        None,
        EvaluationStatus.ERROR,
        {"reason": "invalid_metric_input"},
    )


def _evaluate_output_token_ratio(record: TraceMetricRecord) -> EvaluationResult:
    evaluator = OutputTokenRatioEvaluator()
    if record.mode == "chat" and isinstance(record.payload, Mapping):
        return evaluator.evaluate(normalize_chat_output_token_ratio(record.payload))
    if record.mode == "research" and _is_sequence(record.payload):
        return evaluator.evaluate(normalize_research_output_token_ratio(record.payload))
    return EvaluationResult(
        evaluator.metric_name,
        evaluator.evaluator_version,
        None,
        EvaluationStatus.ERROR,
        {"reason": "invalid_metric_input"},
    )


def _evaluate_cache_input_token_ratio(record: TraceMetricRecord) -> EvaluationResult:
    evaluator = CacheInputTokenRatioEvaluator()
    if record.mode == "chat" and isinstance(record.payload, Mapping):
        return evaluator.evaluate(normalize_chat_cache_input_token_ratio(record.payload))
    if record.mode == "research" and _is_sequence(record.payload):
        return evaluator.evaluate(normalize_research_cache_input_token_ratio(record.payload))
    return EvaluationResult(
        evaluator.metric_name,
        evaluator.evaluator_version,
        None,
        EvaluationStatus.ERROR,
        {"reason": "invalid_metric_input"},
    )


def _evaluate_per_tool_call_latency(record: TraceMetricRecord) -> EvaluationResult:
    evaluator = PerToolCallLatencyEvaluator()
    if record.mode == "chat" and isinstance(record.payload, Mapping):
        return evaluator.evaluate(normalize_chat_tool_call_latency(record.payload))
    if record.mode == "research" and _is_sequence(record.payload):
        return evaluator.evaluate(
            normalize_research_tool_call_latency(
                record.payload,
                session_id=record.session_id,
                user_message_id=record.log_id,
            )
        )
    return EvaluationResult(
        evaluator.metric_name,
        evaluator.evaluator_version,
        None,
        EvaluationStatus.ERROR,
        {"reason": "invalid_metric_input"},
    )


def _evaluate_end_to_end_latency(record: TraceMetricRecord) -> EvaluationResult:
    evaluator = EndToEndLatencyEvaluator()
    if record.mode == "chat" and isinstance(record.payload, Mapping):
        return evaluator.evaluate(normalize_chat_end_to_end_latency(record.payload))
    if record.mode == "research" and _is_sequence(record.payload):
        return evaluator.evaluate(normalize_research_end_to_end_latency(record.payload))
    return EvaluationResult(
        evaluator.metric_name,
        evaluator.evaluator_version,
        None,
        EvaluationStatus.ERROR,
        {"reason": "invalid_metric_input"},
    )


def _evaluate_max_tool_latency_share(record: TraceMetricRecord) -> EvaluationResult:
    evaluator = MaxToolLatencyShareEvaluator()
    if record.mode == "chat" and isinstance(record.payload, Mapping):
        return evaluator.evaluate(normalize_chat_max_tool_latency_share(record.payload))
    if record.mode == "research" and _is_sequence(record.payload):
        return evaluator.evaluate(
            normalize_research_max_tool_latency_share(
                record.payload,
                session_id=record.session_id,
                user_message_id=record.log_id,
            )
        )
    return EvaluationResult(
        evaluator.metric_name,
        evaluator.evaluator_version,
        None,
        EvaluationStatus.ERROR,
        {"reason": "invalid_metric_input"},
    )


# 后续指标完成后，只需新增适配函数并在这里注册一个 MetricColumn。
METRIC_COLUMNS: tuple[MetricColumn, ...] = (
    MetricColumn("tool_calls_count", _evaluate_tool_calls_count),
    MetricColumn("per_tool_repeat_call_count", _evaluate_per_tool_repeat_call_count),
    MetricColumn("per_tool_call_tokens", _evaluate_per_tool_call_tokens),
    MetricColumn("total_token_consumption", _evaluate_total_token_consumption),
    MetricColumn("max_tool_token_share", _evaluate_max_tool_token_share),
    MetricColumn("output_token_ratio", _evaluate_output_token_ratio),
    MetricColumn("cache_input_token_ratio", _evaluate_cache_input_token_ratio),
    MetricColumn("per_tool_call_latency_ms", _evaluate_per_tool_call_latency),
    MetricColumn("end_to_end_latency_ms", _evaluate_end_to_end_latency),
    MetricColumn("max_tool_latency_share", _evaluate_max_tool_latency_share),
)


def summarize_logs(
    *,
    chat_paths: Sequence[Path] = (),
    research_paths: Sequence[Path] = (),
    output_path: Path,
) -> dict[str, int]:
    """读取日志、运行已注册指标并写出宽表，返回不含正文的运行摘要。"""

    records: list[TraceMetricRecord] = []
    for path in chat_paths:
        payload = _load_json(path)
        if not isinstance(payload, list):
            raise ValueError(f"聊天日志必须是 JSON 数组: {path}")
        source_key = _source_fingerprint(path, length=12)
        for index, trace in enumerate(payload, start=1):
            trace_id = _chat_trace_id(trace) or f"chat:{source_key}:{index:06d}"
            user_id, session_id = _chat_identity(trace)
            records.append(
                TraceMetricRecord(
                    trace_id,
                    "chat",
                    trace,
                    user_id=user_id,
                    session_id=session_id,
                )
            )

    for path in research_paths:
        payload = _load_json(path)
        if not isinstance(payload, list):
            raise ValueError(f"研究日志必须是 JSON 数组: {path}")
        source_key = _source_fingerprint(path, length=16)
        records.append(build_research_record(payload, fallback_id=f"research:{source_key}"))

    table = build_metric_table(records, METRIC_COLUMNS)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8", newline="") as stream:
        write_metric_table_csv(table, stream)
    return {
        "row_count": len(table.rows),
        "issue_count": len(table.issues),
        "metric_count": len(METRIC_COLUMNS),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--chat-log", action="append", type=Path, default=[])
    parser.add_argument("--research-log", action="append", type=Path, default=[])
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if not args.chat_log and not args.research_log:
        parser.error("至少需要一个 --chat-log 或 --research-log")
    summary = summarize_logs(
        chat_paths=tuple(args.chat_log),
        research_paths=tuple(args.research_log),
        output_path=args.output,
    )
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))


def _load_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as stream:
        return json.load(stream)


def _source_fingerprint(path: Path, *, length: int) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()[:length]


def _chat_trace_id(trace: object) -> str | None:
    if not isinstance(trace, Mapping):
        return None
    canvas = trace.get("canvas")
    if not isinstance(canvas, Mapping):
        return None
    meta = canvas.get("meta")
    if not isinstance(meta, Mapping):
        return None
    return _text(meta.get("traceId"))


def _chat_identity(trace: object) -> tuple[str | None, str | None]:
    """读取聊天 Trace 的用户与会话标识；消息级 ID 在当前日志中不可观测。"""

    if not isinstance(trace, Mapping):
        return None, None
    user_id = _text(trace.get("userId"))
    canvas = trace.get("canvas")
    if not isinstance(canvas, Mapping):
        return user_id, None
    meta = canvas.get("meta")
    if not isinstance(meta, Mapping):
        return user_id, None
    return user_id, _text(meta.get("sessionId"))


def build_research_record(events: list[Any], fallback_id: str) -> TraceMetricRecord:
    """把研究模式事件列表包装为待评测记录;供批量文件入口与流式入口复用。

    log_id 优先取日志内的用户级 userMessageID(兼容 body.messageID),
    提取不到时回退为调用方提供的稳定 fallback_id,保证行可追溯。
    """
    user_id, session_id, message_id = _research_identity(events)
    return TraceMetricRecord(
        message_id or fallback_id,
        "research",
        events,
        user_id=user_id,
        session_id=session_id,
    )


def _research_identity(
    events: Sequence[object],
) -> tuple[str | None, str | None, str | None]:
    """提取研究请求身份；用户消息 ID 标识整条研究日志。"""

    user_id = _first_nested_text(events, "headers", "userid")
    session_id = _first_top_level_text(events, "sessionID") or _first_nested_text(
        events, "body", "sessionId"
    )
    message_id = _first_top_level_text(events, "userMessageID") or _first_nested_text(
        events, "body", "messageID"
    )
    return user_id, session_id, message_id


def _first_top_level_text(events: Sequence[object], key: str) -> str | None:
    for event in events:
        if isinstance(event, Mapping) and (value := _text(event.get(key))) is not None:
            return value
    return None


def _first_nested_text(events: Sequence[object], container_key: str, value_key: str) -> str | None:
    for event in events:
        if not isinstance(event, Mapping):
            continue
        container = event.get(container_key)
        if (
            isinstance(container, Mapping)
            and (value := _text(container.get(value_key))) is not None
        ):
            return value
    return None


def _text(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _is_sequence(value: object) -> TypeGuard[Sequence[object]]:
    return isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray))


if __name__ == "__main__":
    main()
