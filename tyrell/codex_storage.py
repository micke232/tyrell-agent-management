"""Private Codex session storage; account configuration remains user-owned."""
import os
from pathlib import Path
from .paths import reuse_credentials


def environment(directory):
    """Scope Codex's documented home setting to its child process only.

    Never share sessions, SQLite databases, daemon sockets or logs with the IDE.
    Symlink explicit user configuration so authentication refresh has one owner.
    """
    source = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")).expanduser().resolve()
    target = (Path(directory) / "providers" / "codex").resolve()
    target.mkdir(parents=True, exist_ok=True, mode=0o700)
    for name in ("config.toml", "AGENTS.md", "AGENTS.override.md", "skills", "rules", "plugins"):
        original, link = source / name, target / name
        if original.exists() and not link.exists() and not link.is_symlink():
            link.symlink_to(original, target_is_directory=original.is_dir())
    reuse_credentials(source / "auth.json", target / "auth.json")
    # These are subprocess settings, not changes to the launching shell or IDE.
    return {**os.environ, "CODEX_HOME": str(target)}, target
