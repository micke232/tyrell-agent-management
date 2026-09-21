#!/usr/bin/env python3
"""Build a release archive and Homebrew tap files; never publish or install them."""
import argparse
import gzip
import hashlib
import io
from pathlib import Path
import re
import tarfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def build(wheel, repository, output):
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9][A-Za-z0-9_.-]*', repository):
        raise ValueError('Use a GitHub repository in OWNER/REPOSITORY form')
    wheel, output = Path(wheel).resolve(), Path(output).resolve()
    match = re.fullmatch(r'tyrell_agent_management-([0-9][A-Za-z0-9.+]*)-py3-none-any.whl', wheel.name)
    if not match:
        raise ValueError('Choose a Tyrell Agent Management universal wheel')
    version = match[1]
    with zipfile.ZipFile(wheel) as archive:
        metadata = archive.read('tyrell_agent_management-' + version + '.dist-info/METADATA').decode()
        if 'Name: tyrell-agent-management\n' not in metadata or 'Version: ' + version + '\n' not in metadata:
            raise ValueError('Wheel metadata does not match its filename')
    output.mkdir(parents=True, exist_ok=True)
    filename = 'tyrell-' + version + '-homebrew.tar.gz'
    archive_path = output / filename
    # Package only the verified wheel and generic installation guide, never the checkout/state.
    with archive_path.open('wb') as raw, gzip.GzipFile(fileobj=raw, mode='wb', filename='', mtime=0) as compressed, tarfile.open(fileobj=compressed, mode='w') as tar:
        for name, data in ((wheel.name, wheel.read_bytes()), ('INSTALL.md', (ROOT/'docs/install.md').read_bytes()), ('opencode.md', (ROOT/'docs/opencode.md').read_bytes())):
            info = tarfile.TarInfo('tyrell-' + version + '/' + name)
            info.size, info.mode, info.mtime = len(data), 0o644, 0
            tar.addfile(info, io.BytesIO(data))
    checksum = hashlib.sha256(archive_path.read_bytes()).hexdigest()
    homepage = 'https://github.com/' + repository
    url = homepage + '/releases/download/v' + version + '/' + filename
    text = (ROOT/'packaging/homebrew/tyrell.rb.in').read_text()
    for key, value in {'HOMEPAGE':homepage, 'URL':url, 'VERSION':version, 'SHA256':checksum, 'WHEEL':wheel.name}.items():
        text = text.replace('@@' + key + '@@', value)
    tap = output/'repository-files'
    (tap/'Formula').mkdir(parents=True, exist_ok=True)
    (tap/'Formula/tyrell.rb').write_text(text)
    (tap/'HOMEBREW.md').write_text('# Install Tyrell Agent Management with Homebrew\n\nKeep Formula/tyrell.rb in the same repository as the app.\n\n```sh\nbrew tap ' + repository + ' https://github.com/' + repository + '.git\nbrew install ' + repository + '/tyrell\ntyrell\n```\n')
    (output/'SHA256SUMS').write_text(checksum + '  ' + filename + '\n')
    return archive_path, tap/'Formula/tyrell.rb'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--wheel', required=True, type=Path)
    parser.add_argument('--repository', required=True, help='GitHub repository that will host release assets (OWNER/REPOSITORY)')
    parser.add_argument('--output', type=Path, default=ROOT/'dist/homebrew')
    args = parser.parse_args()
    archive, formula = build(args.wheel, args.repository, args.output)
    print('Release archive: ' + str(archive))
    print('Tap formula: ' + str(formula))
    print('Prepared locally only. Publish the archive and tap before sharing a brew command.')


if __name__ == '__main__':
    main()
