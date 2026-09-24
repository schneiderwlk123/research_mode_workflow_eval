"""backfill_api_retry_attribution 纯函数行为测试(不触 OSS)。

覆盖:_topk 的 Top-K 截断与降序/同名稳定序、build_cell 的空归因返回空串、
非空归因压成单元素 JSON 数组且键序固定、retry_after_success 透传。
"""

import json

from scripts.backfill_api_retry_attribution import TOP_K, _topk, build_cell


def test_topk_truncates_and_sorts_by_count_desc() -> None:
    counter = {"a": 5, "b": 9, "c": 1, "d": 7, "e": 3, "f": 6, "g": 2}
    out = _topk(counter)
    assert len(out) == TOP_K
    assert [x["name"] for x in out] == ["b", "d", "f", "a", "e"]
    assert [x["count"] for x in out] == [9, 7, 6, 5, 3]


def test_topk_ties_break_by_name_for_stability() -> None:
    out = _topk({"z": 2, "a": 2, "m": 2})
    assert [x["name"] for x in out] == ["a", "m", "z"]


def test_build_cell_empty_attribution_returns_empty_string() -> None:
    assert build_cell(None) == ""
    assert build_cell({}) == ""


def test_build_cell_wraps_single_element_with_fixed_key_order() -> None:
    cell = build_cell(
        {
            "by_tool": {"mkdir": 2},
            "by_endpoint": {"/ep/lookup": 2},
            "retry_after_success": 2,
        }
    )
    arr = json.loads(cell)
    assert isinstance(arr, list) and len(arr) == 1
    assert list(arr[0].keys()) == ["by_tool", "by_endpoint", "retry_after_success"]
    assert arr[0]["by_tool"] == [{"name": "mkdir", "count": 2}]
    assert arr[0]["retry_after_success"] == 2
