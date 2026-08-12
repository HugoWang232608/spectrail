from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path


def _run(*arguments: str, cwd: Path) -> None:
    subprocess.run(
        [sys.executable, "-m", "spectrail", *arguments],
        cwd=cwd,
        check=True,
    )


def _json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _request_json(
    url: str,
    *,
    method: str = "GET",
    payload: dict | None = None,
    content_type: str = "application/json",
    raw_body: bytes | None = None,
) -> dict:
    body = raw_body
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        method=method,
        headers={"Content-Type": content_type},
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))


def _upload_document(base_url: str, task_id: str, document: Path) -> dict:
    boundary = "spectrail-release-smoke-boundary"
    body = (
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="file"; '
        f'filename="{document.name}"\r\n'
        "Content-Type: text/markdown\r\n\r\n"
    ).encode("utf-8")
    body += document.read_bytes()
    body += f"\r\n--{boundary}--\r\n".encode("utf-8")
    return _request_json(
        f"{base_url}/api/tasks/{task_id}/documents",
        method="POST",
        content_type=f"multipart/form-data; boundary={boundary}",
        raw_body=body,
    )


def _wait_for_api(base_url: str) -> None:
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        try:
            if _request_json(f"{base_url}/api/health") == {"status": "ok"}:
                return
        except (OSError, urllib.error.URLError):
            time.sleep(0.1)
    raise RuntimeError("installed-wheel API did not become healthy")


def smoke(document: Path) -> None:
    document = document.resolve(strict=True)
    with tempfile.TemporaryDirectory(prefix="spectrail-wheel-smoke-") as raw:
        root = Path(raw)
        fixed = root / "fixed"
        agent = root / "agent"
        _run(
            "extract",
            str(document),
            "--model-mode",
            "mock",
            "--output",
            str(fixed),
            cwd=root,
        )
        _run(
            "extract",
            str(document),
            "--model-mode",
            "mock",
            "--orchestration-mode",
            "agent",
            "--planner-mode",
            "recorded",
            "--planner-fixture",
            "sample_srs_agent_full.json",
            "--output",
            str(agent),
            cwd=root,
        )
        assert _json(fixed / "run_manifest.json")["status"] == "completed"
        agent_manifest = _json(agent / "run_manifest.json")
        assert agent_manifest["status"] == "completed"
        assert agent_manifest["orchestration"]["mode"] == "agent"

        base_url = "http://127.0.0.1:8765"
        server = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "spectrail.api.app:app",
                "--host",
                "127.0.0.1",
                "--port",
                "8765",
                "--log-level",
                "warning",
            ],
            cwd=root,
        )
        try:
            _wait_for_api(base_url)
            created = _request_json(
                f"{base_url}/api/tasks",
                method="POST",
                payload={"model_mode": "mock"},
            )
            task_id = created["task_id"]
            _upload_document(base_url, task_id, document)
            completed = _request_json(
                f"{base_url}/api/tasks/{task_id}/run",
                method="POST",
            )
            assert completed["status"] == "completed"
        finally:
            server.terminate()
            try:
                server.wait(timeout=5)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait(timeout=5)


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        raise SystemExit("usage: smoke_installed_wheel.py SAMPLE_DOCUMENT")
    smoke(Path(argv[0]))
    print("Installed-wheel fixed, Agent recorded, and API smoke passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
