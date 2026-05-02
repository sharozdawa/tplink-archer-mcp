"""Convert tplinkrouterc6u dataclass / Status objects to plain JSON-friendly dicts."""
from __future__ import annotations

from dataclasses import asdict, is_dataclass
from enum import Enum
from typing import Any


def to_jsonable(obj: Any) -> Any:
    """Recursively convert a tplinkrouterc6u return value into JSON-serializable types."""
    if obj is None or isinstance(obj, (bool, int, float, str)):
        return obj
    if isinstance(obj, Enum):
        return obj.value if isinstance(obj.value, (str, int, float, bool)) else obj.name
    if is_dataclass(obj):
        return to_jsonable(asdict(obj))
    if isinstance(obj, dict):
        return {str(k): to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [to_jsonable(x) for x in obj]
    # Last resort: pull __dict__ if available
    if hasattr(obj, "__dict__"):
        return to_jsonable(vars(obj))
    return str(obj)
