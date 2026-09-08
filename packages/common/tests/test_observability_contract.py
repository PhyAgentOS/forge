from __future__ import annotations

import ast
import json
import os
import subprocess
import sys

from pathlib import Path

import pytest

COMMON_ROOT = Path(__file__).parents[1]

COMMON_SRC = COMMON_ROOT / "src"
sys.path.insert(0, str(COMMON_SRC))


@pytest.mark.parametrize("module", ["metrics.py", "observability.py"])
def test_python_311_syntax(module) -> None:
    path = COMMON_SRC / "forge_common" / module
    ast.parse(
        path.read_text(encoding="utf-8"), filename=str(path), feature_version=(3, 11)
    )


def test_stdlib_only_import_and_snapshot_in_isolated_python() -> None:
    env = dict(os.environ, PYTHONPATH=str(COMMON_SRC), PYTHONDONTWRITEBYTECODE="1")
    result = subprocess.run(
        [
            sys.executable,
            "-S",
            "-c",
            "import json, sys; "
            "from forge_common.observability import Observer; "
            "from forge_common.metrics import BoundedMetricsSink; "
            "sink = BoundedMetricsSink(); Observer(sink=sink); "
            "assert not any(name in sys.modules for name in "
            "('dora', 'pyarrow', 'numpy', 'opentelemetry')); "
            "print(json.dumps(sink.snapshot(), allow_nan=False))",
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
        check=True,
    )
    snapshot = json.loads(result.stdout)
    assert snapshot["histograms"] == []
    assert snapshot["counters"] == []
