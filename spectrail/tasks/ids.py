from __future__ import annotations

import uuid


def new_task_id() -> str:
    return f"task_{uuid.uuid4().hex[:8]}"
