"""使用本地开源 Embedding 召回证据，再由确定性规则判定陈述支持关系。"""

import hashlib
import importlib
import json
import math
import os
import re
from collections.abc import Mapping, Sequence
from typing import Literal, Protocol, cast

from evaluators.deterministic_statement_support import (
    _COMPLEX_RELATION_MARKERS,
    _YEAR_PATTERN,
    _assess_claim,
    _compact,
    _EvidenceText,
    _extract_numbers,
    _flatten,
    _is_excluded,
    _is_negated,
    _metric_labels,
    _normalize_evidence,
    _number_is_present,
    _result,
    _split_claims,
)
from evaluators.openai_compatible_judge_runtime import JudgeExecutionError
from evaluators.statement_support import StatementSupportJudgeRequest

EmbeddingKind = Literal["query", "document"]
EmbeddingVector = tuple[float, ...]

_DEFAULT_MODEL_NAME = "BAAI/bge-small-zh-v1.5"
_DEFAULT_MODEL_REVISION = "main"
_DIRECTION_GROUPS = (
    frozenset({"增长", "上升", "增加", "提升", "扩大", "改善"}),
    frozenset({"下降", "下滑", "减少", "降低", "收缩", "恶化"}),
)
_ENTITY_PATTERN = re.compile(r"(?:[A-Za-z][A-Za-z0-9.-]{1,20}|[\u4e00-\u9fff]{2,12}(?:公司|集团))")


class EmbeddingProvider(Protocol):
    """可替换的本地向量编码边界，测试不需要加载真实模型。"""

    model_name: str
    model_revision: str

    def encode(
        self, texts: Sequence[str], *, kind: EmbeddingKind
    ) -> tuple[EmbeddingVector, ...]: ...


class _SentenceEncoder(Protocol):
    def encode(
        self,
        sentences: Sequence[str],
        *,
        normalize_embeddings: bool,
        convert_to_numpy: bool,
        show_progress_bar: bool,
    ) -> object: ...


class _ArrayLike(Protocol):
    def tolist(self) -> list[list[float]]: ...


class InMemoryEmbeddingCache:
    """仅在进程内缓存向量；键包含模型版本，且不会持久化正文或向量。"""

    def __init__(self) -> None:
        self._vectors: dict[tuple[str, str, EmbeddingKind, str], EmbeddingVector] = {}

    def get(
        self, model_name: str, model_revision: str, kind: EmbeddingKind, text: str
    ) -> EmbeddingVector | None:
        return self._vectors.get(self._key(model_name, model_revision, kind, text))

    def put(
        self,
        model_name: str,
        model_revision: str,
        kind: EmbeddingKind,
        text: str,
        vector: EmbeddingVector,
    ) -> None:
        self._vectors[self._key(model_name, model_revision, kind, text)] = vector

    @staticmethod
    def _key(
        model_name: str, model_revision: str, kind: EmbeddingKind, text: str
    ) -> tuple[str, str, EmbeddingKind, str]:
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        return model_name, model_revision, kind, digest


class SentenceTransformerEmbeddingProvider:
    """sentence-transformers 本地适配器；模型不可用时只暴露安全原因码。"""

    def __init__(
        self,
        model_name: str = _DEFAULT_MODEL_NAME,
        model_revision: str = _DEFAULT_MODEL_REVISION,
    ) -> None:
        self.model_name = model_name
        self.model_revision = model_revision
        self._model: _SentenceEncoder | None = None

    def encode(self, texts: Sequence[str], *, kind: EmbeddingKind) -> tuple[EmbeddingVector, ...]:
        model = self._load_model()
        prepared = [self._prepare(text, kind) for text in texts]
        try:
            raw_vectors = model.encode(
                prepared,
                normalize_embeddings=True,
                convert_to_numpy=True,
                show_progress_bar=False,
            )
            rows = cast(_ArrayLike, raw_vectors).tolist()
        except Exception as error:
            raise JudgeExecutionError(
                "embedding_inference_failed",
                {
                    "embedding_model_name": self.model_name,
                    "embedding_model_revision": self.model_revision,
                },
            ) from error
        return tuple(tuple(float(value) for value in row) for row in rows)

    def _load_model(self) -> _SentenceEncoder:
        if self._model is not None:
            return self._model
        try:
            module = importlib.import_module("sentence_transformers")
            factory = module.SentenceTransformer
            self._model = cast(
                _SentenceEncoder,
                factory(
                    self.model_name,
                    revision=self.model_revision,
                    local_files_only=_local_files_only(),
                ),
            )
        except Exception as error:
            raise JudgeExecutionError(
                "embedding_model_unavailable",
                {
                    "embedding_model_name": self.model_name,
                    "embedding_model_revision": self.model_revision,
                },
            ) from error
        return self._model

    @staticmethod
    def _prepare(text: str, kind: EmbeddingKind) -> str:
        if kind == "query":
            return f"为这个句子生成表示以用于检索相关文章：{text}"
        return text


class EmbeddingStatementSupportJudge:
    """Embedding 只缩小候选范围，最终结论仍由可解释结构规则约束。"""

    judge_name = "embedding-statement-support"
    prompt_version = "not-applicable"

    def __init__(
        self,
        provider: EmbeddingProvider | None = None,
        *,
        top_k: int = 5,
        high_similarity: float = 0.82,
        low_similarity: float = 0.45,
        cache: InMemoryEmbeddingCache | None = None,
    ) -> None:
        if top_k <= 0:
            raise ValueError("top_k 必须大于零")
        if not 0 <= low_similarity < high_similarity <= 1:
            raise ValueError("相似度阈值必须满足 0 <= low < high <= 1")
        self._provider = provider or SentenceTransformerEmbeddingProvider(
            model_name=os.getenv("STATEMENT_SUPPORT_EMBEDDING_MODEL", _DEFAULT_MODEL_NAME),
            model_revision=os.getenv(
                "STATEMENT_SUPPORT_EMBEDDING_REVISION", _DEFAULT_MODEL_REVISION
            ),
        )
        self._top_k = top_k
        self._high_similarity = high_similarity
        self._low_similarity = low_similarity
        self._cache = cache or InMemoryEmbeddingCache()
        self.judge_version = (
            f"embedding-rules-1.0.0:{self._provider.model_name}@"
            f"{self._provider.model_revision}:top{top_k}:"
            f"{low_similarity:.2f}-{high_similarity:.2f}"
        )

    def judge(self, request: StatementSupportJudgeRequest) -> Mapping[str, object]:
        claims = _split_claims(request.answer)
        evidence = tuple(_normalize_evidence(unit) for unit in request.evidence_units)
        evidence_texts = tuple(_embedding_text(unit.content) for unit in request.evidence_units)
        results: list[Mapping[str, object]] = []
        candidate_count = 0
        rule_rejected_count = 0
        conflict_count = 0
        for index, claim in enumerate(claims, start=1):
            claim_id = f"c{index:04d}"
            if _is_excluded(claim):
                results.append(_result(claim_id, "excluded", "excluded", [], 1.0))
                continue
            verdict, reason, unit_ids, confidence = _assess_claim(claim, evidence)
            if verdict == "supported" or not evidence:
                results.append(
                    _result(
                        claim_id,
                        "verifiable",
                        verdict,
                        unit_ids,
                        confidence,
                        reason=reason,
                    )
                )
                continue
            ranked = self._rank(claim, evidence, evidence_texts)
            candidate_count += len(ranked)
            semantic = self._semantic_verdict(claim, ranked)
            if semantic[0] == "unsupported" and semantic[1] == "contradicted_by_evidence":
                conflict_count += 1
            if semantic[0] != "supported" and ranked and ranked[0][0] >= self._high_similarity:
                rule_rejected_count += 1
            results.append(
                _result(
                    claim_id,
                    "verifiable",
                    semantic[0],
                    semantic[2],
                    semantic[3],
                    reason=semantic[1],
                )
            )
        if not results:
            results.append(_result("c0001", "excluded", "excluded", [], 1.0))
        return {
            "claims": results,
            "diagnostics": {
                "embedding_candidate_count": candidate_count,
                "embedding_rule_rejected_count": rule_rejected_count,
                "embedding_conflict_count": conflict_count,
            },
        }

    def _rank(
        self,
        claim: str,
        evidence: tuple[_EvidenceText, ...],
        evidence_texts: tuple[str, ...],
    ) -> list[tuple[float, _EvidenceText]]:
        claim_vector = self._encode_one(claim, "query")
        evidence_vectors = self._encode_many(evidence_texts, "document")
        scored = [
            (_cosine_similarity(claim_vector, vector), item)
            for vector, item in zip(evidence_vectors, evidence, strict=True)
        ]
        scored.sort(key=lambda pair: pair[0], reverse=True)
        return scored[: self._top_k]

    def _encode_one(self, text: str, kind: EmbeddingKind) -> EmbeddingVector:
        return self._encode_many((text,), kind)[0]

    def _encode_many(
        self, texts: Sequence[str], kind: EmbeddingKind
    ) -> tuple[EmbeddingVector, ...]:
        resolved: list[EmbeddingVector | None] = []
        missing_texts: list[str] = []
        for text in texts:
            vector = self._cache.get(
                self._provider.model_name, self._provider.model_revision, kind, text
            )
            resolved.append(vector)
            if vector is None and text not in missing_texts:
                missing_texts.append(text)
        encoded = self._provider.encode(missing_texts, kind=kind) if missing_texts else ()
        if len(encoded) != len(missing_texts) or any(not _valid_vector(item) for item in encoded):
            raise JudgeExecutionError(
                "embedding_invalid_output",
                {
                    "embedding_model_name": self._provider.model_name,
                    "embedding_model_revision": self._provider.model_revision,
                },
            )
        for text, vector in zip(missing_texts, encoded, strict=True):
            self._cache.put(
                self._provider.model_name, self._provider.model_revision, kind, text, vector
            )
        final = tuple(
            vector
            if vector is not None
            else self._cache.get(
                self._provider.model_name, self._provider.model_revision, kind, text
            )
            for text, vector in zip(texts, resolved, strict=True)
        )
        if any(vector is None for vector in final):
            raise JudgeExecutionError("embedding_invalid_output", {})
        return cast(tuple[EmbeddingVector, ...], final)

    def _semantic_verdict(
        self, claim: str, ranked: list[tuple[float, _EvidenceText]]
    ) -> tuple[str, str, list[str], float]:
        related = [(score, item) for score, item in ranked if score >= self._low_similarity]
        if not related:
            return "unsupported", "no_supporting_evidence", [], 0.9
        conflicts = [item for _, item in related if _has_structural_conflict(claim, item)]
        compatible = [
            (score, item)
            for score, item in related
            if not _has_structural_conflict(claim, item) and _structure_is_sufficient(claim, item)
        ]
        if conflicts:
            return "unsupported", "contradicted_by_evidence", [], 0.95
        high = [(score, item) for score, item in compatible if score >= self._high_similarity]
        if high and not any(marker in claim for marker in _COMPLEX_RELATION_MARKERS):
            return "supported", "directly_supported", [item.unit_id for _, item in high], high[0][0]
        return "not_assessable", "incomplete_evidence", [], related[0][0]


def _embedding_text(content: str) -> str:
    try:
        return _flatten(json.loads(content))
    except json.JSONDecodeError:
        return content


def _has_structural_conflict(claim: str, evidence: _EvidenceText) -> bool:
    claim_compact = _compact(claim)
    claim_years = frozenset(int(year) for year in _YEAR_PATTERN.findall(claim))
    if claim_years and evidence.years and not claim_years.intersection(evidence.years):
        return True
    claim_metrics = _metric_labels(claim_compact)
    if claim_metrics and evidence.metrics and not claim_metrics.intersection(evidence.metrics):
        return True
    claim_numbers = tuple(number for number in _extract_numbers(claim) if number.kind != "year")
    comparable = tuple(
        number
        for number in claim_numbers
        if any(candidate.kind == number.kind for candidate in evidence.numbers)
    )
    if comparable and any(
        not _number_is_present(number, evidence.numbers) for number in comparable
    ):
        return True
    if _is_negated(claim_compact) != _is_negated(evidence.normalized):
        return True
    if _direction_conflicts(claim_compact, evidence.normalized):
        return True
    claim_entities = _entities(claim)
    evidence_entities = _entities(evidence.normalized)
    return bool(
        claim_entities and evidence_entities and not claim_entities.intersection(evidence_entities)
    )


def _structure_is_sufficient(claim: str, evidence: _EvidenceText) -> bool:
    claim_compact = _compact(claim)
    claim_years = frozenset(int(year) for year in _YEAR_PATTERN.findall(claim))
    if claim_years and not claim_years.issubset(evidence.years):
        return False
    claim_metrics = _metric_labels(claim_compact)
    if claim_metrics and not claim_metrics.intersection(evidence.metrics):
        return False
    claim_numbers = tuple(number for number in _extract_numbers(claim) if number.kind != "year")
    if claim_numbers and not all(
        _number_is_present(number, evidence.numbers) for number in claim_numbers
    ):
        return False
    return True


def _direction_conflicts(left: str, right: str) -> bool:
    directions = [
        (any(marker in left for marker in group), any(marker in right for marker in group))
        for group in _DIRECTION_GROUPS
    ]
    return (directions[0][0] and directions[1][1]) or (directions[1][0] and directions[0][1])


def _entities(text: str) -> frozenset[str]:
    generic = {"公司", "集团", "企业"}
    return frozenset(
        entity.lower() for entity in _ENTITY_PATTERN.findall(text) if entity.lower() not in generic
    )


def _valid_vector(vector: EmbeddingVector) -> bool:
    return bool(vector) and all(math.isfinite(value) for value in vector)


def _cosine_similarity(left: EmbeddingVector, right: EmbeddingVector) -> float:
    if len(left) != len(right) or not left:
        raise JudgeExecutionError("embedding_invalid_output", {})
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm == 0 or right_norm == 0:
        raise JudgeExecutionError("embedding_invalid_output", {})
    score = sum(a * b for a, b in zip(left, right, strict=True)) / (left_norm * right_norm)
    return max(-1.0, min(1.0, score))


def _local_files_only() -> bool:
    return os.getenv("STATEMENT_SUPPORT_EMBEDDING_LOCAL_ONLY", "1").strip() != "0"
