"""不依赖 LLM 的高精度陈述支持判定规则。"""

import json
import math
import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass

from collectors.evidence_quality import EvidenceUnit
from evaluators.statement_support import StatementSupportJudgeRequest

_SENTENCE_PATTERN = re.compile(r"[^。！？；;\n]+[。！？；;]?")
_NUMBER_PATTERN = re.compile(
    r"(?<![A-Za-z0-9])(-?\d+(?:\.\d+)?)\s*(万亿|亿元|万元|亿|万|元|%|％|个百分点)?"
)
_YEAR_PATTERN = re.compile(r"(?<!\d)((?:19|20)\d{2})\s*年?")
_SPACE_PATTERN = re.compile(r"\s+")
_PUNCTUATION_PATTERN = re.compile(r"[\s\W_]+", re.UNICODE)
_NEGATION_PATTERN = re.compile(r"并非|没有|未曾|未|不|无")

_EXCLUDED_PATTERNS = (
    "建议关注",
    "建议持续关注",
    "仅供参考",
    "不构成投资建议",
    "以下是",
    "综上",
    "免责声明",
    "请注意",
    "核心结论",
    "风险提示",
    "数据来源",
    "参考资料",
)
_FACTUAL_MARKERS = (
    "为",
    "是",
    "包括",
    "达到",
    "增长",
    "下降",
    "同比",
    "环比",
    "占比",
    "位于",
    "成立",
    "发布",
    "收购",
    "驱动",
    "导致",
)
_COMPLEX_RELATION_MARKERS = ("主要由", "驱动", "导致", "得益于", "表明", "意味着")
_METRIC_GROUPS = (
    ("revenue", frozenset({"收入", "营业收入", "营收", "revenue"})),
    ("profit", frozenset({"利润", "净利润", "profit"})),
    ("volume", frozenset({"产量", "销量", "销售量", "production", "volume"})),
    ("price", frozenset({"价格", "均价", "单价", "price"})),
    ("yoy", frozenset({"同比", "同比增长", "yoy"})),
    ("qoq", frozenset({"环比", "环比增长", "qoq", "mom"})),
    ("share", frozenset({"占比", "比例", "份额", "share"})),
)
_AMOUNT_FACTORS = {"万亿": 1e12, "亿": 1e8, "亿元": 1e8, "万": 1e4, "万元": 1e4, "元": 1.0}


@dataclass(frozen=True, slots=True)
class _Number:
    value: float
    kind: str


@dataclass(frozen=True, slots=True)
class _EvidenceText:
    unit_id: str
    normalized: str
    bigrams: frozenset[str]
    numbers: tuple[_Number, ...]
    years: frozenset[int]
    metrics: frozenset[str]


class DeterministicStatementSupportJudge:
    """用可复现规则拆分陈述，并与全部证据做确定性比对。"""

    judge_name = "deterministic-statement-support"
    judge_version = "rules-1.0.0"
    prompt_version = "not-applicable"

    def judge(self, request: StatementSupportJudgeRequest) -> Mapping[str, object]:
        claims = _split_claims(request.answer)
        evidence = tuple(_normalize_evidence(unit) for unit in request.evidence_units)
        results: list[Mapping[str, object]] = []
        for index, claim in enumerate(claims, start=1):
            claim_id = f"c{index:04d}"
            if _is_excluded(claim):
                results.append(_result(claim_id, "excluded", "excluded", [], 1.0))
                continue
            verdict, reason, unit_ids, confidence = _assess_claim(claim, evidence)
            results.append(
                _result(claim_id, "verifiable", verdict, unit_ids, confidence, reason=reason)
            )
        if not results:
            results.append(_result("c0001", "excluded", "excluded", [], 1.0))
        return {"claims": results}


def _split_claims(answer: str) -> tuple[str, ...]:
    claims: list[str] = []
    for match in _SENTENCE_PATTERN.finditer(answer):
        sentence = re.sub(r"^(?:[-*>#]+|\d+[.)、])\s*", "", match.group().strip())
        if not sentence:
            continue
        clauses = re.split(r"[，,]", sentence)
        if len(clauses) > 1 and sum(_looks_factual(item) for item in clauses) >= 2:
            claims.extend(item.strip() for item in clauses if item.strip())
        else:
            claims.append(sentence)
    return tuple(claims)


def _looks_factual(text: str) -> bool:
    return bool(_NUMBER_PATTERN.search(text) or any(marker in text for marker in _FACTUAL_MARKERS))


def _is_excluded(claim: str) -> bool:
    normalized = _compact(claim)
    stripped = claim.strip()
    if not normalized or re.fullmatch(r"[:|\-—_=]+", stripped):
        return True
    has_excluded_pattern = any(pattern in normalized for pattern in _EXCLUDED_PATTERNS)
    if has_excluded_pattern and not _NUMBER_PATTERN.search(normalized):
        return True
    return (
        len(normalized) <= 24
        and not _looks_factual(normalized)
        and (stripped.endswith(("：", ":")) or not re.search(r"[。！？；;]$", stripped))
    )


def _assess_claim(
    claim: str, evidence: tuple[_EvidenceText, ...]
) -> tuple[str, str, list[str], float]:
    if not evidence:
        return "unsupported", "no_evidence", [], 1.0
    normalized = _compact(claim)
    claim_bigrams = frozenset(_bigrams(normalized))
    claim_numbers = _extract_numbers(claim)
    claim_years = frozenset(int(year) for year in _YEAR_PATTERN.findall(claim))
    claim_metrics = _metric_labels(normalized)
    ranked = sorted(
        evidence,
        key=lambda item: _set_similarity(claim_bigrams, item.bigrams),
        reverse=True,
    )

    direct_units = [
        item.unit_id
        for item in ranked
        if _directly_supports(
            normalized, claim_bigrams, claim_numbers, claim_years, claim_metrics, item
        )
    ]
    if direct_units:
        return "supported", "directly_supported", direct_units, 1.0

    derived_units = _simple_derivation(claim, claim_numbers, claim_metrics, evidence)
    if derived_units:
        return "supported", "supported_by_simple_derivation", derived_units, 0.95

    related = [
        item for item in ranked if _is_related(claim_bigrams, claim_years, claim_metrics, item)
    ]
    if claim_numbers and related and _has_numeric_conflict(claim_numbers, related):
        return "unsupported", "contradicted_by_evidence", [], 0.95
    if _has_negation_conflict(normalized, related):
        return "unsupported", "contradicted_by_evidence", [], 0.9
    if any(marker in claim for marker in _COMPLEX_RELATION_MARKERS) and related:
        return "not_assessable", "incomplete_evidence", [], 0.7
    if related:
        return "not_assessable", "incomplete_evidence", [], 0.6
    return "unsupported", "no_supporting_evidence", [], 0.9


def _normalize_evidence(unit: EvidenceUnit) -> _EvidenceText:
    try:
        payload = json.loads(unit.content)
    except json.JSONDecodeError:
        payload = unit.content
    text = _flatten(payload)
    normalized = _compact(text)
    numbers = tuple(dict.fromkeys((*_extract_numbers(text), *_extract_structured_numbers(payload))))
    return _EvidenceText(
        unit.unit_id,
        normalized,
        frozenset(_bigrams(normalized)),
        numbers,
        frozenset(int(year) for year in _YEAR_PATTERN.findall(text)),
        _metric_labels(normalized),
    )


def _flatten(value: object) -> str:
    if isinstance(value, Mapping):
        return " ".join(f"{key} {_flatten(item)}" for key, item in value.items())
    if isinstance(value, list):
        return " ".join(_flatten(item) for item in value)
    if value is None:
        return ""
    return str(value)


def _directly_supports(
    claim_text: str,
    claim_bigrams: frozenset[str],
    claim_numbers: tuple[_Number, ...],
    claim_years: frozenset[int],
    claim_metrics: frozenset[str],
    evidence: _EvidenceText,
) -> bool:
    similarity = _set_similarity(claim_bigrams, evidence.bigrams)
    if claim_years and not claim_years.issubset(evidence.years):
        return False
    if claim_metrics and not claim_metrics.intersection(evidence.metrics):
        return False
    if claim_numbers:
        if not claim_metrics and similarity < 0.15:
            return False
        return all(_number_is_present(number, evidence.numbers) for number in claim_numbers)
    if claim_text in evidence.normalized or evidence.normalized in claim_text:
        return min(len(claim_text), len(evidence.normalized)) >= 6
    return similarity >= 0.72


def _is_related(
    claim_bigrams: frozenset[str],
    claim_years: frozenset[int],
    claim_metrics: frozenset[str],
    evidence: _EvidenceText,
) -> bool:
    if claim_metrics and claim_metrics.intersection(evidence.metrics):
        return not claim_years or bool(claim_years.intersection(evidence.years))
    return _set_similarity(claim_bigrams, evidence.bigrams) >= 0.25


def _has_numeric_conflict(
    claim_numbers: tuple[_Number, ...], evidence: list[_EvidenceText]
) -> bool:
    available = tuple(number for item in evidence for number in item.numbers)
    comparable = [
        number
        for number in claim_numbers
        if number.kind not in {"year"} and any(item.kind == number.kind for item in available)
    ]
    return bool(comparable) and any(
        not _number_is_present(number, available) for number in comparable
    )


def _has_negation_conflict(claim: str, evidence: list[_EvidenceText]) -> bool:
    if not _is_negated(claim):
        return False
    positive_form = _NEGATION_PATTERN.sub("", claim)
    return len(positive_form) >= 6 and any(
        positive_form in item.normalized and claim not in item.normalized for item in evidence
    )


def _simple_derivation(
    claim: str,
    claim_numbers: tuple[_Number, ...],
    claim_metrics: frozenset[str],
    evidence: tuple[_EvidenceText, ...],
) -> list[str]:
    if "yoy" not in claim_metrics:
        return []
    target = next((number.value for number in claim_numbers if number.kind == "percent"), None)
    if target is None:
        return []
    observations: list[tuple[int, float, str]] = []
    base_metrics = claim_metrics - {"yoy", "qoq"}
    for item in evidence:
        if base_metrics and not base_metrics.intersection(item.metrics):
            continue
        values = [number.value for number in item.numbers if number.kind == "amount"]
        # 只有来源单元内时间和值都唯一时才建立配对，避免把表格中的多期、多指标做笛卡尔积。
        if len(item.years) == 1 and len(values) == 1:
            observations.append((next(iter(item.years)), values[0], item.unit_id))
    observations.sort(reverse=True)
    for current in observations:
        for previous in observations:
            if current[0] - previous[0] != 1 or previous[1] == 0:
                continue
            growth = (current[1] - previous[1]) / abs(previous[1])
            if math.isclose(growth, target, rel_tol=0.02, abs_tol=0.005):
                return list(dict.fromkeys((current[2], previous[2])))
    return []


def _extract_numbers(text: str) -> tuple[_Number, ...]:
    years = {match.span() for match in _YEAR_PATTERN.finditer(text)}
    numbers: list[_Number] = []
    for match in _NUMBER_PATTERN.finditer(text):
        raw_value, unit = match.groups()
        if any(start <= match.start() and match.end() <= end for start, end in years):
            numbers.append(_Number(float(raw_value), "year"))
            continue
        value = float(raw_value)
        if unit in {"%", "％", "个百分点"}:
            numbers.append(_Number(value / 100, "percent"))
        elif unit in _AMOUNT_FACTORS:
            numbers.append(_Number(value * _AMOUNT_FACTORS[unit], "amount"))
        else:
            numbers.append(_Number(value, "scalar"))
    return tuple(numbers)


def _extract_structured_numbers(value: object, key: str = "") -> tuple[_Number, ...]:
    if isinstance(value, Mapping):
        return tuple(
            number
            for raw_key, item in value.items()
            for number in _extract_structured_numbers(item, str(raw_key))
        )
    if isinstance(value, list):
        return tuple(number for item in value for number in _extract_structured_numbers(item, key))
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return ()
    normalized_key = _compact(key)
    numeric = float(value)
    if any(
        marker in normalized_key
        for marker in ("率", "占比", "同比", "环比", "percent", "pct", "ratio", "yoy", "qoq")
    ):
        return (_Number(numeric / 100 if abs(numeric) > 1 else numeric, "percent"),)
    if any(marker in normalized_key for marker in ("金额", "元", "amount", "yuan")):
        return (_Number(numeric, "amount"),)
    return ()


def _number_is_present(target: _Number, candidates: tuple[_Number, ...]) -> bool:
    tolerance = 0.005 if target.kind == "percent" else 1e-6
    return any(
        _compatible_number_kinds(target, candidate)
        and math.isclose(candidate.value, target.value, rel_tol=tolerance, abs_tol=tolerance)
        for candidate in candidates
    )


def _compatible_number_kinds(target: _Number, candidate: _Number) -> bool:
    if target.kind == candidate.kind:
        return True
    return {target.kind, candidate.kind} == {"amount", "scalar"} and max(
        abs(target.value), abs(candidate.value)
    ) >= 1e6


def _metric_labels(text: str) -> frozenset[str]:
    labels: set[str] = set()
    lowered = text.lower()
    for label, group in _METRIC_GROUPS:
        if any(alias.lower() in lowered for alias in group):
            labels.add(label)
    return frozenset(labels)


def _set_similarity(left_grams: frozenset[str], right_grams: frozenset[str]) -> float:
    if not left_grams or not right_grams:
        return 0.0
    return len(left_grams & right_grams) / len(left_grams | right_grams)


def _bigrams(text: str) -> set[str]:
    normalized = _compact(text)
    return {normalized[index : index + 2] for index in range(len(normalized) - 1)}


def _compact(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text).lower()
    return _PUNCTUATION_PATTERN.sub("", _SPACE_PATTERN.sub("", normalized))


def _is_negated(text: str) -> bool:
    return _NEGATION_PATTERN.search(text) is not None


def _result(
    claim_id: str,
    claim_type: str,
    verdict: str,
    supporting_unit_ids: list[str],
    confidence: float,
    *,
    reason: str | None = None,
) -> Mapping[str, object]:
    if reason is None:
        reason = "instruction_or_disclaimer"
    return {
        "claim_id": claim_id,
        "claim_type": claim_type,
        "verdict": verdict,
        "reason_code": reason,
        "supporting_unit_ids": supporting_unit_ids,
        "confidence": confidence,
    }
