from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import urllib.request


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "manifest",
        nargs="?",
        default="eval/real_world_agent_v1/manifest.json",
    )
    parser.add_argument(
        "--output",
        default="outputs/real-world-agent-v1/corpus",
    )
    args = parser.parse_args()

    manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    if manifest.get("schema_version") != "real_world_agent_corpus_v1":
        raise ValueError("unsupported real-world corpus schema")
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)

    for document in manifest["documents"]:
        target = output / document["filename"]
        if target.is_file() and _sha256(target) == document["sha256"]:
            print(f"Verified {target}")
            continue
        request = urllib.request.Request(
            document["source_url"],
            headers={"User-Agent": "SpecTrail-real-world-agent-evaluation/1"},
        )
        with urllib.request.urlopen(request, timeout=120) as response:
            payload = response.read()
        digest = hashlib.sha256(payload).hexdigest()
        if digest != document["sha256"]:
            raise ValueError(
                f"download hash differs for {document['case_id']}: {digest}"
            )
        target.write_bytes(payload)
        print(f"Downloaded {target}")
    return 0


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


if __name__ == "__main__":
    raise SystemExit(main())
