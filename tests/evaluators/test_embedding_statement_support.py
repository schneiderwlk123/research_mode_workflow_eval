import json
from collections.abc import Mapping, Sequence
from typing import Literal

from collectors.evidence_quality import EvidenceUnit
from evaluators.embedding_statement_support import (
    EmbeddingStatementSupportJudge,
    InMemoryEmbeddingCache,
)
from evaluators.statement_support import StatementSupportJudgeRequest


class FakeEmbeddingProvider:
    def __init__(
        self,
        vectors: dict[tuple[Literal["query", "document"], str], tuple[float, ...]],
        *,
        revision: str = "revision-a",
    ) -> None:
        self.model_name = "fake-bge"
        self.model_revision = revision
        self._vectors = vectors
        self.calls: list[tuple[Literal["query", "document"], tuple[str, ...]]] = []

    def encode(
        self, texts: Sequence[str], *, kind: Literal["query", "document"]
    ) -> tuple[tuple[float, ...], ...]:
        self.calls.append((kind, tuple(texts)))
        return tuple(self._vectors[(kind, text)] for text in texts)


def _request(answer: str, *evidence: str) -> StatementSupportJudgeRequest:
    return StatementSupportJudgeRequest(
        trace_id="trace-1",
        mode="chat",
        query="分析公司经营情况",
        answer=answer,
        evidence_units=tuple(
            EvidenceUnit(f"call-{index}:0", "search", content)
            for index, content in enumerate(evidence, start=1)
        ),
        schema_version="0.1.0",
        evidence_scope_version="evidence-scope-test",
    )


def _provider(
    claim: str,
    evidence: str,
    *,
    claim_vector: tuple[float, ...] = (1.0, 0.0),
    evidence_vector: tuple[float, ...] = (1.0, 0.0),
    revision: str = "revision-a",
) -> FakeEmbeddingProvider:
    return FakeEmbeddingProvider(
        {
            ("query", claim): claim_vector,
            ("document", _document_text(evidence)): evidence_vector,
        },
        revision=revision,
    )


def _document_text(content: str) -> str:
    value = json.loads(content)
    if isinstance(value, Mapping):
        return " ".join(f"{key} {item}" for key, item in value.items())
    return str(value)


def test_semantic_paraphrase_is_supported_after_structural_checks() -> None:
    claim = "该企业的主营业务涵盖铜矿采掘。"
    evidence = '"公司核心业务包括铜资源的开采。"'
    judge = EmbeddingStatementSupportJudge(_provider(claim, evidence))

    response = judge.judge(_request(claim, evidence))

    assert response["claims"][0]["verdict"] == "supported"  # type: ignore[index]
    assert response["claims"][0]["supporting_unit_ids"] == ["call-1:0"]  # type: ignore[index]


def test_high_similarity_cannot_override_numeric_or_time_conflict() -> None:
    claim = "公司2025年营业收入为100亿元。"
    evidence = '{"year":2024,"营业收入":"80亿元"}'
    judge = EmbeddingStatementSupportJudge(_provider(claim, evidence))

    response = judge.judge(_request(claim, evidence))

    assert response["claims"][0]["verdict"] == "unsupported"  # type: ignore[index]
    assert response["claims"][0]["reason_code"] == "contradicted_by_evidence"  # type: ignore[index]


def test_high_similarity_cannot_override_opposite_direction_or_negation() -> None:
    claim = "公司收入增长且未发生重大收购。"
    evidence = '"公司收入下降并发生重大收购。"'
    judge = EmbeddingStatementSupportJudge(_provider(claim, evidence))

    response = judge.judge(_request(claim, evidence))

    assert response["claims"][0]["verdict"] == "unsupported"  # type: ignore[index]


def test_middle_similarity_is_not_assessable_instead_of_forced_negative() -> None:
    claim = "该企业的主营业务涵盖铜矿采掘。"
    evidence = '"公司核心业务包括铜资源的开采。"'
    judge = EmbeddingStatementSupportJudge(
        _provider(claim, evidence, evidence_vector=(0.65, 0.76)),
        high_similarity=0.82,
        low_similarity=0.45,
    )

    response = judge.judge(_request(claim, evidence))

    assert response["claims"][0]["verdict"] == "not_assessable"  # type: ignore[index]


def test_low_similarity_remains_unsupported() -> None:
    claim = "该企业的主营业务涵盖铜矿采掘。"
    evidence = '"天气晴朗。"'
    judge = EmbeddingStatementSupportJudge(_provider(claim, evidence, evidence_vector=(0.0, 1.0)))

    response = judge.judge(_request(claim, evidence))

    assert response["claims"][0]["verdict"] == "unsupported"  # type: ignore[index]


def test_embedding_cache_is_isolated_by_model_revision() -> None:
    claim = "该企业的主营业务涵盖铜矿采掘。"
    evidence = '"公司核心业务包括铜资源的开采。"'
    cache = InMemoryEmbeddingCache()
    first = _provider(claim, evidence, revision="revision-a")
    second = _provider(claim, evidence, revision="revision-b")

    EmbeddingStatementSupportJudge(first, cache=cache).judge(_request(claim, evidence))
    EmbeddingStatementSupportJudge(second, cache=cache).judge(_request(claim, evidence))

    assert any(kind == "document" for kind, _ in first.calls)
    assert any(kind == "document" for kind, _ in second.calls)
