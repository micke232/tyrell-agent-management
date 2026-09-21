"""State location with compatibility for existing installations."""
import os
import shutil
import tempfile
from pathlib import Path


def state_directory(environ=None, home=None):
    environ = os.environ if environ is None else environ
    home = Path.home() if home is None else Path(home)
    explicit = environ.get('TYRELL_HOME') or environ.get('CODEX_DASHBOARD_HOME')
    if explicit:
        return str(Path(explicit).expanduser())
    legacy = home/'.codex-dashboard'
    return str(legacy if legacy.exists() else home/'.tyrell')


def reuse_credentials(source, target):
    """Reuse user sign-in without sharing the provider's session directory.

    Some CLIs replace auth files atomically, replacing our original symlink.
    A later explicit sign-in in the user's CLI must supersede that private copy.
    """
    source, target = Path(source), Path(target)
    if not source.is_file():
        return
    if not target.exists() and not target.is_symlink():
        target.symlink_to(source.resolve())
    elif not target.is_symlink() and source.stat().st_mtime_ns > target.stat().st_mtime_ns:
        fd, name = tempfile.mkstemp(prefix='.auth-', dir=target.parent)
        try:
            with os.fdopen(fd, 'wb') as output, source.open('rb') as incoming:
                shutil.copyfileobj(incoming, output)
            os.replace(name, target)
        finally:
            Path(name).unlink(missing_ok=True)
