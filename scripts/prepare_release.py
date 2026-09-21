#!/usr/bin/env python3
"""Build reproducible packages and update the formula before merging a release PR."""
import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.build_homebrew import build


def prepare(repository, output, check=False):
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    # Fixed ZIP member dates plus pinned tooling make the wheel byte-identical
    # across the developer machine, PR build and subsequent tagged release.
    env = dict(os.environ, SOURCE_DATE_EPOCH='315532800', PYTHONHASHSEED='0')
    with tempfile.TemporaryDirectory(prefix='tyrell-release-') as directory:
        source = Path(directory)/'source'
        source.mkdir()
        for name in ('pyproject.toml', 'setup.cfg', 'setup.py', 'MANIFEST.in'):
            shutil.copyfile(ROOT/name, source/name)
        shutil.copytree(ROOT/'tyrell', source/'tyrell', ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
        (source/'docs').mkdir()
        shutil.copyfile(ROOT/'docs/install.md', source/'docs/install.md')
        # Git records executable bits, not the checkout's read/write permissions.
        # Wheels retain file modes, so normalize the temporary source tree.
        for path in source.rglob('*'):
            path.chmod(0o755 if path.is_dir() else 0o644)
        subprocess.run([sys.executable, '-m', 'build', '--wheel', '--no-isolation',
                        '--outdir', directory, str(source)], env=env, check=True)
        wheel, = Path(directory).glob('*.whl')
        archive, generated = build(wheel, repository, output/'homebrew')
        shutil.copy2(wheel, output/wheel.name)
    formula = ROOT/'Formula/tyrell.rb'
    if check:
        if not formula.exists() or formula.read_bytes() != generated.read_bytes():
            raise SystemExit('Formula differs from this build. Run scripts/prepare_release.py and commit Formula/tyrell.rb.')
    else:
        formula.parent.mkdir(exist_ok=True)
        shutil.copyfile(generated, formula)
    print('Verified formula: ' + str(formula) if check else 'Updated formula: ' + str(formula))
    return archive


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repository', default='micke232/tyrell-agent-management')
    parser.add_argument('--output', type=Path, default=ROOT/'dist')
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    prepare(args.repository, args.output, args.check)
