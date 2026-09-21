"""State location with compatibility for existing installations."""
import os
from pathlib import Path


def state_directory(environ=None, home=None):
    environ = os.environ if environ is None else environ
    home = Path.home() if home is None else Path(home)
    explicit = environ.get('TYRELL_HOME') or environ.get('CODEX_DASHBOARD_HOME')
    if explicit:
        return str(Path(explicit).expanduser())
    legacy = home/'.codex-dashboard'
    return str(legacy if legacy.exists() else home/'.tyrell')
