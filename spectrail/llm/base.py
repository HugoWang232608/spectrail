from __future__ import annotations

from typing import Protocol


class RequirementModel(Protocol):
    def generate(self, document_text: str) -> dict:
        ...
