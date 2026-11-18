#!/usr/bin/env python3
"""Validate a release tag, its source branch, metadata and Forge/Dora line."""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import tomllib

CORE_PACKAGES = {'forge-common', 'forge-msgs', 'forge-robot', 'forge-policy', 'forge-tool', 'forge-kinematics', 'forge_msgs', 'forgelab_common'}
SEMVER = r'(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)'

def git(*args):
    return subprocess.check_output(['git', *args], text=True).strip()

def load(path):
    return tomllib.loads(path.read_text())

def metadata(root, family=None):
    if family:
        versions = load(root / 'versions.toml')['packages']
        version = versions[family]
        project = load(root / 'packages' / family / 'pyproject.toml')['project']
        if project['version'] != version:
            raise ValueError('Family metadata differs from versions.toml')
        return version, {int(version.split('.')[0])}, set(), 'family'
    manifest = root / 'pyproject.toml'
    rust = not manifest.exists()
    doc = load(root / 'Cargo.toml') if rust else load(manifest)
    version = doc['package' if rust else 'project']['version']
    lock = load(root / ('Cargo.lock' if rust else 'uv.lock'))
    forge = {int(p['version'].split('.')[0]) for p in lock['package'] if p['name'] in CORE_PACKAGES}
    dora = {tuple(map(int, p['version'].split('.')[:2])) for p in lock['package'] if p['name'] in {'dora-rs', 'dora-node-api'}}
    kind = 'python' if not rust and 'build-system' in doc and doc.get('tool', {}).get('uv', {}).get('package') is not False else 'source'
    return version, forge, dora, kind

def validate(root, line, tag, default_branch, requested_version=None):
    match = re.fullmatch(r'(?:forge-(common|msgs|robot|policy|kinematics|tool)-)?v' + SEMVER, tag)
    if not match:
        raise ValueError('Use an existing annotated vX.Y.Z or forge-<family>-vX.Y.Z tag')
    family = match[1]
    version = '.'.join(match.groups()[1:])
    if requested_version is not None and version != requested_version:
        raise ValueError('Tag and requested version differ')
    tag_ref = 'refs/tags/' + tag
    if git('cat-file', '-t', tag_ref) != 'tag':
        raise ValueError('An annotated release tag is required')
    commit = git('rev-parse', tag_ref + '^{commit}')
    if commit != git('rev-parse', 'HEAD'):
        raise ValueError('Checkout differs from the release tag')
    branch = 'stable/1.x' if line == '1.x' else default_branch
    parent = 'refs/remotes/origin/' + branch
    ancestry = subprocess.run(['git', 'merge-base', '--is-ancestor', commit, parent], check=False)
    if ancestry.returncode != 0:
        raise ValueError('Release tag must belong to ' + branch)
    actual, forge, dora, kind = metadata(root, family)
    if actual != version:
        raise ValueError('Tag version differs from source metadata: ' + actual)
    major = int(line[0])
    if not version.startswith(str(major) + '.'):
        raise ValueError(f'Forge {line} requires node/package version {major}.x; normalize legacy 0.x nodes to 1.0.0 on stable/1.x')
    if forge != {major}:
        raise ValueError(f'Forge {line} requires resolved Forge major {major}; found {sorted(forge)}. Finish the migration before releasing.')
    expected_dora = (0, 4) if line == '1.x' else (1, 0)
    if not family and (not dora or any(v[0] != expected_dora[0] or (v[0] == 0 and v[1] != 4) for v in dora)):
        raise ValueError(f'Forge {line} requires Dora {expected_dora[0]}.{expected_dora[1]}.x; found {sorted(dora)}')
    return dict(tag=tag, version=version, release_line=line, commit=commit, source_branch=branch, package_kind=kind)

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--line', choices=['1.x', '2.x'], required=True)
    parser.add_argument('--tag', required=True)
    parser.add_argument('--default-branch', required=True)
    parser.add_argument('--version')
    args = parser.parse_args()
    try:
        result = validate(Path.cwd(), args.line, args.tag, args.default_branch, args.version)
    except (ValueError, KeyError, OSError, subprocess.CalledProcessError) as error:
        raise SystemExit(str(error)) from error
    if os.environ.get('GITHUB_OUTPUT'):
        with open(os.environ['GITHUB_OUTPUT'], 'a') as output:
            for key, value in result.items():
                output.write(f'{key}={value}\n')
    print(json.dumps(result, indent=2))

if __name__ == '__main__':
    main()
