"""
Common JSON file I/O for FinMan.
"""

import json
from pathlib import Path

def read_json(path: str | Path, default=None):
    """Read and parse a JSON file."""
    path = Path(path)

    if not path.exists():
        return default

    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def write_json(path: str | Path, data, indent=2):
    """Write data to a JSON file."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    path.write_text(
        json.dumps(data, indent=indent),
        encoding="utf-8",
    )

