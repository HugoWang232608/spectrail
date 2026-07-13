from __future__ import annotations


class ModelError(ValueError):
    pass


class ModelResponseParseError(ModelError):
    pass


class ModelPayloadContractError(ModelError):
    pass


class ModelConfigurationError(ModelError):
    pass


class ModelProviderError(ModelError):
    pass
