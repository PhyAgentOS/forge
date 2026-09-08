# Releasing Forge Package Families

Forge is a Monorepo with independently versioned logical package families. Implementations of one family across languages remain synchronized, while unrelated families can release at different rates.

## Version source of truth

`versions.toml` records the current family versions:

```toml
[packages]
common = "2.1.0"
msgs = "2.0.0"
robot = "2.0.0"
policy = "2.0.0"
kinematics = "2.0.0"
tool = "2.0.0"

[interfaces]
msgs = 1
```

The root `pyproject.toml` is a virtual uv workspace, not a published `forge` Python distribution. Its project version is workspace tooling metadata and is not a package-family release version.

## Release lines

`master` is the current Forge 2.x development and release line. `stable/1.x` is the
Dora 0.4-compatible maintenance line and accepts only critical, security, and release
fixes. Develop changes on `master`; cherry-pick applicable fixes to `stable/1.x` rather
than merging the newer major line into the older one.

## Family boundaries and tags

| Family | Synchronized implementations | Protected tag pattern |
|---|---|---|
| Common | `packages/common`, `crates/forge_common`, `cpp/forge_common` | `forge-common-v<semver>` |
| Msgs | `interfaces/forge_msgs`, `packages/msgs`, `crates/forge_msgs`, `cpp/forge_msgs` | `forge-msgs-v<semver>` |
| Robot | `packages/robot`, `cpp/forge_robot` | `forge-robot-v<semver>` |
| Policy | `packages/policy` | `forge-policy-v<semver>` |
| Kinematics | `packages/kinematics` | `forge-kinematics-v<semver>` |
| Tool | `interfaces/forge_tool`, `packages/tool` | `forge-tool-v<semver>` |

Do not create separate Python/Rust/C++ tags for one family. `forge-msgs-v2.0.0`, for example, identifies the Msgs 2 language implementations at the tagged commit. Package majors and wire-schema versions are tracked independently because a language API can break while the cross-language schema remains compatible.

Do not use or move historical generic tags such as `v1.0.0`. They belong to an earlier repository layout. All new release tags must use a family namespace.

The initial five `1.0.0` tags may point to the same clean commit. Tool's first release is `forge-tool-v0.1.0`. Later releases can be independent, for example:

```text
forge-common-v2.1.0
forge-msgs-v2.0.0
forge-robot-v2.0.0
forge-policy-v2.0.0
forge-kinematics-v2.0.0
forge-tool-v2.0.0
```

## Downstream dependencies

Use the tag for the exact family being consumed:

```toml
[tool.uv.sources]
forge-msgs = { git = "https://gitlab.ex-ai.cn/PhyAgentOS/framework/forge.git", tag = "forge-msgs-v2.0.0", subdirectory = "packages/msgs" }
forge-common = { git = "https://gitlab.ex-ai.cn/PhyAgentOS/framework/forge.git", tag = "forge-common-v2.1.0", subdirectory = "packages/common" }
```

A downstream `uv.lock` or `Cargo.lock` records the exact commit resolved from each protected tag. Protected release tags must never be moved.

## Changing a family version

1. Change only the affected family in `versions.toml`.
2. Update every language implementation belonging to that family.
3. Update compatible internal dependency constraints only when their supported range changes.
4. Regenerate `uv.lock` and/or `Cargo.lock` as applicable.
5. Add a family-specific entry to `CHANGELOG.md`.
6. Run the version gate and relevant tests before review.

When one family introduces a dependency whose minimum version is being released from
the same revision, publish the dependency family first. For the coordinated 2.0 migration,
`forge-msgs 2.0.0` must be available before publishing `forge-tool 2.0.0`,
`forge-policy 2.0.0`, or `forge-robot 2.0.0`.

`./scripts/check_release_versions.py` verifies family synchronization, workspace membership, internal Python requirements, uv/Cargo locks, and the independently declared Msgs interface version. In GitLab and GitHub tag pipelines it also reads `CI_COMMIT_TAG` or `GITHUB_REF_NAME` and rejects unknown families, non-SemVer tags, and tags whose version differs from `versions.toml`.

A tag can be checked locally before creation:

```bash
./scripts/check_release_versions.py --tag forge-msgs-v2.0.0
```

## Release requirements

Before creating any family tag:

1. `versions.toml`, all implementations in that family, internal requirements, and lock files are aligned.
2. `CHANGELOG.md` identifies the family version and release date.
3. The repository working tree is clean.
4. Relevant Python, Rust, C++, and cross-language interoperability tests pass.
5. Affected Python distributions and Rust packages build from the clean revision.
6. The exact protected GitLab tag does not already exist.

For a coordinated initial baseline, run the complete repository validation and create all five tags only after it passes.

## Validation

From the repository root:

```bash
./scripts/check_release_versions.py
uv lock --check
cargo check --workspace --locked

uv sync --locked --all-packages --all-extras --dev
uv run ruff check .
uv run python scripts/check_forge_msgs_schema.py
uv run pytest \
  packages/common/tests \
  packages/msgs/tests \
  packages/policy/tests \
  packages/robot/tests \
  packages/kinematics/tests \
  packages/tool/tests

cargo test --workspace --locked
```

Build language packages:

```bash
# Local validation of all six Python packages; do not publish these artifacts.
uv run python scripts/build_python_distributions.py

# Final candidate for one affected family, from a clean commit.
uv run python scripts/build_python_distributions.py --release-family msgs

cargo package --workspace --locked
```

The Python build entry point cleans `dist/release/python`, checks package-family
versions, validates wheel and sdist identity, Apache-2.0 metadata and exact
license layout, runs the additional Kinematics checks when applicable, and
generates `dist/release/python/SHA256SUMS`. Each package-local `LICENSE` must
exactly match the repository root `LICENSE`; this duplication is required so
independently built source distributions carry the complete license text.

`--release-family` builds only that family's Python package and requires a clean
working tree. For a local pre-tag candidate, the expected protected tag must not
exist. In a supported family tag pipeline, `CI_COMMIT_TAG` or the GitHub tag
environment selects release-family mode automatically; that tag must exist and
point to the checked-out `HEAD`.

Configure, build, and test C++ with the same Python environment used for PyArrow:

```bash
cmake -S cpp/forge_common -B build/release/forge_common \
  -DFORGE_COMMON_CPP_BUILD_TESTS=ON
cmake --build build/release/forge_common
ctest --test-dir build/release/forge_common --output-on-failure

cmake -S cpp/forge_msgs -B build/release/forge_msgs \
  -DFORGE_MSGS_CPP_BUILD_TESTS=ON \
  -DPython3_EXECUTABLE="$PWD/.venv/bin/python"
cmake --build build/release/forge_msgs
ctest --test-dir build/release/forge_msgs --output-on-failure

cmake -S cpp/forge_robot -B build/release/forge_robot \
  -DFORGE_ROBOT_CPP_BUILD_TESTS=ON \
  -DPython3_EXECUTABLE="$PWD/.venv/bin/python"
cmake --build build/release/forge_robot
ctest --test-dir build/release/forge_robot --output-on-failure
```

`cargo package --allow-dirty` may be used while preparing a release candidate. Final package verification and tag creation must use a clean working tree without `--allow-dirty`.

## Publishing Rust crates to crates.io

The public distribution names and Rust library targets are:

| Family | crates.io distribution | Rust library target |
|---|---|---|
| Common | `forgelab_common` | `forge_common` |
| Msgs | `forge_msgs` | `forge_msgs` |

The Common distribution uses the `forgelab_` prefix because the crates.io
`forge_common`/`forge-common` namespace is owned by an unrelated project. The
explicit `forge_common` library target preserves existing Rust imports. Each
crate-local `LICENSE` must exactly match the repository root Apache-2.0 license
so the published archive carries the complete license text.

Validate each crate before uploading from the clean release revision:

```bash
cargo publish -p forgelab_common --locked --dry-run
cargo publish -p forge_msgs --locked --dry-run
```

For local publishing, authenticate with `cargo login`, then publish only the
crate belonging to the release family:

```bash
cargo publish -p forgelab_common --locked
cargo publish -p forge_msgs --locked
```

Never store a crates.io token in the repository. crates.io does not permit
replacing an uploaded version; increment the affected family version after an
incorrect upload rather than attempting to reuse it.

## Publishing Python distributions to PyPI

Publish only artifacts produced by a clean, single-family release build. Use a
separate output directory for each family because every build cleans its output
directory:

```bash
uv run python scripts/build_python_distributions.py \
  --release-family common --out-dir dist/release/python/common
uv run python scripts/build_python_distributions.py \
  --release-family msgs --out-dir dist/release/python/msgs
uv run python scripts/build_python_distributions.py \
  --release-family tool --out-dir dist/release/python/tool
uv run python scripts/build_python_distributions.py \
  --release-family kinematics --out-dir dist/release/python/kinematics
uv run python scripts/build_python_distributions.py \
  --release-family policy --out-dir dist/release/python/policy
uv run python scripts/build_python_distributions.py \
  --release-family robot --out-dir dist/release/python/robot
```

Pass only the wheel and source archive; `SHA256SUMS` is release metadata and
must not be uploaded as a Python distribution. Configure authentication before
the dry run because `uv publish --dry-run` still validates authentication
parameters:

```bash
uv publish --dry-run \
  dist/release/python/common/*.whl \
  dist/release/python/common/*.tar.gz
```

Prefer PyPI trusted publishing. The GitHub workflow
`.github/workflows/publish-pypi.yml` requests a short-lived OIDC credential and
does not require a stored PyPI token. Configure one `pypi-<family>` GitHub
Environment per package family and add the matching trusted publisher to each
PyPI project (or as a pending publisher before the first upload). For Msgs:

```text
Owner: Forgelab-Robotics
Repository: forge
Workflow: publish-pypi.yml
Environment: pypi-msgs
```

Existing release tags can be published with `workflow_dispatch` by entering the
full immutable family tag. Future `forge-*-v*` tag pushes trigger the same
workflow automatically. In both cases the workflow checks out and verifies the
annotated tag, builds only that family, stores the artifacts for audit, and
publishes only its wheel and source archive.

For token-based local publishing, provide a scoped token through the protected
`UV_PUBLISH_TOKEN` environment variable; never store it in the repository.
Publish in dependency order: `common`, `msgs`, `tool`, `kinematics`, `policy`, then
`robot`. In particular, publish Msgs before Tool because the Tool Dora extra requires the
new carrier version. Verify each exact version from PyPI in a fresh environment before
continuing to dependent packages.

PyPI does not permit replacing an uploaded file or reusing a released version.
If an upload is wrong, increment that family version and create a new immutable
tag rather than attempting to overwrite it.

## GitLab release procedure

After review:

1. Move the affected family subsection from the global `Unreleased` section into a dated release section; leave unrelated family subsections under `Unreleased`.
2. Commit the release changes.
3. Run the required validation again from the clean commit.
4. Create the protected annotated family tag or tags at that exact commit.
5. Push the commit and tags to GitLab.
6. Attach checksums or package registry links to the corresponding GitLab Release if artifacts are uploaded.
7. Update downstream nodes to use the family tags and regenerate their locks.

When one release depends on another version from the same commit, do not batch-push the
tags. Push and publish the dependency tag first, verify the exact package version from
the public registry, and only then push the dependent tag. For the 2.0 migration, publish
`forge-msgs-v2.0.0` before the Tool, Policy, and Robot 2.0 tags.

The initial five `1.0.0` baseline tags have already been created:

```text
forge-common-v1.0.0
forge-msgs-v1.0.0
forge-robot-v1.0.0
forge-policy-v1.0.0
forge-kinematics-v1.0.0
```

Tool's first release uses the separate `forge-tool-v0.1.0` tag. These and all future
release tags are immutable. Never move or reuse one; increment the affected family
version and create a new protected tag instead.
