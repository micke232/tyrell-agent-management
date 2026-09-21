"""Shared state events and bounded Git refresh for provider runtimes."""
import asyncio
from .workspace_changes import changes


class ProviderRuntime:
    def emit(self, tid, method, **params):
        self.state.event(method, dict(threadId=tid, **params))

    def schedule(self, coroutine):
        task = asyncio.create_task(coroutine)
        self.background.add(task)
        task.add_done_callback(self.background.discard)

    def request_changes(self, tid):
        existing = self.diff_jobs.get(tid)
        if existing and not existing.done():
            self.diff_again.add(tid)
            return
        async def delayed():
            while True:
                self.diff_again.discard(tid)
                await asyncio.sleep(1)
                await self.collect_changes(tid)
                if tid not in self.diff_again:
                    break
        task = asyncio.create_task(delayed())
        self.diff_jobs[tid] = task
        self.background.add(task)
        task.add_done_callback(self.background.discard)

    async def collect_changes(self, tid):
        t = self.state.thread(tid)
        try:
            records = await changes(t.get("setupCwd") or t["cwd"], t.get("agentWorktree", {}).get("baseCommit"))
            t.update(changedFiles=records, filesSource="git", filesBase=records.base,
                     filesError=("Showing the first 250 of %d changed files." % records.total) if records.truncated else None)
        except (OSError, ValueError, asyncio.TimeoutError):
            t["filesError"] = "Git diff unavailable or too large; reported file operations remain visible."
        self.state.dirty = True

    def finish(self, tid, status):
        t = self.state.thread(tid)
        if t.get("turnId"):
            self.emit(tid, "turn/completed", turn={"id": t["turnId"], "status": status})
            self.request_changes(tid)

    def refresh_waiting(self, tid):
        requests = [r for r in self.state.requests.values() if r.get("params", {}).get("threadId") == tid]
        t = self.state.thread(tid)
        if t.get("status", {}).get("type") == "active":
            t["status"]["activeFlags"] = (["waitingOnUserInput" if "requestUserInput" in requests[0]["method"] else "waitingOnApproval"] if requests else [])
        self.state.dirty = True
