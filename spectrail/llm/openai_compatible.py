class OpenAICompatibleModel:
    def __init__(self, model_name: str | None = None) -> None:
        self.model_name = model_name

    def generate(self, document_text: str = "") -> dict:
        raise NotImplementedError("live model mode is reserved for P0b and is not part of mock CI")
