"""Keep the package's minimum Python syntax covered by the workspace CI."""

import ast
from pathlib import Path

import pytest

import forge_tool

_SOURCE_ROOT = Path(forge_tool.__file__).resolve().parent


@pytest.mark.parametrize(
    "source",
    sorted(_SOURCE_ROOT.rglob("*.py")),
    ids=lambda source: str(source.relative_to(_SOURCE_ROOT)),
)
def test_source_supports_python_311(source: Path) -> None:
    ast.parse(source.read_text(encoding="utf-8"), filename=str(source), feature_version=(3, 11))
