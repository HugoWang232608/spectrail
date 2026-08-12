from __future__ import annotations

import json
import sys
from pathlib import Path
from zipfile import ZipFile


EXPECTED_AGENT_FIXTURES = {
    "sample_srs_agent.json",
    "sample_srs_agent_failure_retry_full.json",
    "sample_srs_agent_full.json",
    "sample_srs_agent_needs_human_full.json",
    "sample_srs_api_agent_full.json",
    "sample_srs_replan_agent.json",
}
WHEEL_FIXTURE_PREFIX = "spectrail/fixtures/agent/"


def verify_wheel(path: Path) -> None:
    with ZipFile(path) as wheel:
        fixture_entries = {
            name.removeprefix(WHEEL_FIXTURE_PREFIX): name
            for name in wheel.namelist()
            if name.startswith(WHEEL_FIXTURE_PREFIX) and name.endswith(".json")
        }
        if set(fixture_entries) != EXPECTED_AGENT_FIXTURES:
            raise ValueError(
                "wheel Agent fixture set differs: "
                f"expected={sorted(EXPECTED_AGENT_FIXTURES)}, "
                f"actual={sorted(fixture_entries)}"
            )
        for filename, entry in fixture_entries.items():
            payload = json.loads(wheel.read(entry))
            if payload.get("schema_version") != "agent_planner_fixture_v1":
                raise ValueError(
                    f"wheel Agent fixture schema differs: {filename}"
                )


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        raise SystemExit("usage: verify_agent_fixture_wheel.py WHEEL")
    verify_wheel(Path(argv[0]))
    print(f"Verified {len(EXPECTED_AGENT_FIXTURES)} bundled Agent fixtures")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
