"""Exercise the actual release resolver against remote tag API outcomes."""
import os
from pathlib import Path
import subprocess

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize(
    "tag_type,api_status,expected_status",
    [("tag", 0, 0), ("commit", 0, 1), ("", 1, 1)],
    ids=["annotated-tag", "lightweight-tag", "missing-tag"],
)
def test_release_resolver_rejects_missing_or_lightweight_tags(
    tmp_path, tag_type, api_status, expected_status
):
    workflow = yaml.safe_load((ROOT / ".github/workflows/publish-pypi.yml").read_text())
    script = workflow["jobs"]["resolve"]["steps"][0]["run"]
    fake_gh = tmp_path / "gh"
    fake_gh.write_text(
        '#!/bin/sh\nprintf "%s\\n" "$FAKE_TAG_TYPE"\nexit "$FAKE_API_STATUS"\n'
    )
    fake_gh.chmod(0o755)
    output = tmp_path / "output"
    result = subprocess.run(
        ["bash", "-c", script],
        env={
            **os.environ,
            "PATH": str(tmp_path) + os.pathsep + os.environ["PATH"],
            "GITHUB_REPOSITORY": "PhyAgentOS/forge",
            "REQUESTED_TAG": "forge-msgs-v2.1.0",
            "GITHUB_OUTPUT": str(output),
            "GH_TOKEN": "unused-test-token",
            "FAKE_TAG_TYPE": tag_type,
            "FAKE_API_STATUS": str(api_status),
        },
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == expected_status, result.stderr
    if expected_status == 0:
        values = dict(line.split("=", 1) for line in output.read_text().splitlines())
        assert values == {
            "tag": "forge-msgs-v2.1.0",
            "family": "msgs",
            "version": "2.1.0",
            "distribution": "forge-msgs",
        }
    else:
        assert "annotated" in result.stderr
        assert not output.exists()
