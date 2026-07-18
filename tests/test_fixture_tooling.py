from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys

import pytest


pytest.importorskip("pypdf")

SCRIPT = (
    Path(__file__).parent
    / "fixtures"
    / "build_pdf_merged_table_libreoffice_fixture.py"
)
SPEC = importlib.util.spec_from_file_location(
    "spectrail_libreoffice_fixture_tooling",
    SCRIPT,
)
assert SPEC is not None
assert SPEC.loader is not None
fixture_tooling = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = fixture_tooling
SPEC.loader.exec_module(fixture_tooling)


def _write_locked_manifest(
    path: Path,
    toolchain: dict[str, dict[str, str]],
) -> None:
    path.write_text(
        json.dumps(
            {
                "schema_version": "pdf_fixture_manifest_v2",
                **toolchain,
            }
        ),
        encoding="utf-8",
    )


def _toolchain(identity: str = "LibreOffice 1.0"):
    return {
        "content_producer": {
            "name": "LibreOffice",
            "identity": identity,
        },
        "source_builder": {
            "name": "python-docx",
            "version": "1.2.0",
        },
        "metadata_normalizer": {
            "name": "pypdf",
            "version": "6.10.0",
        },
    }


def test_fixture_toolchain_guard_accepts_matching_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
):
    manifest = tmp_path / "manifest.json"
    toolchain = _toolchain()
    _write_locked_manifest(manifest, toolchain)
    monkeypatch.setattr(fixture_tooling, "MANIFEST", manifest)
    monkeypatch.delenv(
        fixture_tooling.TOOLCHAIN_CHANGE_ENV,
        raising=False,
    )

    fixture_tooling._check_locked_toolchain(toolchain)

    assert "Fixture toolchain:" in capsys.readouterr().out


def test_fixture_toolchain_guard_rejects_mismatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    manifest = tmp_path / "manifest.json"
    _write_locked_manifest(manifest, _toolchain())
    monkeypatch.setattr(fixture_tooling, "MANIFEST", manifest)
    monkeypatch.delenv(
        fixture_tooling.TOOLCHAIN_CHANGE_ENV,
        raising=False,
    )

    with pytest.raises(RuntimeError, match="FIXTURE_TOOLCHAIN_MISMATCH"):
        fixture_tooling._check_locked_toolchain(
            _toolchain("LibreOffice 2.0")
        )


def test_fixture_toolchain_guard_allows_explicit_migration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
):
    manifest = tmp_path / "manifest.json"
    _write_locked_manifest(manifest, _toolchain())
    monkeypatch.setattr(fixture_tooling, "MANIFEST", manifest)
    monkeypatch.setenv(fixture_tooling.TOOLCHAIN_CHANGE_ENV, "1")

    fixture_tooling._check_locked_toolchain(
        _toolchain("LibreOffice 2.0")
    )

    assert "Accepting intentional fixture toolchain change" in (
        capsys.readouterr().out
    )
