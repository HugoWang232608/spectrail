from __future__ import annotations

import difflib
import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


PUNCT_MAP = str.maketrans(
    {
        "，": ",",
        "。": ".",
        "：": ":",
        "；": ";",
        "（": "(",
        "）": ")",
        "！": "!",
        "？": "?",
        "、": ",",
        "“": '"',
        "”": '"',
        "‘": "'",
        "’": "'",
    }
)


class QuoteMatchRange(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    start: int
    end: int

    @model_validator(mode="after")
    def validate_range(self) -> "QuoteMatchRange":
        if self.start < 0 or self.end <= self.start:
            raise ValueError("quote match range must be non-empty and half-open")
        return self


class QuoteMatchResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: Literal["NO_MATCH", "UNIQUE_MATCH", "AMBIGUOUS_MATCH"]
    match_basis: Literal["exact", "normalized", "none"]
    original_ranges: list[QuoteMatchRange] = Field(default_factory=list)
    selected_range: QuoteMatchRange | None = None
    provisional_range: QuoteMatchRange | None = None
    score: float = 0.0

    @model_validator(mode="after")
    def validate_contract(self) -> "QuoteMatchResult":
        if not 0 <= self.score <= 1:
            raise ValueError("quote match score must be between 0 and 1")
        stable_ranges = sorted(set((item.start, item.end) for item in self.original_ranges))
        if stable_ranges != [(item.start, item.end) for item in self.original_ranges]:
            raise ValueError("quote match ranges must be unique and stably sorted")
        if self.status == "NO_MATCH":
            if self.match_basis != "none" or self.original_ranges:
                raise ValueError("NO_MATCH cannot contain a match basis or ranges")
            if self.selected_range is not None or self.provisional_range is not None:
                raise ValueError("NO_MATCH cannot select a range")
        elif self.status == "UNIQUE_MATCH":
            if self.match_basis == "none" or len(self.original_ranges) != 1:
                raise ValueError("UNIQUE_MATCH requires one range and a match basis")
            if self.selected_range != self.original_ranges[0]:
                raise ValueError("UNIQUE_MATCH must select its only range")
            if self.provisional_range is not None:
                raise ValueError("UNIQUE_MATCH cannot contain a provisional range")
        else:
            if self.match_basis == "none" or len(self.original_ranges) < 2:
                raise ValueError("AMBIGUOUS_MATCH requires multiple ranges and a match basis")
            if self.selected_range is not None:
                raise ValueError("AMBIGUOUS_MATCH cannot select a validated range")
            if (
                self.provisional_range is not None
                and self.provisional_range not in self.original_ranges
            ):
                raise ValueError("provisional range must be one of the ambiguous ranges")
        return self


class QuoteMatchRegistry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entries: dict[str, QuoteMatchResult] = Field(default_factory=dict)

    def add(self, key: str, result: QuoteMatchResult) -> None:
        existing = self.entries.get(key)
        if existing is not None and existing != result:
            raise ValueError(f"quote match registry collision: {key}")
        self.entries[key] = result

    def require(self, key: str) -> QuoteMatchResult:
        try:
            return self.entries[key]
        except KeyError as exc:
            raise KeyError(f"quote match result not found: {key}") from exc

    def merge(self, other: "QuoteMatchRegistry") -> None:
        for key, result in other.entries.items():
            self.add(key, result)


@dataclass(frozen=True)
class _MappedCharacter:
    value: str
    original_start: int
    original_end: int


@dataclass(frozen=True)
class NormalizedText:
    text: str
    original_ranges: tuple[tuple[int, int], ...]

    def original_range(self, start: int, end: int) -> QuoteMatchRange:
        if start < 0 or end <= start or end > len(self.original_ranges):
            raise ValueError("normalized range is invalid")
        selected = self.original_ranges[start:end]
        return QuoteMatchRange(
            start=min(item[0] for item in selected),
            end=max(item[1] for item in selected),
        )


class QuoteMatcher:
    def match(self, block_text: str, quote: str, *, provisional: bool = False) -> QuoteMatchResult:
        exact = find_all_exact_ranges(block_text, quote)
        if exact:
            return _matched_result(exact, "exact", 1.0, provisional=provisional)

        normalized_block = normalize_with_mapping(block_text)
        normalized_quote = normalize_with_mapping(quote)
        normalized = _find_normalized_ranges(normalized_block, normalized_quote.text)
        if normalized:
            return _matched_result(normalized, "normalized", 0.95, provisional=provisional)

        score = difflib.SequenceMatcher(
            None,
            normalized_quote.text,
            normalized_block.text,
        ).ratio()
        return QuoteMatchResult(
            status="NO_MATCH",
            match_basis="none",
            score=score,
        )


def find_all_exact_ranges(text: str, quote: str) -> list[QuoteMatchRange]:
    if not quote:
        return []
    result: list[QuoteMatchRange] = []
    start = 0
    while start <= len(text) - len(quote):
        found = text.find(quote, start)
        if found < 0:
            break
        result.append(QuoteMatchRange(start=found, end=found + len(quote)))
        start = found + 1
    return result


def find_all_normalized_ranges(block_text: str, quote: str) -> list[QuoteMatchRange]:
    normalized_block = normalize_with_mapping(block_text)
    normalized_quote = normalize_with_mapping(quote)
    return _find_normalized_ranges(normalized_block, normalized_quote.text)


def normalize_with_mapping(text: str) -> NormalizedText:
    mapped = _nfkc_characters(text)
    mapped = [
        _MappedCharacter(
            value=item.value.translate(PUNCT_MAP),
            original_start=item.original_start,
            original_end=item.original_end,
        )
        for item in mapped
    ]
    mapped = _regex_replace(mapped, re.compile(r"\s*\|\s*"), "|")
    mapped = _regex_replace(mapped, re.compile(r"\s+"), " ")
    while mapped and mapped[0].value == " ":
        mapped.pop(0)
    while mapped and mapped[-1].value == " ":
        mapped.pop()
    return NormalizedText(
        text="".join(item.value for item in mapped),
        original_ranges=tuple((item.original_start, item.original_end) for item in mapped),
    )


def normalize_text(text: str) -> str:
    return normalize_with_mapping(text).text


def source_evidence_key(
    *,
    evidence_fingerprint: str,
    document_id: str,
    block_id: str,
    quote: str,
    canonical_cell_ids: list[str] | tuple[str, ...] = (),
) -> str:
    payload = {
        "evidence_fingerprint": evidence_fingerprint,
        "document_id": document_id,
        "block_id": block_id,
        "quote": quote,
        "canonical_cell_ids": list(canonical_cell_ids),
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"src_{hashlib.sha256(encoded).hexdigest()[:24]}"


def _matched_result(
    ranges: list[QuoteMatchRange],
    basis: Literal["exact", "normalized"],
    score: float,
    *,
    provisional: bool,
) -> QuoteMatchResult:
    stable = sorted({(item.start, item.end) for item in ranges})
    items = [QuoteMatchRange(start=start, end=end) for start, end in stable]
    if len(items) == 1:
        return QuoteMatchResult(
            status="UNIQUE_MATCH",
            match_basis=basis,
            original_ranges=items,
            selected_range=items[0],
            score=score,
        )
    return QuoteMatchResult(
        status="AMBIGUOUS_MATCH",
        match_basis=basis,
        original_ranges=items,
        provisional_range=items[0] if provisional else None,
        score=score,
    )


def _find_normalized_ranges(
    normalized_block: NormalizedText,
    normalized_quote: str,
) -> list[QuoteMatchRange]:
    if not normalized_quote:
        return []
    ranges: set[tuple[int, int]] = set()
    start = 0
    while start <= len(normalized_block.text) - len(normalized_quote):
        found = normalized_block.text.find(normalized_quote, start)
        if found < 0:
            break
        original = normalized_block.original_range(found, found + len(normalized_quote))
        ranges.add((original.start, original.end))
        start = found + 1
    return [QuoteMatchRange(start=start, end=end) for start, end in sorted(ranges)]


def _nfkc_characters(text: str) -> list[_MappedCharacter]:
    result: list[_MappedCharacter] = []
    index = 0
    while index < len(text):
        start = index
        index += 1
        while index < len(text) and unicodedata.combining(text[index]):
            index += 1
        normalized = unicodedata.normalize("NFKC", text[start:index])
        for character in normalized:
            result.append(
                _MappedCharacter(
                    value=character,
                    original_start=start,
                    original_end=index,
                )
            )
    return result


def _regex_replace(
    mapped: list[_MappedCharacter],
    pattern: re.Pattern[str],
    replacement: str,
) -> list[_MappedCharacter]:
    text = "".join(item.value for item in mapped)
    result: list[_MappedCharacter] = []
    position = 0
    for match in pattern.finditer(text):
        result.extend(mapped[position : match.start()])
        selected = mapped[match.start() : match.end()]
        if selected:
            start = min(item.original_start for item in selected)
            end = max(item.original_end for item in selected)
            for character in replacement:
                result.append(_MappedCharacter(character, start, end))
        position = match.end()
    result.extend(mapped[position:])
    return result
