from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from spectrail.core.models import RequirementIR
from spectrail.evaluation.models import GoldRequirement, GoldSource
from spectrail.validators.source_quote_validator import normalize_text


MATCHING_ALGORITHM = "deterministic_bipartite_v1"


@dataclass(frozen=True)
class MatchPair:
    candidate_index: int
    gold_index: int
    candidate_id: str
    gold_id: str
    source_index: int
    quote_match: str
    statement_match: str | None
    quality: int


@dataclass(frozen=True)
class EvaluationMatches:
    source_alignment_matches: list[MatchPair]
    requirement_exact_matches: list[MatchPair]
    ambiguous_optimum_count: int = 0
    algorithm: str = MATCHING_ALGORITHM


@dataclass(frozen=True)
class _EdgeCandidate:
    candidate_index: int
    gold_index: int
    source_index: int
    quote_match: str
    statement_match: str | None
    quality: int


def match_requirements(
    candidates: list[RequirementIR],
    gold: list[GoldRequirement],
    *,
    scope_block_ids: Iterable[str] = (),
) -> EvaluationMatches:
    scope = set(scope_block_ids)
    indexed_candidates = [
        (index, candidate)
        for index, candidate in enumerate(candidates)
        if not scope or any(source.block_id in scope for source in candidate.sources)
    ]
    indexed_candidates.sort(key=lambda item: (item[1].metadata.get("candidate_key", ""), item[1].id))
    indexed_gold = sorted(enumerate(gold), key=lambda item: item[1].gold_id)

    source_edges: list[_EdgeCandidate] = []
    exact_edges: list[_EdgeCandidate] = []
    for local_candidate_index, (_, candidate) in enumerate(indexed_candidates):
        for local_gold_index, (_, gold_item) in enumerate(indexed_gold):
            source_edge = _best_source_edge(local_candidate_index, local_gold_index, candidate, gold_item)
            if source_edge is None:
                continue
            source_edges.append(source_edge)
            statement_match = _statement_match(candidate.statement, gold_item)
            if statement_match is not None:
                exact_edges.append(
                    _EdgeCandidate(
                        candidate_index=local_candidate_index,
                        gold_index=local_gold_index,
                        source_index=source_edge.source_index,
                        quote_match=source_edge.quote_match,
                        statement_match=statement_match,
                        quality=source_edge.quality + (40 if statement_match == "primary" else 30),
                    )
                )

    source_pairs = _maximum_weight_matching(source_edges, len(indexed_candidates), len(indexed_gold))
    exact_pairs = _maximum_weight_matching(exact_edges, len(indexed_candidates), len(indexed_gold))
    ambiguous = _ambiguous_edge_count(source_edges) + _ambiguous_edge_count(exact_edges)

    def materialize(edges: list[_EdgeCandidate]) -> list[MatchPair]:
        result = []
        for edge in edges:
            original_candidate_index, candidate = indexed_candidates[edge.candidate_index]
            original_gold_index, gold_item = indexed_gold[edge.gold_index]
            result.append(
                MatchPair(
                    candidate_index=original_candidate_index,
                    gold_index=original_gold_index,
                    candidate_id=candidate.id,
                    gold_id=gold_item.gold_id,
                    source_index=edge.source_index,
                    quote_match=edge.quote_match,
                    statement_match=edge.statement_match,
                    quality=edge.quality,
                )
            )
        return sorted(result, key=lambda pair: (pair.gold_id, pair.candidate_id, pair.source_index))

    return EvaluationMatches(
        source_alignment_matches=materialize(source_pairs),
        requirement_exact_matches=materialize(exact_pairs),
        ambiguous_optimum_count=ambiguous,
    )


def _best_source_edge(
    candidate_index: int,
    gold_index: int,
    candidate: RequirementIR,
    gold: GoldRequirement,
) -> _EdgeCandidate | None:
    best: _EdgeCandidate | None = None
    for source_index, source in enumerate(candidate.sources):
        for gold_source in gold.sources:
            quote_match = _source_match(source.block_id, source.quote, gold_source)
            if quote_match is None:
                continue
            quality = (20 if quote_match == "exact" else 10) + max(0, 9 - source_index)
            edge = _EdgeCandidate(candidate_index, gold_index, source_index, quote_match, None, quality)
            if best is None or (edge.quality, -edge.source_index) > (best.quality, -best.source_index):
                best = edge
    return best


def _source_match(block_id: str, quote: str, gold_source: GoldSource) -> str | None:
    if block_id != gold_source.block_id:
        return None
    candidate_quote = normalize_text(quote)
    expected_quote = normalize_text(gold_source.quote)
    if candidate_quote == expected_quote:
        return "exact"
    if candidate_quote and expected_quote and (
        candidate_quote in expected_quote or expected_quote in candidate_quote
    ):
        return "containment"
    return None


def _statement_match(statement: str, gold: GoldRequirement) -> str | None:
    normalized = normalize_text(statement)
    if normalized == normalize_text(gold.statement):
        return "primary"
    if any(normalized == normalize_text(alias) for alias in gold.accepted_statements):
        return "accepted"
    return None


@dataclass
class _FlowEdge:
    to: int
    rev: int
    capacity: int
    cost: int
    payload: _EdgeCandidate | None = None


def _maximum_weight_matching(
    edges: list[_EdgeCandidate], candidate_count: int, gold_count: int
) -> list[_EdgeCandidate]:
    if not edges:
        return []
    source = 0
    candidate_base = 1
    gold_base = candidate_base + candidate_count
    sink = gold_base + gold_count
    graph: list[list[_FlowEdge]] = [[] for _ in range(sink + 1)]

    def add_edge(start: int, end: int, capacity: int, cost: int, payload=None) -> None:
        forward = _FlowEdge(end, len(graph[end]), capacity, cost, payload)
        reverse = _FlowEdge(start, len(graph[start]), 0, -cost, None)
        graph[start].append(forward)
        graph[end].append(reverse)

    for index in range(candidate_count):
        add_edge(source, candidate_base + index, 1, 0)
    for index in range(gold_count):
        add_edge(gold_base + index, sink, 1, 0)

    stable_edges = sorted(edges, key=lambda edge: (edge.candidate_index, edge.gold_index, edge.source_index))
    for rank, edge in enumerate(stable_edges):
        # Flow is always maximized first. Cost then maximizes quality, while the
        # stable rank makes equal-quality traversals independent of input order.
        cost = -(edge.quality * 1_000_000) + rank
        add_edge(candidate_base + edge.candidate_index, gold_base + edge.gold_index, 1, cost, edge)

    while True:
        infinity = 10**30
        distance = [infinity] * len(graph)
        previous: list[tuple[int, int] | None] = [None] * len(graph)
        distance[source] = 0
        for _ in range(len(graph) - 1):
            changed = False
            for node, outgoing in enumerate(graph):
                if distance[node] == infinity:
                    continue
                for edge_index, edge in enumerate(outgoing):
                    if edge.capacity <= 0:
                        continue
                    candidate_distance = distance[node] + edge.cost
                    if candidate_distance < distance[edge.to]:
                        distance[edge.to] = candidate_distance
                        previous[edge.to] = (node, edge_index)
                        changed = True
            if not changed:
                break
        if previous[sink] is None:
            break
        node = sink
        while node != source:
            previous_node, edge_index = previous[node]  # type: ignore[misc]
            edge = graph[previous_node][edge_index]
            edge.capacity -= 1
            graph[node][edge.rev].capacity += 1
            node = previous_node

    selected: list[_EdgeCandidate] = []
    for candidate_index in range(candidate_count):
        for edge in graph[candidate_base + candidate_index]:
            if edge.payload is not None and edge.capacity == 0:
                selected.append(edge.payload)
    return selected


def _ambiguous_edge_count(edges: list[_EdgeCandidate]) -> int:
    by_candidate: dict[int, list[_EdgeCandidate]] = {}
    for edge in edges:
        by_candidate.setdefault(edge.candidate_index, []).append(edge)
    return sum(
        1
        for candidate_edges in by_candidate.values()
        if len([edge for edge in candidate_edges if edge.quality == max(item.quality for item in candidate_edges)]) > 1
    )
