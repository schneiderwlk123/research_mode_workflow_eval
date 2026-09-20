"""把松耦合指标适配器汇总为一行一个 Trace 的宽表。"""

import csv
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal, TextIO, TypeAlias

from evaluators.base import EvaluationResult, EvaluationStatus

TraceMode: TypeAlias = Literal["chat", "research"]
MetricCell: TypeAlias = str | int | float | None
MetricEvaluator: TypeAlias = Callable[["TraceMetricRecord"], EvaluationResult]

_IDENTITY_FIELDS = ("user_id", "session_id", "log_id", "mode")


@dataclass(frozen=True, slots=True)
class TraceMetricRecord:
    """一条待评测记录；payload 只在指标适配器内使用，不进入输出表。"""

    log_id: str
    mode: TraceMode
    payload: object
    user_id: str | None = None
    session_id: str | None = None

    def __post_init__(self) -> None:
        if not self.log_id.strip():
            raise ValueError("log_id 必须是非空字符串")


@dataclass(frozen=True, slots=True)
class MetricColumn:
    """一个指标列及其独立执行入口。"""

    name: str
    evaluate: MetricEvaluator

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("指标列名必须是非空字符串")


@dataclass(frozen=True, slots=True)
class MetricIssue:
    """未写入宽表字段的独立失败摘要。"""

    log_id: str
    mode: TraceMode
    metric_name: str
    status: str
    reason: str


@dataclass(frozen=True, slots=True)
class MetricTable:
    """指标宽表及与数值列分离的问题摘要。"""

    fieldnames: tuple[str, ...]
    rows: tuple[Mapping[str, MetricCell], ...]
    issues: tuple[MetricIssue, ...]


def build_metric_table(
    records: Iterable[TraceMetricRecord], columns: Sequence[MetricColumn]
) -> MetricTable:
    """逐 Trace 独立执行已注册指标，每个指标只占一个数值列。"""

    metric_names = _validated_metric_names(columns)
    rows: list[Mapping[str, MetricCell]] = []
    issues: list[MetricIssue] = []
    for record in records:
        row: dict[str, MetricCell] = {
            "user_id": record.user_id,
            "session_id": record.session_id,
            "log_id": record.log_id,
            "mode": record.mode,
        }
        for column in columns:
            try:
                result = column.evaluate(record)
            except Exception:
                row[column.name] = None
                issues.append(
                    MetricIssue(
                        record.log_id,
                        record.mode,
                        column.name,
                        EvaluationStatus.ERROR.value,
                        "metric_execution_error",
                    )
                )
                continue
            if result.metric_name != column.name:
                row[column.name] = None
                issues.append(
                    MetricIssue(
                        record.log_id,
                        record.mode,
                        column.name,
                        EvaluationStatus.ERROR.value,
                        "metric_contract_mismatch",
                    )
                )
                continue
            if result.status is EvaluationStatus.COMPUTED:
                row[column.name] = result.value
                partial_issues = result.metadata.get("partial_issues", ())
                if isinstance(partial_issues, Sequence) and not isinstance(
                    partial_issues, (str, bytes, bytearray)
                ):
                    for issue in partial_issues:
                        if not isinstance(issue, Mapping):
                            continue
                        status = issue.get("status")
                        reason = issue.get("reason")
                        if (
                            status
                            in {
                                EvaluationStatus.NOT_COMPUTABLE.value,
                                EvaluationStatus.ERROR.value,
                            }
                            and isinstance(reason, str)
                            and reason
                        ):
                            issues.append(
                                MetricIssue(
                                    record.log_id,
                                    record.mode,
                                    column.name,
                                    status,
                                    reason,
                                )
                            )
                partial_reasons = result.metadata.get("partial_issue_reasons", ())
                if isinstance(partial_reasons, Sequence) and not isinstance(
                    partial_reasons, (str, bytes, bytearray)
                ):
                    for reason in partial_reasons:
                        if isinstance(reason, str) and reason:
                            issues.append(
                                MetricIssue(
                                    record.log_id,
                                    record.mode,
                                    column.name,
                                    EvaluationStatus.NOT_COMPUTABLE.value,
                                    reason,
                                )
                            )
                continue
            row[column.name] = None
            reason = result.metadata.get("reason")
            issues.append(
                MetricIssue(
                    record.log_id,
                    record.mode,
                    column.name,
                    result.status.value,
                    reason if isinstance(reason, str) and reason else result.status.value,
                )
            )
        rows.append(MappingProxyType(row))
    return MetricTable(
        fieldnames=(*_IDENTITY_FIELDS, *metric_names),
        rows=tuple(rows),
        issues=tuple(issues),
    )


def write_metric_table_csv(table: MetricTable, stream: TextIO) -> None:
    """写出仅含标识列和指标值列的 CSV，不混入诊断统计。"""

    writer = csv.DictWriter(stream, fieldnames=table.fieldnames, extrasaction="raise")
    writer.writeheader()
    writer.writerows(table.rows)


def _validated_metric_names(columns: Sequence[MetricColumn]) -> tuple[str, ...]:
    names = tuple(column.name for column in columns)
    if len(names) != len(set(names)):
        raise ValueError("指标列名不得重复")
    if set(names) & set(_IDENTITY_FIELDS):
        raise ValueError("指标列名不得占用身份字段或 mode")
    return names
