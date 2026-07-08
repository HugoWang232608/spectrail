from __future__ import annotations

from spectrail.core.models import RequirementIR


def normalize_requirement(requirement: RequirementIR) -> RequirementIR:
    requirement.statement = requirement.statement.strip()
    requirement.ears_pattern = requirement.ears_pattern or "unknown"
    requirement.verification_method = requirement.verification_method or "unknown"
    return requirement


def normalize_requirements(requirements: list[RequirementIR]) -> list[RequirementIR]:
    return [normalize_requirement(requirement) for requirement in requirements]
