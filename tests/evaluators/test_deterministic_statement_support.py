from collections.abc import Mapping
from typing import cast

from collectors.evidence_quality import EvidenceUnit
from evaluators.deterministic_statement_support import DeterministicStatementSupportJudge
from evaluators.statement_support import StatementSupportJudgeRequest


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


def _claims(response: Mapping[str, object]) -> list[Mapping[str, object]]:
    return cast(list[Mapping[str, object]], response["claims"])


def test_numeric_units_and_percentages_are_matched_deterministically() -> None:
    response = DeterministicStatementSupportJudge().judge(
        _request(
            "公司2025年营业收入为100亿元，同比增长10%。建议持续关注。",
            '{"company":"公司","year":2025,"revenue_yuan":10000000000,"yoy":"10.0%"}',
        )
    )

    assert [claim["verdict"] for claim in _claims(response)] == [
        "supported",
        "supported",
        "excluded",
    ]


def test_numeric_percentage_field_is_normalized_from_structured_key() -> None:
    response = DeterministicStatementSupportJudge().judge(
        _request("公司收入同比增长10%。", '{"revenue_yoy":10}')
    )

    assert _claims(response)[0]["verdict"] == "supported"


def test_conflicting_numeric_evidence_is_unsupported() -> None:
    response = DeterministicStatementSupportJudge().judge(
        _request("公司2025年营业收入为100亿元。", '{"year":2025,"营业收入":"80亿元"}')
    )

    assert _claims(response)[0]["verdict"] == "unsupported"
    assert _claims(response)[0]["reason_code"] == "contradicted_by_evidence"


def test_text_fact_requires_strong_overlap_and_keeps_ambiguous_claim_separate() -> None:
    response = DeterministicStatementSupportJudge().judge(
        _request(
            "公司主营业务包括铜矿开采。收入增长主要由产品升级驱动。",
            '"公司主营业务包括铜矿开采及冶炼，公司收入保持增长。"',
        )
    )

    assert _claims(response)[0]["verdict"] == "supported"
    assert _claims(response)[1]["verdict"] == "not_assessable"


def test_simple_year_over_year_derivation_is_supported_across_evidence_units() -> None:
    response = DeterministicStatementSupportJudge().judge(
        _request(
            "公司2025年收入同比增长约10%。",
            '{"year":2025,"revenue":"100亿元"}',
            '{"year":2024,"revenue":"90.91亿元"}',
        )
    )

    assert _claims(response)[0]["verdict"] == "supported"
    assert _claims(response)[0]["reason_code"] == "supported_by_simple_derivation"
    assert _claims(response)[0]["supporting_unit_ids"] == ["call-1:0", "call-2:0"]


def test_derivation_does_not_mix_values_from_different_metrics() -> None:
    response = DeterministicStatementSupportJudge().judge(
        _request(
            "公司2025年收入同比增长约10%。",
            '{"year":2025,"profit":"100亿元"}',
            '{"year":2024,"profit":"90.91亿元"}',
        )
    )

    assert _claims(response)[0]["verdict"] != "supported"


def test_derivation_does_not_guess_year_value_pairs_from_multi_period_unit() -> None:
    response = DeterministicStatementSupportJudge().judge(
        _request(
            "公司2025年收入同比增长约10%。",
            '{"revenue":{"2025":"100亿元","2024":"90.91亿元"}}',
        )
    )

    assert _claims(response)[0]["verdict"] != "supported"


def test_no_evidence_is_unsupported_without_external_calls() -> None:
    response = DeterministicStatementSupportJudge().judge(_request("公司收入为100亿元。"))

    assert _claims(response)[0]["verdict"] == "unsupported"
    assert _claims(response)[0]["reason_code"] == "no_evidence"


def test_same_number_in_unrelated_evidence_does_not_create_false_support() -> None:
    response = DeterministicStatementSupportJudge().judge(
        _request("公司于2020年完成重大收购。", '"行业在2020年发布了100项政策。"')
    )

    assert _claims(response)[0]["verdict"] != "supported"


def test_opposite_negation_is_treated_as_contradictory_evidence() -> None:
    response = DeterministicStatementSupportJudge().judge(
        _request("公司未发生重大收购。", '"公司发生重大收购。"')
    )

    assert _claims(response)[0]["verdict"] == "unsupported"
    assert _claims(response)[0]["reason_code"] == "contradicted_by_evidence"


def test_markdown_heading_is_excluded_from_denominator() -> None:
    response = DeterministicStatementSupportJudge().judge(
        _request("核心结论：\n公司收入为100亿元。")
    )

    assert _claims(response)[0]["claim_type"] == "excluded"
    assert _claims(response)[1]["claim_type"] == "verifiable"


def test_leading_year_is_not_removed_as_a_list_marker() -> None:
    response = DeterministicStatementSupportJudge().judge(
        _request("2025年公司收入为100亿元。", '{"year":2025,"revenue":"100亿元"}')
    )

    assert _claims(response)[0]["verdict"] == "supported"
