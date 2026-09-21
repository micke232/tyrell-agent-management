import asyncio
import hashlib
import re
from pathlib import Path


async def git(*args):
    process = await asyncio.create_subprocess_exec("git", *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    out, err = await process.communicate()
    if process.returncode:
        raise ValueError(err.decode(errors="replace").strip())
    return out.decode().strip()


async def create_worktree(repo, root, task_id, title, base_ref="HEAD", detached=False):
    repo = Path(repo).expanduser().resolve()
    top = Path(await git("-C", str(repo), "rev-parse", "--show-toplevel")).resolve()
    head = await git("-C", str(top), "rev-parse", "--verify", "--end-of-options", (base_ref or "HEAD") + "^{commit}")
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:32] or "task"
    branch = "dashboard/" + slug + "-" + task_id[:8]
    key = hashlib.sha256(str(top).encode()).hexdigest()[:10]
    destination = Path(root).resolve() / (top.name + "-" + key) / task_id[:12]
    if destination == top or top in destination.parents:
        raise ValueError("Worktrees must be stored outside the project's checkout")
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if detached:
        await git("-C", str(top), "worktree", "add", "--detach", str(destination), head)
        branch = None
    else:
        await git("-C", str(top), "worktree", "add", "-b", branch, str(destination), head)
    return {"repo": str(top), "cwd": str(destination), "branch": branch, "baseCommit": head}
