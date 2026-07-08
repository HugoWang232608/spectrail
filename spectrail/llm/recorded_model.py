from .mock_model import MockModel


class RecordedModel(MockModel):
    def __init__(self, fixture_path: str = "fixtures/recorded_reqir_response.json") -> None:
        super().__init__(fixture_path)
