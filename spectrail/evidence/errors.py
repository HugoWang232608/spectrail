class EvidenceReferenceError(ValueError):
    """A source refers to evidence identities that are invalid for its block."""


class LocatorDerivationError(ValueError):
    """Valid evidence identities cannot be mapped to the requested locator."""
