"""复用最终回答与证据单元，构建召回数值一致率的内存候选。"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TypedDict

from collectors.evidence_quality import EvidenceDiagnostics, EvidenceSourceCatalog, EvidenceUnit
from collectors.statement_support import (
    NormalizedStatementSupportTrace,
    StatementSupportCandidate,
    normalize_chat_statement_support,
    normalize_research_statement_support,
)


@dataclass(frozen=True, slots=True)
class NumericConsistencyCandidate:
    """数值对齐候选；回答和证据正文只允许在 Judge 生命周期内存在。"""

    query: str
    answer: str
    evidence_units: tuple[EvidenceUnit, ...]


class NormalizedNumericConsistencyTrace(TypedDict):
    """供数值一致率评测器消费的规范化输入。"""

    trace_id: str | None
    mode: str
    schema_version: str
    evidence_scope_version: str
    evidence_unit_count: int
    candidate: NumericConsistencyCandidate | None
    diagnostics: EvidenceDiagnostics
    normalization_error: str | None


def normalize_chat_numeric_consistency(
    trace: Mapping[str, object], catalog: EvidenceSourceCatalog
) -> NormalizedNumericConsistencyTrace:
    """复用聊天最终回答和完整证据观察，不另行解释原始响应。"""

    return _from_statement_trace(normalize_chat_statement_support(trace, catalog))


def normalize_research_numeric_consistency(
    events: Sequence[object], catalog: EvidenceSourceCatalog
) -> NormalizedNumericConsistencyTrace:
    """复用研究模式最终回答选择与 callID 证据关联。"""

    return _from_statement_trace(normalize_research_statement_support(events, catalog))


def _from_statement_trace(
    trace: NormalizedStatementSupportTrace,
) -> NormalizedNumericConsistencyTrace:
    raw_candidate = trace.get("candidate")
    candidate = (
        NumericConsistencyCandidate(
            raw_candidate.query,
            raw_candidate.answer,
            raw_candidate.evidence_units,
        )
        if isinstance(raw_candidate, StatementSupportCandidate)
        else None
    )
    return {
        "trace_id": trace["trace_id"],
        "mode": trace["mode"],
        "schema_version": trace["schema_version"],
        "evidence_scope_version": trace["evidence_scope_version"],
        "evidence_unit_count": trace["evidence_unit_count"],
        "candidate": candidate,
        "diagnostics": trace["diagnostics"],
        "normalization_error": trace["normalization_error"],
    }
