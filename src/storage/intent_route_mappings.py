"""安全持久化可复用的去实体化意图到路由映射。"""

import json
import re
from collections import deque
from collections.abc import Mapping, Sequence
from pathlib import Path

from evaluators.routing import ReusableIntentRouteMapping

_SAFE_SIGNATURE_TOKEN = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


class JsonlIntentRouteMappingStore:
    """使用 JSONL 保存不含原始意图和参数值的路由映射表。"""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)

    def load_recent(self, *, limit: int) -> tuple[ReusableIntentRouteMapping, ...]:
        if limit <= 0:
            raise ValueError("limit 必须大于零")
        if not self._path.exists():
            return ()
        recent: deque[ReusableIntentRouteMapping] = deque(maxlen=limit)
        with self._path.open("r", encoding="utf-8") as source:
            for line in source:
                mapping = _decode_mapping(line)
                if mapping is not None:
                    recent.append(mapping)
        return tuple(recent)

    def append(self, mappings: Sequence[ReusableIntentRouteMapping]) -> None:
        if not mappings:
            return
        existing = set(self.load_recent(limit=100_000))
        pending: list[ReusableIntentRouteMapping] = []
        for mapping in mappings:
            if mapping in existing:
                continue
            pending.append(mapping)
            existing.add(mapping)
        if not pending:
            return
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("a", encoding="utf-8", newline="\n") as destination:
            for mapping in pending:
                destination.write(
                    json.dumps(_encode_mapping(mapping), ensure_ascii=False, separators=(",", ":"))
                )
                destination.write("\n")

    def find_similar(
        self,
        intent_signature: Sequence[str],
        *,
        minimum_similarity: float = 0.5,
        limit: int = 10,
    ) -> tuple[ReusableIntentRouteMapping, ...]:
        """按 Jaccard 相似度返回可复用映射，且不接触原始用户文本。"""

        if not 0 <= minimum_similarity <= 1:
            raise ValueError("minimum_similarity 必须位于 [0, 1]")
        if limit <= 0:
            raise ValueError("limit 必须大于零")
        query = frozenset(intent_signature)
        if not query:
            return ()
        scored: list[tuple[float, ReusableIntentRouteMapping]] = []
        for mapping in self.load_recent(limit=100_000):
            candidate = frozenset(mapping.intent_signature)
            score = len(query & candidate) / len(query | candidate)
            if score >= minimum_similarity:
                scored.append((score, mapping))
        scored.sort(key=lambda item: (-item[0], item[1].intent_signature, item[1].subtask_type))
        return tuple(mapping for _, mapping in scored[:limit])


def _encode_mapping(mapping: ReusableIntentRouteMapping) -> dict[str, object]:
    return {
        "intent_signature": list(mapping.intent_signature),
        "subtask_type": mapping.subtask_type,
        "capability_ids": list(mapping.capability_ids),
        "schema_version": mapping.schema_version,
        "judge_name": mapping.judge_name,
        "judge_version": mapping.judge_version,
        "prompt_version": mapping.prompt_version,
    }


def _decode_mapping(line: str) -> ReusableIntentRouteMapping | None:
    try:
        raw = json.loads(line)
    except json.JSONDecodeError:
        return None
    if not isinstance(raw, Mapping):
        return None
    signature = _string_tuple(raw.get("intent_signature"))
    capability_ids = _string_tuple(raw.get("capability_ids"))
    scalar_names = (
        "subtask_type",
        "schema_version",
        "judge_name",
        "judge_version",
        "prompt_version",
    )
    scalars = {name: raw.get(name) for name in scalar_names}
    subtask_type = scalars["subtask_type"]
    if (
        signature is None
        or not signature
        or capability_ids is None
        or any(not isinstance(value, str) or not value.strip() for value in scalars.values())
        or any(_SAFE_SIGNATURE_TOKEN.fullmatch(token) is None for token in signature)
        or not isinstance(subtask_type, str)
        or _SAFE_SIGNATURE_TOKEN.fullmatch(subtask_type) is None
    ):
        return None
    return ReusableIntentRouteMapping(
        intent_signature=signature,
        subtask_type=str(scalars["subtask_type"]),
        capability_ids=capability_ids,
        schema_version=str(scalars["schema_version"]),
        judge_name=str(scalars["judge_name"]),
        judge_version=str(scalars["judge_version"]),
        prompt_version=str(scalars["prompt_version"]),
    )


def _string_tuple(value: object) -> tuple[str, ...] | None:
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item.strip() for item in value
    ):
        return None
    return tuple(item.strip() for item in value)
