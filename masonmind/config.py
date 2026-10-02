"""Load independent configuration snapshots."""
from pathlib import Path
from typing import Any
import yaml

ROOT = Path(__file__).resolve().parents[1]


def load_config(name: str = "default", path: Path | None = None) -> dict[str, Any]:
    """Load YAML from an explicit path or the repository defaults."""
    source = path or ROOT / "configs" / f"{name}.yaml"
    if not source.exists() and path is None:
        source = Path(__file__).parent / "defaults" / f"{name}.yaml"
    with source.open(encoding="utf-8") as stream:
        result = yaml.safe_load(stream)
    if not isinstance(result, dict):
        raise ValueError(f"Configuration must be a mapping: {source}")
    return result
