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
@pytest.mark.parametrize(
    "requested_tag,selected_line,event",
    [
        ("forge-msgs-v1.3.2", "1.x", "workflow_dispatch"),
        ("forge-msgs-v2.1.1", "2.x", "workflow_dispatch"),
        ("forge-msgs-v1.3.2", "", "push"),
        ("forge-msgs-v2.1.1", "", "push"),
    ],
)
def test_release_resolver_rejects_missing_or_lightweight_tags(
    tmp_path, tag_type, api_status, expected_status, requested_tag, selected_line, event
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
            "REQUESTED_TAG": requested_tag,
            "SELECTED_LINE": selected_line,
            "GITHUB_EVENT_NAME": event,
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
            "tag": requested_tag,
            "family": "msgs",
            "version": requested_tag.removeprefix("forge-msgs-v"),
            "distribution": "forge-msgs",
            "release_line": requested_tag.removeprefix("forge-msgs-v")[0] + ".x",
        }
    else:
        assert "annotated" in result.stderr
        assert not output.exists()


@pytest.mark.parametrize(
    "tag,line,message",
    [
        ("forge-msgs-v2.1.1", "1.x", "Selected line differs"),
        ("forge-msgs-v1.3.2", "2.x", "Selected line differs"),
        ("forge-msgs-v3.0.0", "2.x", "Only Forge 1.x and 2.x"),
    ],
)
def test_manual_release_rejects_wrong_line_before_remote_lookup(tmp_path, tag, line, message):
    workflow = yaml.safe_load((ROOT / ".github/workflows/publish-pypi.yml").read_text())
    fake_gh = tmp_path / "gh"
    fake_gh.write_text("#!/bin/sh\nexit 99\n")
    fake_gh.chmod(0o755)
    result = subprocess.run(
        ["bash", "-c", workflow["jobs"]["resolve"]["steps"][0]["run"]],
        env={
            **os.environ,
            "PATH": str(tmp_path) + os.pathsep + os.environ["PATH"],
            "REQUESTED_TAG": tag,
            "SELECTED_LINE": line,
            "GITHUB_EVENT_NAME": "workflow_dispatch",
        },
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1
    assert message in result.stderr
