#!/usr/bin/env python3
"""Install a zsh dispatcher while leaving the real Codex executable untouched."""
import argparse
import os
import shlex
import shutil
import sys
import time
from pathlib import Path


def install(home, project, python):
    state = home / ".codex-dashboard"
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    entry = project / "dashboard.py"
    executable = home / ".local/bin/codex-dashboard"
    executable.parent.mkdir(parents=True, exist_ok=True)
    content = "#!/bin/sh\nexec " + shlex.quote(python) + " -B " + shlex.quote(str(entry)) + ' "$@"\n'
    if executable.exists() and "dashboard.py" not in executable.read_text():
        raise RuntimeError("Refusing to replace an unrelated executable: " + str(executable))
    executable.write_text(content)
    executable.chmod(0o755)
    tyrell = executable.with_name("tyrell")
    if tyrell.exists() or tyrell.is_symlink():
        if not tyrell.is_symlink() or tyrell.resolve() != executable.resolve():
            raise RuntimeError("Refusing to replace an unrelated executable: " + str(tyrell))
    else:
        tyrell.symlink_to(executable.name)
    integration = state / "shell.zsh"
    integration.write_text("# Codex Dashboard: delegate every other command to the original executable.\n"
                           "# Interactive non-login shells do not read .zprofile.\n"
                           'case ":${PATH}:" in\n'
                           "  *" + shlex.quote(":" + str(executable.parent) + ":") + "*) ;;\n"
                           "  *) export PATH=" + shlex.quote(str(executable.parent)) + ':"$PATH" ;;\n'
                           "esac\n"
                           "function codex() {\n"
                           '  if [[ "${1-}" == "dashboard" ]]; then\n'
                           "    shift\n"
                           "    " + shlex.quote(str(executable)) + ' "$@"\n'
                           "  else\n"
                           '    command codex "$@"\n'
                           "  fi\n"
                           "}\n")
    zshrc = home / ".zshrc"
    source = "source " + shlex.quote(str(integration))
    existing = zshrc.read_text() if zshrc.exists() else ""
    if source not in existing:
        if zshrc.exists():
            shutil.copy2(zshrc, state / ("zshrc.backup." + time.strftime("%Y%m%d-%H%M%S")))
        with zshrc.open("a") as f:
            f.write("\n# Codex Dashboard\n" + source + "\n")
    print("Installed: " + str(executable))
    print("New terminals: tyrell (codex dashboard also works)")
    print("Current terminal: " + source)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--home", type=Path, default=Path.home(), help="Alternative installation root for testing")
    args = parser.parse_args()
    install(args.home.resolve(), Path(__file__).resolve().parents[1], sys.executable)
