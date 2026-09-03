from pathlib import Path

from evaluators.routing import ReusableIntentRouteMapping
from storage.intent_route_mappings import JsonlIntentRouteMappingStore


def _mapping(signature: tuple[str, ...]) -> ReusableIntentRouteMapping:
    return ReusableIntentRouteMapping(
        intent_signature=signature,
        subtask_type="quote_lookup",
        capability_ids=("quote",),
        schema_version="0.1.0",
        judge_name="judge",
        judge_version="model",
        prompt_version="1.0.0",
    )


def test_store_persists_only_safe_mapping_fields_and_deduplicates(tmp_path: Path) -> None:
    path = tmp_path / "routing" / "intent-route-mappings.jsonl"
    store = JsonlIntentRouteMappingStore(path)

    store.append([_mapping(("market_data",)), _mapping(("market_data",))])

    raw = path.read_text(encoding="utf-8")
    assert raw.count("market_data") == 1
    assert "query" not in raw
    assert store.load_recent(limit=10) == (_mapping(("market_data",)),)


def test_store_finds_similar_signatures_by_jaccard_score(tmp_path: Path) -> None:
    store = JsonlIntentRouteMappingStore(tmp_path / "mappings.jsonl")
    store.append(
        [
            _mapping(("market_data", "valuation")),
            _mapping(("news_review",)),
        ]
    )

    matches = store.find_similar(
        ("market_data", "valuation", "company_analysis"),
        minimum_similarity=0.5,
        limit=5,
    )

    assert matches == (_mapping(("market_data", "valuation")),)


def test_store_ignores_tampered_record_with_non_signature_text(tmp_path: Path) -> None:
    path = tmp_path / "mappings.jsonl"
    path.write_text(
        '{"intent_signature":["贵州茅台"],"subtask_type":"quote_lookup",'
        '"capability_ids":["quote"],"schema_version":"0.1.0",'
        '"judge_name":"judge","judge_version":"model","prompt_version":"1.0.0"}\n',
        encoding="utf-8",
    )

    assert JsonlIntentRouteMappingStore(path).load_recent(limit=10) == ()
