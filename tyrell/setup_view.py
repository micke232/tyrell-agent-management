"""Keyboard/mouse form state for the Setup tab; commands go through the service."""
from copy import deepcopy
import re
from pathlib import Path

from .agent_setup import (FIELDS, SECTIONS, choices_for, effective_config, instruction_text,
                          project_root, target_entity, validate_patch, guidance_conflicts)


ACCESS_PRESETS = {
    "review": ("Read only", {"fileAccess": "Read only", "approvalMode": "On request", "networkAccess": False}),
    "workspace": ("Workspace + network", {"fileAccess": "Workspace", "approvalMode": "On request", "networkAccess": True}),
    "autonomous": ("Autonomous / full access", {"fileAccess": "Full access", "approvalMode": "Never", "networkAccess": True}),
}
ACCESS_HELP = """ACCESS — CODEX

WHEN CHANGES APPLY
Saved settings apply when you send the next message to a ready agent.
Messages sent while it is working steer the current turn and keep its current permissions.
Saving Setup does not run commands or approve an already pending request.

QUICK PROFILES
Read only: inspect files, with restricted network access. Changes need approval.
Workspace + network: edit in the workspace and run commands with network access.
Operations outside sandbox limits can still require approval.
Autonomous / full access: unrestricted files and network, without approval dialogs.
This allows npm scripts and installs to run without a permission prompt.
It also allows commands outside the worktree; repository and task instructions still apply.

FILE ACCESS
Keep current: preserve the session's existing restrictions. It does not grant more access.
Read only: sandboxed commands cannot write files without additional permission.
Workspace: allow writes in the working folder and permitted temporary paths;
protected paths and operations outside the workspace may require approval.
Full access: remove filesystem and network sandbox restrictions.

APPROVAL REQUESTS
On request: ask when an operation needs additional permission.
Never: no approval dialogs. With a restricted sandbox, blocked operations fail instead.
To work autonomously with full access, select Full access together with Never.
Untrusted (legacy): frequently asks for command approval. It is no longer offered as a new choice.

NETWORK ACCESS
Read only / Workspace: the switch controls sandbox network access on the next turn.
Full access: network is always allowed; the separate switch has no effect.
Keep current: the session's existing network policy is preserved.

COPILOT
Copilot uses its own CLI and organization permission policy.
Codex access profiles do not change Copilot permissions.
Copilot has Ask and Autonomous modes. Autonomous permits tools and unrestricted paths.
Organization policies can still block actions; questions requiring your input remain interactive.
Changes apply at the next new turn.

Esc closes this guide.
"""


def access_description(key, value):
    return {
        "fileAccess": {"Keep current": "Keep the session's existing restrictions.", "Read only": "Inspect files; writes need more permission.",
                       "Workspace": "Write in the workspace; sandbox limits apply.", "Full access": "Unrestricted filesystem and network access."},
        "approvalMode": {"Keep current": "Keep the current approval policy.", "On request": "Ask when more permission is needed.",
                         "Never": "No dialogs; sandbox limits still apply.", "Untrusted": "Legacy: asks for many shell commands."},
    }.get(key, {}).get(value, "")


def extra_commands(config):
    return [s.strip() for s in re.split(r";|\n", config["extraChecks"]) if s.strip()]


def script_selected(script, config):
    role, command = script["role"], script["command"]
    if role == "dev":
        return config["devCommand"] == command
    if role == "extra":
        return command in extra_commands(config)
    return config[role] and config[role + "Command"] == command


def preview_rows(text, width, wrap):
    rows = []
    for line in text.splitlines():
        heading = line.startswith("## ") or line == "AGENT SETUP PREVIEW"
        if line.startswith("## "):
            line = line[3:].upper()
        elif line.startswith("- "):
            line = "• " + line[2:]
        for i, part in enumerate(wrap(line, max(8, width - 2))):
            rows.append({"text": ("  " if i and not heading else "") + part,
                         "heading": heading, "error": heading and "INSTRUCTION CONFLICTS" in line})
    return rows


class SetupForm:
    def __init__(self):
        self.scope = "agent"
        self.expanded = {"Model & speed"}
        self.index = 0
        self.scroll = 0
        self.rows = []
        self.hits = {}
        self.pending = False
        self.acknowledged = []
        self.owner = None
        self.branch_picker = None
        self.folder = None
        self.folder_entries = []
        self.folder_error = ""
        self.folder_target = None
        self.folder_owner = None

    def browse_folder(self, ui, path=None):
        if self.folder is None:
            data, params, _, _ = self.context(ui)
            self.folder_target = ui.selected if params.get("scope") == "agent" else None
            self.folder_owner = ui.selected
            entity = target_entity(data, ui.selected)
            path = params.get("path") or entity.get("projectRoot") or str(Path.home())
        candidate = Path(path).expanduser()
        try:
            candidate = candidate.resolve(strict=True)
            entries = sorted((p for p in candidate.iterdir() if p.is_dir()), key=lambda p: p.name.casefold())
        except (OSError, RuntimeError) as error:
            self.folder_error = "Cannot open folder: " + str(error)
            if self.folder is None:
                self.folder = Path.home()
            return
        self.folder, self.folder_entries, self.folder_error = candidate, entries, ""
        self.index, self.scroll, ui.focus = 1, 0, "history"

    def close_folder(self):
        self.folder = None
        self.folder_entries = []
        self.index = self.scroll = 0

    def folder_rows(self):
        entries = [("Folder: " + str(self.folder), "folder:stay"),
                   ("OK · Use this folder", "folder:ok"),
                   ("..  Parent folder", "folder:parent"),
                   ("~  Home", "folder:home"),
                   ("Cancel", "folder:cancel")]
        entries += [("  ▸ " + p.name + "/", "folder:open:" + str(p)) for p in self.folder_entries]
        if self.folder_error:
            entries.append((self.folder_error, "folder:stay"))
        self.rows = [{"text": text, "action": action,
                      "help": str(self.folder) + " · ↑/↓ Select · Enter/→ Open · ← Parent · OK chooses folder · Esc Cancel",
                      "tone": "success" if action == "folder:ok" else "base",
                      "title": "", "inset": 0, "header": False, "copy_text": None}
                     for text, action in entries]
        return self.rows

    def view_data(self, data):
        self.acknowledged = [a for a in self.acknowledged if a["revision"] > data.get("setupRevision", 0)]
        if not self.acknowledged:
            return data
        data = deepcopy(data)
        for a in self.acknowledged:
            if "profile" in a:
                if a.get("target"):
                    entity = target_entity(data, a["target"])
                    entity["projectRoot"] = a["profile"]["path"]
                    entity["setupCwd"] = (entity.get("agentWorktree") or {}).get("cwd") or a["profile"]["path"]
                p = a["profile"]
                data.setdefault("projectProfiles", {})[p["path"]] = p
                continue
            owner = (data.setdefault("settings", {}) if a["scope"] == "defaults" else
                     data.setdefault("projectProfiles", {}).setdefault(a["path"], {}) if a["scope"] == "project" else
                     target_entity(data, a["target"]))
            owner["config" if a["scope"] == "project" else "agentConfig"] = a["config"]
            if a.get("reset"):
                owner.pop("nextModel", None)
                owner.pop("nextEffort", None)
        return data

    def scopes(self, ui, data):
        result = [("agent", "This agent / task")] if target_entity(data, ui.selected) else []
        result += [("project:" + path, "Project · " + p.get("name", path)) for path, p in data.get("projectProfiles", {}).items()]
        return result + [("defaults", "Global defaults")]

    def context(self, ui):
        data = self.view_data(ui.data)
        scopes = self.scopes(ui, data)
        if self.scope not in dict(scopes):
            self.scope = scopes[0][0]
        if self.scope == "agent":
            params = {"scope": "agent", "target": ui.selected}
            config = effective_config(data, ui.selected)
            raw = target_entity(data, ui.selected).get("agentConfig", {})
        elif self.scope == "defaults":
            params = {"scope": "defaults"}
            config = effective_config(data, defaults_only=True)
            raw = data.get("settings", {}).get("agentConfig", {})
        else:
            path = self.scope[len("project:"):]
            params = {"scope": "project", "path": path}
            config = effective_config(data, project=path)
            raw = data.get("projectProfiles", {}).get(path, {}).get("config", {})
        if params["scope"] == "agent" and target_entity(data, ui.selected).get("provider") in ("copilot", "opencode"):
            data = dict(data, models=[{"model": m["id"], "displayName": m["name"], "supportedReasoningEfforts": []}
                                     for m in data.get("providers", {}).get(target_entity(data, ui.selected)["provider"], {}).get("models", [])])
        return data, params, config, raw

    def build_rows(self, ui):
        if self.branch_picker:
            picker = self.branch_picker
            if picker["owner"] != ui.selected:
                self.branch_picker = None
            else:
                self.rows = [{"text": ("● " if value == picker["value"] else "  ") + (value or "Current HEAD"),
                              "action": "branch:pick:" + str(i), "help": "Select base branch · Enter or click · Esc Cancel",
                              "tone": "success" if value == picker["value"] else "base", "title": "",
                              "inset": 0, "header": False, "copy_text": None}
                             for i, value in enumerate(picker["choices"])]
                self.rows.append({"text": "Cancel", "action": "branch:cancel", "help": "Return without changing base branch",
                                  "tone": "muted", "title": "", "inset": 0, "header": False, "copy_text": None})
                return self.rows
        if self.folder is not None:
            if self.folder_owner == ui.selected:
                return self.folder_rows()
            self.close_folder()
        data, params, config, raw = self.context(ui)
        owner = (self.scope, ui.selected if self.scope == "agent" else None)
        if owner != self.owner:
            self.index = self.scroll = 0
            self.owner = owner
        rows = []
        def row(text, action, help="", tone="base"):
            rows.append({"text": text, "action": action, "help": help, "tone": tone,
                         "title": "", "inset": 0, "header": False, "copy_text": None})
        row("Scope: " + dict(self.scopes(ui, data))[self.scope], "scope",
            "Agent overrides project defaults, which override global defaults. Click to change scope.", "accent")
        root = params.get("path") or project_root(target_entity(data, ui.selected))
        profile = data.get("projectProfiles", {}).get(root, {}) if self.scope != "defaults" else {}
        scripts = profile.get("scripts", [])
        conflicts = guidance_conflicts(profile, config)
        row("Working folder: " + (root or "Not selected"), "import", root or "Choose a folder")
        if profile:
            row("Detected: " + ("Git repo" if profile.get("gitRoot") else "No Git repo") + " · " + ("package.json" if profile.get("hasPackageJson") else "No package.json") + " · %d scripts" % len(scripts), "import_current", "Refresh folder detection")
        if conflicts:
            row("Instruction conflicts · " + str(len(conflicts)), "preview", "Repository instructions override Setup; open for details.", "error")
        row("Choose folder…", "import", "Select a directory to discover package.json scripts, ports and agent instructions.")
        if self.scope == "agent" and (ui.selected or "").startswith("thread:"):
            row("Hand over to another agent…", "handoff", "F6 · Copies Git changes and recent context into a separate worktree; review before starting.", "accent")
        if self.scope == "agent" and target_entity(data, ui.selected).get("provider") == "copilot":
            row("Verify GHE repository access", "verify_repository", "Read remote HEAD via Git HTTPS using existing credentials. No clone or remote changes.", "accent")
        row("Preview agent instructions", "preview", "Review the effective settings and the instructions attached to your next message.")
        row("Reset this scope to inherited defaults", "reset", "Remove this scope's overrides. Parent scopes remain unchanged.", "muted")
        row("Import selected repository", "import_current", root or "Choose a repository first.", "accent" if root else "muted")
        for section, fields in SECTIONS:
            if section == "Git worktree" and not profile.get("gitRoot") and self.scope != "defaults":
                continue
            opened = section in self.expanded
            row(("▾ " if opened else "▸ ") + section + "  · " + str(len(scripts) if section == "Project scripts" else len(fields)), "section:" + section,
                "Click or press Enter to " + ("collapse" if opened else "expand") + " this group.", "accent")
            if not opened:
                continue
            if section == "Runtime & ports" and self.scope == "agent":
                entity = target_entity(data, ui.selected)
                row("    Agent ports: dev " + str(entity.get("agentPort") or "Not assigned") + " · test " + str(entity.get("agentTestPort") or "Not assigned"),
                    "section:Runtime & ports", "Assigned on the next message from 42000–42999, separately from developer ports.", "accent")
            if section == "Git worktree":
                entity = target_entity(data, ui.selected)
                worktree = entity.get("agentWorktree")
                if worktree:
                    row("    Agent folder: " + worktree["cwd"], "section:Git worktree", worktree["cwd"], "muted")
                    row("    Base commit: " + worktree["baseCommit"][:12], "section:Git worktree", "Existing worktrees keep their base and files.", "muted")
                    if self.scope == "agent" and (ui.selected or "").startswith("thread:"):
                        row("    Prepare local branch…", "prepare_review", "Asks the agent to commit its completed work locally and create a review branch. No push or checkout switch.", "success")
                    if entity.get("reviewBranchRequested"):
                        row("    Requested branch: " + entity["reviewBranchRequested"], "section:Git worktree", "Read the agent's reply for completion and the actual commit.", "muted")
                else:
                    row("    Created on the next message", "section:Git worktree", "Starts from committed HEAD or Base branch; uncommitted changes stay in your normal checkout.", "muted")
            if section == "Project scripts":
                if not scripts:
                    row("    Import a repository to discover its scripts", "import_current", root or "Choose a repository first.", "muted")
                for script in scripts:
                    selected = script_selected(script, config)
                    role = {"dev": "Dev server", "extra": "Additional check"}.get(script["role"], FIELDS.get(script["role"], {}).get("label", "Check"))
                    row("    " + ("[x] " if selected else "[ ] ") + script["name"] + " · " + role,
                        "script:" + script["name"], script["command"] + " · Selects the command; does not run it.", "success" if selected else "base")
                continue
            provider = target_entity(data, ui.selected).get("provider", "codex")
            provider_name = {"copilot": "Copilot", "opencode": "OpenCode"}.get(provider, "Codex")
            access_key = provider + "Access"
            copilot = self.scope == "agent" and provider in ("copilot", "opencode")
            if section == "Access" and copilot:
                value = config.get(access_key, "Ask")
                row("    " + provider_name + " permissions: " + value, access_key, FIELDS[access_key]["help"], "success" if value == "Autonomous" else "base")
                row("    Ask: review permission requests", "access:help", "The CLI asks when an operation needs approval.", "muted")
                row("    Autonomous: allow files, network and tools", "access:help", "No routine approval prompts. Organization policies and user questions still apply. This is not a Codex sandbox.", "muted")
                actual = target_entity(data, ui.selected).get("reportedAccess", {}).get(access_key, "Not reported")
                row("    Current session: " + actual, "access:help", "Saved changes apply to the next new turn, not while steering an active one.", "warning" if actual != value else "muted")
                row("    " + provider_name + " CLI tool permissions", "section:Access", "In Ask mode, requests appear under Waiting. Manual approvals apply once.", "accent")
                row("    No Codex file or network sandbox", "section:Access", "The provider CLI controls access; worktrees isolate changes, not security.", "warning")
                continue
            if section == "Access":
                row("    Access explained…", "access:help", "Read what each profile permits and when changes apply.", "accent")
                row("    Applies on the next message to a ready agent", "access:help", "Steering an active turn keeps its current permissions.", "muted")
                for preset, (title, patch) in ACCESS_PRESETS.items():
                    chosen = all(config[k] == v for k, v in patch.items() if k != "networkAccess" or config["fileAccess"] != "Full access")
                    row("    " + ("[x] " if chosen else "[ ] ") + title, "access:" + preset,
                        "Sets file access, approvals and network together in this scope. Applies to the next new turn.", "success" if chosen else "base")
            if section == "Access" and self.scope == "agent":
                access = target_entity(data, ui.selected).get("reportedAccess", {})
                policy = access.get("sandbox") or {}
                if not isinstance(policy, dict):
                    policy = {}
                label = {"readOnly": "Read only", "workspaceWrite": "Workspace", "dangerFullAccess": "Full access", "externalSandbox": "External sandbox"}.get(policy.get("type"), "Not reported")
                row("    Current files: " + label, "section:Access", "Last policy reported by Codex or accepted for a turn.", "muted")
                row("    Current approvals: " + str(access.get("approvalPolicy") or "Not reported"), "section:Access", "Setup choices apply on the next turn, not during current work.", "muted")
                current_approval = {"on-request": "On request", "never": "Never", "untrusted": "Untrusted"}.get(access.get("approvalPolicy")) if isinstance(access.get("approvalPolicy"), str) else None
                pending = ((config["fileAccess"] != "Keep current" and config["fileAccess"] != label)
                           or (config["approvalMode"] != "Keep current" and config["approvalMode"] != current_approval)
                           or (config["fileAccess"] in ("Read only", "Workspace") and policy.get("networkAccess") != config["networkAccess"]))
                if pending:
                    row("    Saved changes pending next turn", "access:help", "The current session still has the policy shown above. Send a message when the agent is ready to apply your saved settings.", "warning")
            for f in fields:
                key, value = f["key"], config[f["key"]]
                if key in ("copilotAccess", "opencodeAccess"):
                    continue
                if copilot and key in ("effort", "tier"):
                    continue
                if key == "networkAccess" and config["fileAccess"] in ("Full access", "Keep current"):
                    row("    Network: " + ("Always allowed (full access)" if config["fileAccess"] == "Full access" else "Keep session policy"),
                        "access:help", "The network switch only applies with Read only or Workspace file access.", "muted")
                    continue
                choices = dict(choices_for(key, config, data.get("models", []), target_entity(data, ui.selected) if params["scope"] == "agent" else {}))
                if f["kind"] == "bool":
                    text = "    [x] " if value else "    [ ] "
                    text += f["label"]
                else:
                    display = choices.get(value, value) or ("Current HEAD" if key == "baseBranch" else "Automatic name" if key == "reviewBranch" else "Not set")
                    text = ("      " if key.endswith("Command") else "    ") + f["label"] + ": " + str(display).replace("\n", "; ")
                availability = ""
                if key in ("lint", "typecheck", "unit", "integration", "e2e", "build") and profile:
                    availability = " · " + (config[key + "Command"] or "No command detected; discover or set one")
                if key == "devPort" and profile.get("portSource"):
                    availability = " · Detected in " + profile["portSource"]
                row(text, key, f["help"] + availability + (" · Override in this scope" if key in raw else " · Inherited"),
                    "success" if f["kind"] == "bool" and value else "base")
                description = access_description(key, value)
                if description:
                    row("      " + description, "access:help", description, "muted")
        self.rows = rows
        self.index = min(self.index, len(rows) - 1)
        return rows

    def visible(self, ui, height):
        rows = self.build_rows(ui)
        self.scroll = min(max(0, self.scroll), max(0, len(rows) - height))
        return [{**row, "source_index": index, "setup_selected": index == self.index and ui.focus == "history"}
                for index, row in enumerate(rows[self.scroll:self.scroll + height], self.scroll)]

    def move(self, ui, delta, height):
        self.build_rows(ui)
        self.index = min(max(0, self.index + delta), len(self.rows) - 1)
        self.scroll = max(min(self.scroll, self.index), self.index - height + 1, 0)

    def saved(self, result):
        if "profile" in result:
            self.close_folder()
        self.pending = False
        self.acknowledged.append(result)
        if "profile" in result:
            self.scope = "agent" if result.get("target") else "project:" + result["profile"]["path"]
            self.index = self.scroll = 0

    def save(self, ui, patch=None, reset=False, params=None):
        if self.pending:
            return
        params = params or self.context(ui)[1]
        validate_patch(patch or {})
        self.pending = not ui.demo
        ui.submit("agent_setup", **params, patch=patch or {}, reset=reset)

    def edit(self, ui, key, value, params):
        if ui.wizard:
            return
        ui.drafts[ui.selected] = ui.buffer
        ui.wizard = {"kind": "setup", "field": key, "params": params,
                     "label": FIELDS[key]["label"] if key != "import" else "Project directory"}
        ui.buffer, ui.cursor, ui.focus = str(value), len(str(value)), "chat"
        ui.notice = "Enter saves · Esc returns to Chat"

    def activate(self, ui, index=None, direction=1):
        self.build_rows(ui)
        if index is not None:
            self.index = min(max(0, index), len(self.rows) - 1)
        action = self.rows[self.index]["action"]
        data, params, config, raw = self.context(ui)
        if action == "verify_repository":
            ui.submit("verify_repository", threadId=target_entity(data, ui.selected)["id"])
            return
        if action == "handoff":
            ui.begin_handoff()
            return
        if action.startswith("access:"):
            if action == "access:help":
                ui.panel, ui.panel_scroll = ACCESS_HELP, 0
            else:
                self.save(ui, dict(ACCESS_PRESETS[action.split(":", 1)[1]][1]))
            return
        if action.startswith("branch:"):
            if self.pending:
                return
            if action != "branch:cancel":
                picker = self.branch_picker
                value = picker["choices"][int(action.rsplit(":", 1)[1])]
                self.save(ui, {"baseBranch": value}, params=picker["params"])
            self.branch_picker = None
            self.index = self.scroll = 0
            return
        if action.startswith("folder:"):
            if self.pending:
                return
            if action == "folder:ok":
                self.pending = not ui.demo
                ui.submit("setup_import", path=str(self.folder), target=self.folder_target)
            elif action == "folder:cancel":
                self.close_folder()
            elif action == "folder:parent":
                self.browse_folder(ui, self.folder.parent)
            elif action == "folder:home":
                self.browse_folder(ui, Path.home())
            elif action.startswith("folder:open:"):
                self.browse_folder(ui, action[len("folder:open:"):])
            return
        if action == "scope":
            scopes = [key for key, _ in self.scopes(ui, data)]
            self.scope = scopes[(scopes.index(self.scope) + direction) % len(scopes)]
            return
        if action.startswith("section:"):
            name = action[len("section:"):]
            self.expanded.symmetric_difference_update({name})
            return
        if action == "preview":
            engine = "\n".join(FIELDS[k]["label"] + ": " + dict(choices_for(k, config, data.get("models", []), target_entity(data, ui.selected) if params["scope"] == "agent" else {})).get(config[k], config[k]) for k in ("model", "effort", "tier"))
            checks = ", ".join(FIELDS[k]["label"] for k in ("lint", "typecheck", "unit", "integration", "e2e", "build") if config[k]) or "None"
            provider = target_entity(data, ui.selected).get("provider", "codex")
            access_preview = ("\n" + provider.capitalize() + " permissions (next turn): " + config[provider + "Access"]
                              if params["scope"] == "agent" and provider in ("copilot", "opencode")
                              else "\nFile access (next turn): " + config["fileAccess"] + "\nApprovals (next turn): " + config["approvalMode"])
            ui.panel = ("AGENT SETUP PREVIEW\n\n## Overview\n" + engine + "\nWork mode: " + config["mode"]
                        + "\nSelected checks: " + ("None (Do not run)" if config["testScope"] == "Do not run" else checks)
                        + access_preview
                        + "\nCreate / update tests: " + ("On" if config["regressions"] else "Off")
                        + "\nDevelopment servers: " + config["processes"] + "\nDelivery: " + config["delivery"]
                        + "\n\n" + instruction_text(config))
            root = params.get("path") or project_root(target_entity(data, ui.selected))
            conflicts = guidance_conflicts(data.get("projectProfiles", {}).get(root, {}), config)
            if conflicts:
                ui.panel = ("AGENT SETUP PREVIEW\n\n## Instruction conflicts\n"
                            + "\n".join("- " + value for value in conflicts)
                            + "\nRepository instructions override Setup preferences. Explicit user instructions take precedence.\n"
                            + "Automatic check covers explicit rules only; the agent must review the full files.\n\n"
                            + ui.panel[len("AGENT SETUP PREVIEW\n\n"):])
            ui.panel_scroll = 0
            return
        if self.pending:
            return
        if action == "prepare_review":
            ui.submit("prepare_review", threadId=ui.selected.split(":", 1)[1])
            return
        if action == "import_current":
            path = params.get("path") or project_root(target_entity(data, ui.selected))
            if path:
                self.pending = not ui.demo
                ui.submit("setup_import", path=path, target=ui.selected if params.get("scope") == "agent" else None)
            else:
                self.browse_folder(ui)
        elif action.startswith("script:"):
            root = params.get("path") or project_root(target_entity(data, ui.selected))
            scripts = data.get("projectProfiles", {}).get(root, {}).get("scripts", [])
            script = next((s for s in scripts if s["name"] == action[7:]), None)
            if not script:
                return
            selected = script_selected(script, config)
            role, command = script["role"], script["command"]
            if role == "extra":
                commands = extra_commands(config)
                commands = [c for c in commands if c != command] if selected else commands + [command]
                self.save(ui, {"extraChecks": "\n".join(commands)})
            elif role == "dev":
                self.save(ui, {"devCommand": "" if selected else command})
            else:
                self.save(ui, {role: not selected, role + "Command": command})
        elif action == "import":
            self.browse_folder(ui)
        elif action == "baseBranch":
            root = params.get("path") or project_root(target_entity(data, ui.selected))
            branches = data.get("projectProfiles", {}).get(root, {}).get("baseBranches", [])
            choices = sorted(set(branches), key=lambda name: (0 if name == "main" else 1 if name.endswith("/main") else 2, name.casefold()))
            choices.append("")
            self.branch_picker = {"choices": choices, "value": config[action], "params": params, "owner": ui.selected}
            self.index = self.scroll = 0
            ui.focus = "history"
        elif action == "reset":
            self.save(ui, reset=True)
        elif FIELDS[action]["kind"] == "bool":
            self.save(ui, {action: not config[action]})
        elif FIELDS[action]["kind"] in ("choice", "model", "effort", "tier"):
            choices = [value for value, _ in choices_for(action, config, data.get("models", []), target_entity(data, ui.selected) if params["scope"] == "agent" else {})]
            current = choices.index(config[action]) if config[action] in choices else 0
            patch = {action: choices[(current + direction) % len(choices)]}
            if action == "model":
                patch.update(effort="", tier="")
            self.save(ui, patch)
        else:
            self.edit(ui, action, config[action], params)
