import json
from pathlib import Path

from spectrail import __version__
from spectrail.api.app import create_app


def test_v091_release_versions_are_synchronized():
    pyproject = Path("pyproject.toml").read_text(encoding="utf-8")
    frontend = json.loads(
        Path("frontend/package.json").read_text(encoding="utf-8")
    )

    assert __version__ == "0.9.1"
    assert 'version = "0.9.1"' in pyproject
    assert frontend["version"] == "0.9.1"
    assert create_app().version == "0.9.1"
