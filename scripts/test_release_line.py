"""Exercise the release boundary without installing or starting a robot node."""

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from check_release_line import validate


class ReleaseLineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        old = Path.cwd()
        os.chdir(self.root)
        self.addCleanup(os.chdir, old)
        self.git("init", "-b", "main")
        self.git("config", "user.name", "Release test")
        self.git("config", "user.email", "release-test@example.invalid")
        self.write(1, "1.0.0")
        self.git("add", ".")
        self.git("commit", "-qm", "legacy line")
        self.git("tag", "-a", "v1.0.0", "-m", "legacy")
        self.git("update-ref", "refs/remotes/origin/stable/1.x", "HEAD")
        self.legacy = self.git("rev-parse", "HEAD")
        self.write(2, "2.0.0")
        self.git("add", ".")
        self.git("commit", "-qm", "current line")
        self.git("tag", "-a", "v2.0.0", "-m", "current")
        self.git("update-ref", "refs/remotes/origin/main", "HEAD")

    def git(self, *args):
        return subprocess.check_output(
            ["git", *args], text=True, stderr=subprocess.DEVNULL
        ).strip()

    def write(self, major, version):
        (self.root / "pyproject.toml").write_text(
            f'[project]\nname="node"\nversion="{version}"\n[build-system]\nrequires=["hatchling"]\nbuild-backend="hatchling.build"\n'
        )
        dora = "0.4.1" if major == 1 else "1.0.1"
        (self.root / "uv.lock").write_text(
            f'[[package]]\nname="forge-msgs"\nversion="{major}.0.0"\n[[package]]\nname="dora-rs"\nversion="{dora}"\n'
        )

    def test_current_line(self):
        self.assertEqual(
            validate(self.root, "2.x", "v2.0.0", "main")["package_kind"], "python"
        )

    def test_legacy_line(self):
        self.git("checkout", "-q", "v1.0.0")
        self.assertEqual(
            validate(self.root, "1.x", "v1.0.0", "main")["version"], "1.0.0"
        )

    def test_wrong_ancestry(self):
        with self.assertRaisesRegex(ValueError, "belong to stable"):
            validate(self.root, "1.x", "v2.0.0", "main")

    def test_incomplete_migration(self):
        self.git("checkout", "-q", "v1.0.0")
        with self.assertRaisesRegex(ValueError, "requires node/package version 2.x"):
            validate(self.root, "2.x", "v1.0.0", "main")

    def test_zero_version_needs_normalization(self):
        self.git("checkout", "-q", "v1.0.0")
        self.write(1, "0.1.0")
        self.git("add", ".")
        self.git("commit", "-qm", "legacy zero version")
        self.git("tag", "-a", "v0.1.0", "-m", "zero")
        self.git("update-ref", "refs/remotes/origin/stable/1.x", "HEAD")
        with self.assertRaisesRegex(ValueError, "normalize legacy 0.x"):
            validate(self.root, "1.x", "v0.1.0", "main")

    def test_unrelated_tag(self):
        self.git("checkout", "--orphan", "unrelated")
        self.git("commit", "-qm", "unreviewed source")
        self.git("tag", "-a", "v2.0.1", "-m", "unrelated")
        with self.assertRaisesRegex(ValueError, "belong to main"):
            validate(self.root, "2.x", "v2.0.1", "main")

    def test_lightweight_tag(self):
        self.git("tag", "v2.0.1")
        with self.assertRaisesRegex(ValueError, "annotated"):
            validate(self.root, "2.x", "v2.0.1", "main")

    def test_wrong_checkout(self):
        with self.assertRaisesRegex(ValueError, "Checkout differs"):
            validate(self.root, "2.x", "v1.0.0", "main")

    def test_version_mismatch(self):
        with self.assertRaisesRegex(ValueError, "requested version"):
            validate(self.root, "2.x", "v2.0.0", "main", "2.0.1")
        (self.root / "pyproject.toml").write_text('[project]\nversion="2.0.1"\n')
        with self.assertRaisesRegex(ValueError, "source metadata"):
            validate(self.root, "2.x", "v2.0.0", "main")

    def test_wrong_dora_and_missing_forge(self):
        (self.root / "uv.lock").write_text(
            '[[package]]\nname="forge-msgs"\nversion="2.0.0"\n[[package]]\nname="dora-rs"\nversion="0.4.1"\n'
        )
        with self.assertRaisesRegex(ValueError, "requires Dora"):
            validate(self.root, "2.x", "v2.0.0", "main")
        (self.root / "uv.lock").write_text("package=[]\n")
        with self.assertRaisesRegex(ValueError, "resolved Forge"):
            validate(self.root, "2.x", "v2.0.0", "main")

    def test_rust_metadata(self):
        (self.root / "pyproject.toml").unlink()
        (self.root / "Cargo.toml").write_text(
            '[package]\nname="node"\nversion="2.0.0"\n'
        )
        (self.root / "Cargo.lock").write_text(
            '[[package]]\nname="forge_msgs"\nversion="2.0.0"\n[[package]]\nname="dora-node-api"\nversion="1.0.1"\n'
        )
        self.assertEqual(
            validate(self.root, "2.x", "v2.0.0", "main")["package_kind"], "source"
        )

    def test_core_family(self):
        path = self.root / "packages/msgs"
        path.mkdir(parents=True)
        (path / "pyproject.toml").write_text('[project]\nversion="2.1.0"\n')
        (self.root / "versions.toml").write_text('[packages]\nmsgs="2.1.0"\n')
        self.git("tag", "-a", "forge-msgs-v2.1.0", "-m", "msgs")
        self.assertEqual(
            validate(self.root, "2.x", "forge-msgs-v2.1.0", "main")["package_kind"],
            "family",
        )
        with self.assertRaisesRegex(ValueError, "Use an existing"):
            validate(self.root, "2.x", "forge-unknown-v2.1.0", "main")


if __name__ == "__main__":
    unittest.main()
