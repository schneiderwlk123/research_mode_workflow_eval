from copy import deepcopy

import pytest

from evaluators.base import EvaluationResult, EvaluationStatus
from reporters.metric_table import (
    MetricColumn,
    TraceMetricRecord,
    build_metric_table,
)


def _value_metric(record: TraceMetricRecord) -> EvaluationResult:
    payload = record.payload
    assert isinstance(payload, dict)
    return _computed("metric_a", int(payload["value"]))


def _status_metric(record: TraceMetricRecord) -> EvaluationResult:
    payload = record.payload
    assert isinstance(payload, dict)
    if payload["status"] == "not_computable":
        return EvaluationResult(
            "metric_a",
            "1.0.0",
            None,
            EvaluationStatus.NOT_COMPUTABLE,
            {"reason": "missing_input", "extra_count": 10},
        )
    return EvaluationResult(
        "metric_a",
        "1.0.0",
        None,
        EvaluationStatus.ERROR,
        {"reason": "invalid_input", "extra_count": 20},
    )


def _raise_metric(_: TraceMetricRecord) -> EvaluationResult:
    raise RuntimeError("sensitive failure detail")


def _computed(metric_name: str, value: int) -> EvaluationResult:
    return EvaluationResult(
        metric_name,
        "1.0.0",
        value,
        EvaluationStatus.COMPUTED,
        {"diagnostic_count": 99},
    )


def _partially_computed(_: TraceMetricRecord) -> EvaluationResult:
    return EvaluationResult(
        "structured_metric",
        "1.0.0",
        '[{"tool_name":"search","value":null}]',
        EvaluationStatus.COMPUTED,
        {"partial_issue_reasons": ("incomplete_token_coverage",)},
    )


def test_builds_one_value_column_per_metric_without_metadata_leakage() -> None:
    records = (
        TraceMetricRecord(
            "trace-1",
            "chat",
            {"value": 3, "secret": "PRIVATE"},
            user_id="user-1",
            session_id="session-1",
        ),
        TraceMetricRecord(
            "message-2",
            "research",
            {"value": 5, "secret": "HIDDEN"},
            user_id="user-2",
            session_id="session-2",
        ),
    )
    original_payloads = deepcopy([record.payload for record in records])
    column = MetricColumn("metric_a", _value_metric)

    table = build_metric_table(records, (column,))

    assert table.fieldnames == (
        "user_id",
        "session_id",
        "log_id",
        "mode",
        "metric_a",
    )
    assert tuple(dict(row) for row in table.rows) == (
        {
            "user_id": "user-1",
            "session_id": "session-1",
            "log_id": "trace-1",
            "mode": "chat",
            "metric_a": 3,
        },
        {
            "user_id": "user-2",
            "session_id": "session-2",
            "log_id": "message-2",
            "mode": "research",
            "metric_a": 5,
        },
    )
    assert table.issues == ()
    assert "secret" not in repr(table)
    assert [record.payload for record in records] == original_payloads


def test_non_computable_and_error_results_leave_empty_cells_and_record_separate_issues() -> None:
    records = (
        TraceMetricRecord("trace-1", "chat", {"status": "not_computable"}),
        TraceMetricRecord("trace-2", "chat", {"status": "error"}),
    )
    column = MetricColumn("metric_a", _status_metric)

    table = build_metric_table(records, (column,))

    assert tuple(dict(row) for row in table.rows) == (
        {
            "user_id": None,
            "session_id": None,
            "log_id": "trace-1",
            "mode": "chat",
            "metric_a": None,
        },
        {
            "user_id": None,
            "session_id": None,
            "log_id": "trace-2",
            "mode": "chat",
            "metric_a": None,
        },
    )
    assert [(issue.status, issue.reason) for issue in table.issues] == [
        ("not_computable", "missing_input"),
        ("error", "invalid_input"),
    ]


def test_metric_execution_failure_is_isolated_from_other_columns() -> None:
    record = TraceMetricRecord("trace-1", "chat", {})
    columns = (
        MetricColumn("broken_metric", _raise_metric),
        MetricColumn("healthy_metric", lambda _: _computed("healthy_metric", 7)),
    )

    table = build_metric_table((record,), columns)

    assert dict(table.rows[0]) == {
        "user_id": None,
        "session_id": None,
        "log_id": "trace-1",
        "mode": "chat",
        "broken_metric": None,
        "healthy_metric": 7,
    }
    assert len(table.issues) == 1
    assert table.issues[0].metric_name == "broken_metric"
    assert table.issues[0].reason == "metric_execution_error"


def test_computed_structured_value_keeps_partial_issues_outside_csv_columns() -> None:
    table = build_metric_table(
        (TraceMetricRecord("trace-1", "chat", {}),),
        (MetricColumn("structured_metric", _partially_computed),),
    )

    assert dict(table.rows[0]) == {
        "user_id": None,
        "session_id": None,
        "log_id": "trace-1",
        "mode": "chat",
        "structured_metric": '[{"tool_name":"search","value":null}]',
    }
    assert [(issue.status, issue.reason) for issue in table.issues] == [
        ("not_computable", "incomplete_token_coverage")
    ]


def test_computed_structured_value_preserves_local_issue_status() -> None:
    def evaluate(_: TraceMetricRecord) -> EvaluationResult:
        return EvaluationResult(
            "structured_metric",
            "1.0.0",
            '[{"tool_name":"bad","value":null}]',
            EvaluationStatus.COMPUTED,
            {"partial_issues": ({"status": "error", "reason": "invalid_duration"},)},
        )

    table = build_metric_table(
        (TraceMetricRecord("trace-1", "chat", {}),),
        (MetricColumn("structured_metric", evaluate),),
    )

    assert [(issue.status, issue.reason) for issue in table.issues] == [
        ("error", "invalid_duration")
    ]


@pytest.mark.parametrize(
    "columns",
    [
        (MetricColumn("metric_a", _value_metric), MetricColumn("metric_a", _value_metric)),
        (MetricColumn("log_id", _value_metric),),
        (MetricColumn("mode", _value_metric),),
        (MetricColumn("user_id", _value_metric),),
        (MetricColumn("session_id", _value_metric),),
    ],
)
def test_rejects_duplicate_or_reserved_metric_names(columns: tuple[MetricColumn, ...]) -> None:
    with pytest.raises(ValueError):
        build_metric_table((), columns)
