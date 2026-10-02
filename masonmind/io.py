"""Run artifact storage with JSON serialization."""
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4
from pydantic import BaseModel


def json_value(value: Any) -> Any:
    """Serialize schemas without dropping validation metadata."""
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, Path):
        return str(value)
    if hasattr(value, "tolist"):
        return value.tolist()
    raise TypeError(f"Not JSON serializable: {type(value)}")


def save_json(path: Path, value: Any) -> None:
    """Write a human-readable artifact."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, default=json_value, indent=2), encoding="utf-8")


def append_json(path: Path, value: Any) -> None:
    """Append one independently parseable event."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(value, default=json_value) + "\n")


def create_run(root: Path = Path("runs")) -> Path:
    """Allocate a unique run directory even for concurrent invocations."""
    path = root / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "_" + uuid4().hex[:8])
    path.mkdir(parents=True)
    return path
