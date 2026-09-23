"""Agent-reported file changes and a read-only, optional patch browser."""
import posixpath
from pathlib import Path


def workspace_root(thread):
    return (thread.get("agentWorktree") or {}).get("cwd") or thread.get("setupCwd") or thread.get("projectRoot") or thread.get("repo") or thread.get("cwd") or str(Path.home())


def record_changes(thread, item):
    """Keep the latest reported patch per path, independent of chat retention."""
    if thread.get("filesSource") == "git":
        return  # Historical/tool patches must not contaminate the current Git snapshot.
    records = thread.setdefault("changedFiles", {})
    for change in item.get("changes", []):
        path = change.get("path")
        if not path:
            continue
        cwd = workspace_root(thread)
        path = posixpath.normpath(posixpath.join(cwd, path))
        kind = change.get("kind", {})
        destination = kind.get("move_path")
        target = posixpath.normpath(posixpath.join(cwd, destination)) if destination else path
        diff = change.get("diff") or ""
        # App-server supplies raw contents for newly added/deleted files, and
        # unified hunks for updates. Normalize raw contents for the same viewer.
        if diff and kind.get("type") in ("add", "delete") and not diff.startswith(("diff --git ", "--- ", "@@ ")):
            sign = "+" if kind["type"] == "add" else "-"
            diff = "\n".join(sign + line for line in diff.splitlines())
        lines = diff.splitlines()
        records[target] = {
            "path": target, "from": path if destination else None,
            "kind": "rename" if destination else kind.get("type", "update"),
            "status": item.get("status", "inProgress"), "itemId": item.get("id"),
            "diff": diff[:60000], "truncated": len(diff) > 60000,
            "added": sum(line.startswith("+") and not line.startswith("+++") for line in lines),
            "removed": sum(line.startswith("-") and not line.startswith("---") for line in lines),
        }
        if destination and target != path:
            records.pop(path, None)


def display_path(path, cwd):
    root = posixpath.normpath(cwd) if cwd else ""
    if root and (path == root or path.startswith(root.rstrip("/") + "/")):
        return posixpath.relpath(path, root)
    return "Outside project/" + path.lstrip("/") if path.startswith("/") else path


def status(record):
    state = record.get("status")
    if state != "completed":
        return {"inProgress": ("Pending", "working"), "failed": ("Failed", "error"),
                "declined": ("Declined", "warning")}.get(state, ("Unknown", "muted"))
    return {"add": ("Added", "success"), "delete": ("Deleted", "error"),
            "rename": ("Renamed", "warning")}.get(record.get("kind"), ("Modified", "working"))


class FilesView:
    def __init__(self):
        self.owner = None
        self.closed = set()
        self.detail = None
        self.index = self.scroll = 0
        self.rows = []
        self.hits = {}
        self.locations = {}
        self.picker = None
        self.picker_error = ""

    def build_rows(self, ui, width):
        if self.owner != ui.selected:
            self.owner = ui.selected
            self.closed.clear()
            self.picker = None
            self.detail = None
            self.index = self.scroll = 0
        thread = ui.current()
        location = self.locations.get(ui.selected)
        root = location["root"] if location else workspace_root(thread)
        records = location["records"] if location else thread.get("changedFiles", {})
        scope = location.get("scope", "working") if location else "working"
        previous = self.rows[self.index].get("action") if self.rows and self.index < len(self.rows) else None
        rows = []

        def row(text, tone="base", action=None, copy=False):
            rows.append(dict(text=text, tone=tone, action=action, title="", inset=0,
                             header=False, copy_text=text if copy else None))

        if self.picker is not None:
            row("Folder: " + str(self.picker), "accent")
            row("OK · Open this folder", "success", ("choose", str(self.picker)))
            row("..  Parent folder", "accent", ("navigate", str(self.picker.parent)))
            row("~  Home", "accent", ("navigate", str(Path.home())))
            row("Cancel", "muted", ("cancel", ""))
            try:
                for folder in sorted((p for p in self.picker.iterdir() if p.is_dir()), key=lambda p: p.name.casefold())[:1000]:
                    row("  ▸ " + folder.name + "/", "base", ("navigate", str(folder)))
            except OSError as error:
                row("Cannot read folder: " + str(error), "warning")
            self.rows = rows
            self.index = min(max(1, self.index), len(rows) - 1)
            return rows
        if self.detail and self.detail in records:
            record = records[self.detail]
            row("‹ Back to file tree", "accent", ("back", ""))
            from .ui import wrap
            for line in wrap(record["path"], width):
                row(line, "accent", copy=True)
            label, tone = status(record)
            row(label + (" · Workspace diff" if (location is not None or thread.get("filesSource") == "git") else " · Latest recorded patch"), tone)
            if record.get("from"):
                for line in wrap("From: " + record["from"], width):
                    row(line, "muted", copy=True)
            row("+%d / -%d lines in this patch" % (record.get("added", 0), record.get("removed", 0)), "muted")
            row("")
            for line in (record.get("diff") or "No patch text was reported.").splitlines():
                tone = "success" if line.startswith("+") else "error" if line.startswith("-") else "accent" if line.startswith("@@") else "base"
                for part in wrap(line, width):
                    row(part, tone, copy=True)
            if record.get("truncated"):
                row("Patch preview truncated at 60,000 characters.", "warning")
        else:
            self.detail = None
            row("Root: " + root, "accent", copy=True)
            row("Open folder…", "accent", ("open", root))
            shared = [t.get("name") or tid[:8] for tid, t in ui.data.get("threads", {}).items()
                      if tid != thread.get("id") and workspace_root(t) == root]
            if shared:
                row("Shared workspace with: " + ", ".join(shared), "warning")
            row("Show uncommitted changes" if scope == "branch" else "Include branch commits", "accent", ("scope", "working" if scope == "branch" else "branch"))
            if location:
                row("Refresh", "accent", ("refresh", root))
                row("Use agent workspace", "muted", ("reset", ""))
                row("Browsing only · agent working folder is unchanged", "muted")
                if location.get("error"):
                    row(location["error"], "warning")
            current_git = location is not None or thread.get("filesSource") == "git"
            label = "Uncommitted changes" if scope == "working" else "Branch changes + uncommitted changes"
            if not location and current_git and thread.get("filesBase") != "HEAD":
                label = "Previous comparison · refreshing"
            row(("%d files · " + (label if current_git else "Historical agent patches")) % len(records), "accent")
            comparison = location.get("base") if location else thread.get("filesBase")
            if comparison:
                row("Compared with: " + ("HEAD · uncommitted changes" if comparison == "HEAD" else comparison[:12] + " · includes committed branch changes"), "muted")
            row("Git workspace state · not proof of which agent changed a file" if current_git else "Historical patches · may already be committed, merged or reverted", "muted")
            if thread.get("filesError"):
                row(thread["filesError"], "warning")
            if not records:
                row(("No uncommitted changes." if scope == "working" else "No branch changes against this base.") if current_git else "No file changes reported by this agent yet.", "muted")
            tree = {}
            for path, record in records.items():
                parts = display_path(path, root).split("/")
                node = tree
                for part in parts[:-1]:
                    node = node.setdefault(("folder", part), {})
                node[("file", parts[-1])] = record

            def walk(node, prefix="", parents=()):
                entries = sorted(node.items(), key=lambda entry: (entry[0][0] != "folder", entry[0][1].casefold()))
                for i, ((kind, name), value) in enumerate(entries):
                    last = i == len(entries) - 1
                    branch = prefix + ("└─ " if last else "├─ ")
                    key = parents + (name,)
                    if kind == "folder":
                        row(branch + ("▸ " if key in self.closed else "▾ ") + name + "/", "accent", ("folder", key))
                        if key not in self.closed:
                            walk(value, prefix + ("   " if last else "│  "), key)
                    else:
                        label, tone = status(value)
                        row(branch + label + "  " + name, tone, ("file", value["path"]))
            walk(tree)
        self.rows = rows
        self.index = next((i for i, r in enumerate(rows) if previous and r.get("action") == previous), min(self.index, len(rows) - 1))
        if not rows[self.index]["action"] and not self.detail:
            self.index = next((i for i, r in enumerate(rows) if r["action"]), 0)
        return rows

    def visible(self, ui, height, width):
        rows = self.build_rows(ui, width)
        self.scroll = min(max(0, self.scroll), max(0, len(rows) - height))
        return [{**row, "source_index": i, "setup_selected": i == self.index and bool(row["action"]) and ui.focus == "history"}
                for i, row in enumerate(rows[self.scroll:self.scroll + height], self.scroll)]

    def move(self, delta, height):
        if self.detail:
            self.scroll = min(max(0, self.scroll + delta), max(0, len(self.rows) - height))
            return
        choices = [i for i, row in enumerate(self.rows) if row["action"] and (i - self.index) * delta > 0]
        if choices:
            self.index = min(choices) if delta > 0 else max(choices)
        self.scroll = max(min(self.scroll, self.index), self.index - height + 1, 0)

    def activate(self, index=None, direction=0, ui=None):
        if index is not None:
            self.index = index
        if not self.rows or not 0 <= self.index < len(self.rows):
            return
        action = self.rows[self.index]["action"]
        if self.detail and direction < 0:
            action = ("back", "")
        if not action:
            return
        kind, key = action
        if kind in ("open", "navigate"):
            self.picker = Path(key).expanduser()
            self.index = 1
            self.scroll = 0
        elif kind in ("choose", "refresh", "scope") and ui:
            location = self.locations.get(ui.selected, {})
            scope = key if kind == "scope" else location.get("scope", "working") if kind == "refresh" else "working"
            path = (location.get("root") or workspace_root(ui.current())) if kind == "scope" else key
            ui.submit("files_open", path=path, scope=scope, owner=ui.selected)
        elif kind in ("cancel", "reset"):
            self.picker = None
            if kind == "reset" and ui:
                self.locations.pop(ui.selected, None)
            self.index = self.scroll = 0
        elif kind == "back":
            self.detail = None
            self.index = self.scroll = 0
        elif kind == "folder":
            if direction < 0:
                self.closed.add(key)
            elif direction > 0:
                self.closed.discard(key)
            else:
                self.closed.symmetric_difference_update({key})
        elif direction >= 0:
            self.detail = key
            self.index = self.scroll = 0
