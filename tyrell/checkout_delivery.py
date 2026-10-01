"""Transactional delivery of an agent worktree into its registered checkout."""
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import time
import uuid
from pathlib import Path


DELIVERY_PATTERNS = (
    r"\bdeliver changes to checkout\b",
    r"\b(?:lägg in|applicera|leverera|för över|flytta) (?:de här |agentens |mina )?ändringar(?:na)? (?:lokalt )?(?:till|i|på) (?:min|den|min vanliga|den vanliga) (?:branch|branchen|checkout)\b",
    r"\b(?:apply|deliver|transfer|move) (?:the |these |my |agent )?changes (?:locally )?(?:to|into|in) (?:my|the|my normal|the normal) (?:branch|checkout)\b",
)


def is_delivery_instruction(text):
    normalized = " ".join(str(text).casefold().split())
    return any(re.search(pattern, normalized) for pattern in DELIVERY_PATTERNS)


def _git(cwd, *args, text=True):
    result = subprocess.run(
        ("git", "-C", str(cwd), *args), stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, check=False,
        env=dict(os.environ, GIT_OPTIONAL_LOCKS="0", GIT_TERMINAL_PROMPT="0"),
    )
    if result.returncode:
        raise ValueError(result.stderr.decode(errors="replace").strip() or "Git command failed")
    return result.stdout.decode(errors="surrogateescape").strip() if text else result.stdout


def _root(path):
    return Path(_git(path, "rev-parse", "--show-toplevel")).resolve()


def _common(path):
    value = Path(_git(path, "rev-parse", "--git-common-dir"))
    return (Path(path) / value).resolve()


def _changed_paths(source, base):
    raw = _git(source, "diff", "--name-only", "-z", "--no-renames", base, "--", text=False)
    untracked = _git(source, "ls-files", "--others", "--exclude-standard", "-z", text=False)
    names = {os.fsdecode(value) for value in (raw + untracked).split(b"\0") if value}
    if not names:
        raise ValueError("The agent workspace has no changes to deliver")
    for name in names:
        candidate = Path(name)
        if candidate.is_absolute() or ".." in candidate.parts:
            raise ValueError("Git reported an unsafe path")
    return sorted(names)


def _entry(path):
    try:
        mode = path.lstat().st_mode
    except (FileNotFoundError, NotADirectoryError):
        return {"kind": "missing"}
    if stat.S_ISLNK(mode):
        return {"kind": "symlink", "target": os.readlink(path)}
    if stat.S_ISREG(mode):
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        return {"kind": "file", "sha256": digest.hexdigest(), "executable": bool(mode & stat.S_IXUSR)}
    if stat.S_ISDIR(mode):
        return {"kind": "directory"}
    return {"kind": "special"}


def _base_entry(repo, base, name):
    try:
        mode, _, blob = _git(repo, "ls-tree", base, "--", name).split(None, 2)
    except ValueError:
        return {"kind": "missing"}
    if not blob:
        return {"kind": "missing"}
    object_id = blob.split("\t", 1)[0]
    if mode == "120000":
        return {"kind": "symlink", "target": os.fsdecode(_git(repo, "cat-file", "blob", object_id, text=False))}
    if mode in ("100644", "100755"):
        content = _git(repo, "cat-file", "blob", object_id, text=False)
        return {"kind": "file", "sha256": hashlib.sha256(content).hexdigest(), "executable": mode == "100755"}
    return {"kind": "special"}


def _remove(path):
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.exists():
        shutil.rmtree(path)


def _ordered(paths, entries):
    """Remove deep paths before creating their shallower replacements."""
    missing = sorted((name for name in paths if entries[name]["kind"] == "missing"),
                     key=lambda name: len(Path(name).parts), reverse=True)
    present = sorted((name for name in paths if entries[name]["kind"] != "missing"),
                     key=lambda name: len(Path(name).parts))
    return missing + present


def _copy_entry(source, destination, expected):
    _remove(destination)
    if expected["kind"] == "missing":
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    if expected["kind"] == "symlink":
        destination.symlink_to(os.readlink(source))
    elif expected["kind"] == "file":
        shutil.copyfile(source, destination, follow_symlinks=False)
        destination.chmod(0o755 if expected["executable"] else 0o644)
    elif expected["kind"] == "directory":
        destination.mkdir(parents=True, exist_ok=True)
    else:
        raise ValueError("Special files cannot be delivered: " + str(source))


def _backup_entry(path, backup_path, entry):
    if entry["kind"] == "missing":
        return
    backup_path.parent.mkdir(parents=True, exist_ok=True)
    if entry["kind"] == "symlink":
        backup_path.symlink_to(os.readlink(path))
    elif entry["kind"] == "file":
        shutil.copy2(path, backup_path, follow_symlinks=False)
    else:
        raise ValueError("A changed destination path is not a regular file or symbolic link: " + str(path))


def _restore_entry(backup_path, destination, entry):
    _remove(destination)
    if entry["kind"] == "missing":
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    if entry["kind"] == "symlink":
        destination.symlink_to(os.readlink(backup_path))
    elif entry["kind"] == "file":
        shutil.copy2(backup_path, destination, follow_symlinks=False)
    else:
        raise ValueError("Cannot restore a non-file backup: " + str(destination))


def deliver(worktree, backup_root):
    """Apply source changes to its registered checkout or leave it untouched."""
    source = _root(worktree["cwd"])
    destination = Path(worktree["repo"]).resolve()
    base = worktree.get("baseCommit")
    if not base:
        raise ValueError("The agent worktree has no recorded base commit")
    if _root(destination) != destination:
        raise ValueError("The registered checkout is no longer the repository root")
    if source == destination or _common(source) != _common(destination):
        raise ValueError("The agent worktree and registered checkout are not from the same repository")
    branch = _git(destination, "symbolic-ref", "--quiet", "--short", "HEAD")
    if not branch:
        raise ValueError("The registered checkout must be on a branch")
    _git(source, "cat-file", "-e", base + "^{commit}")
    _git(destination, "merge-base", "--is-ancestor", base, "HEAD")
    destination_head = _git(destination, "rev-parse", "HEAD")
    paths = _changed_paths(source, base)
    desired = {name: _entry(source / name) for name in paths}
    unsupported = [name for name, entry in desired.items() if entry["kind"] == "special"]
    if unsupported:
        raise ValueError("Unsupported changed paths: " + ", ".join(unsupported[:5]))
    before = {name: _entry(destination / name) for name in paths}
    parent_paths = {parent for name in paths for parent in (destination / name).parents
                    if parent != destination and destination in parent.parents}
    absent_parents = {parent for parent in parent_paths if not parent.exists()}
    base_entries = {name: _base_entry(source, base, name) for name in paths}
    conflicts = [name for name in paths if before[name] != base_entries[name] and before[name] != desired[name]]
    if conflicts:
        raise ValueError("Delivery would overwrite conflicting checkout changes: " + ", ".join(conflicts[:10]))

    delivery_id = time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:8]
    backup = Path(backup_root) / delivery_id
    files = backup / "files"
    backup_files = {name: files / hashlib.sha256(os.fsencode(name)).hexdigest() for name in paths}
    backup.mkdir(parents=True, mode=0o700)
    manifest = {"id": delivery_id, "createdAt": time.time(), "source": str(source),
                "destination": str(destination), "branch": branch, "destinationHead": destination_head,
                "baseCommit": base,
                "paths": [{"path": name, "before": before[name], "desired": desired[name],
                           "backupObject": str(backup_files[name].relative_to(backup)) if before[name]["kind"] != "missing" else None}
                          for name in paths]}
    (backup / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    for name in paths:
        _backup_entry(destination / name, backup_files[name], before[name])
    try:
        # Recheck after backup to close the validation/write race window.
        changed = [name for name in paths if _entry(destination / name) != before[name]]
        if changed:
            raise ValueError("Checkout changed while the delivery was being prepared: " + ", ".join(changed[:10]))
        if (_git(destination, "symbolic-ref", "--quiet", "--short", "HEAD") != branch
                or _git(destination, "rev-parse", "HEAD") != destination_head):
            raise ValueError("The checkout branch or commit changed while the delivery was being prepared")
        for name in _ordered(paths, desired):
            _copy_entry(source / name, destination / name, desired[name])
        failed = [name for name in paths if _entry(destination / name) != desired[name]]
        if failed:
            raise ValueError("Delivered files did not match the agent workspace: " + ", ".join(failed[:10]))
        if (_git(destination, "symbolic-ref", "--quiet", "--short", "HEAD") != branch
                or _git(destination, "rev-parse", "HEAD") != destination_head):
            raise ValueError("The checkout branch or commit changed during delivery")
    except Exception as error:
        rollback_errors = []
        for name in _ordered(paths, before):
            try:
                _restore_entry(backup_files[name], destination / name, before[name])
            except Exception as rollback_error:
                rollback_errors.append(name + ": " + str(rollback_error))
        for parent in sorted(absent_parents, key=lambda path: len(path.parts), reverse=True):
            try:
                parent.rmdir()
            except OSError:
                pass
        if rollback_errors:
            raise RuntimeError(str(error) + "; rollback also failed: " + "; ".join(rollback_errors)) from error
        raise
    return {"id": delivery_id, "source": str(source), "destination": str(destination),
            "branch": branch, "baseCommit": base, "backup": str(backup), "paths": paths}
