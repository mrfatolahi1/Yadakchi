from __future__ import annotations

from collections import defaultdict

from search.models import SynonymDecision
from search.text import normalize_text


def approved_synonyms(part_type: str | None = None) -> dict[str, list[str]]:
    queryset = SynonymDecision.objects.filter(active=True, decision="approve")
    if part_type is not None:
        queryset = queryset.filter(part_type=part_type)
    grouped: defaultdict[str, set[str]] = defaultdict(set)
    for decision in queryset.only("part_type", "token"):
        token = normalize_text(decision.token)
        if decision.part_type and token:
            grouped[decision.part_type].add(token)
    return {key: sorted(values) for key, values in grouped.items()}
