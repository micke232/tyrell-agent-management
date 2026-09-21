"""Persisted agent preferences and read-only project discovery.

Model and Codex access settings are API controls applied on the next turn.
Other preferences become task context; saving Setup never starts a command.
"""
import json
import re
import shlex
import subprocess
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit


def field(key, label, kind, default, help, choices=()):
    return {"key": key, "label": label, "kind": kind, "default": default,
            "help": help, "choices": list(choices)}


SECTIONS = [
    ("Model & speed", [
        field("model", "Model", "model", "", "Keep the current model, or choose one available from this agent's provider."),
        field("effort", "Reasoning", "effort", "", "More reasoning can take longer. Choices follow the selected model."),
        field("tier", "Speed", "tier", "", "Fast uses the model's advertised service tier and can increase usage."),
    ]),
    ("Access", [
        field("copilotAccess", "Copilot permissions", "choice", "Ask", "Ask shows CLI permission requests. Autonomous allows tools, files and network without routine prompts; organization policy still applies. Changes apply to the next new turn.", ("Ask", "Autonomous")),
        field("fileAccess", "File access", "choice", "Keep current", "Applies to the next turn. Workspace allows writes in the selected folder; Full access removes file restrictions.", ("Keep current", "Read only", "Workspace", "Full access")),
        field("approvalMode", "Approval requests", "choice", "Keep current", "On request asks when more permission is needed. Never shows no approval dialogs; sandbox limits still apply. Full access + Never allows autonomous execution.", ("Keep current", "On request", "Never")),
        field("networkAccess", "Network access", "bool", False, "Used with Read only or Workspace. Full access includes network; Keep current preserves the existing policy."),
    ]),
    ("Git worktree", [
        field("useWorktree", "Isolated worktree", "bool", True, "Default for Git folders. Created on the next message; the normal checkout stays unchanged."),
        field("baseBranch", "Base branch", "text", "", "Blank uses the selected repository's committed HEAD. Applies when creating a new worktree."),
        field("reviewBranch", "Local review branch", "text", "", "Optional local branch name. Prepare local branch asks the agent to commit its work and create this branch without switching your checkout."),
    ]),
    ("Communication", [
        field("verbosity", "Answer length", "choice", "Concise", "How much explanation to include in replies.", ("Concise", "Balanced", "Detailed")),
        field("language", "Conversation language", "choice", "Swedish", "Language for replies; project files follow repository conventions.", ("Swedish", "English", "Match my message")),
        field("updates", "Progress updates", "choice", "Milestones", "A plan is always required, regardless of this setting.", ("Milestones", "Frequent", "Only when blocked")),
        field("evidence", "Include test results", "bool", True, "Summarize actual checks and failures in the final answer."),
        field("fileLinks", "Link changed files", "bool", True, "Include useful file references in the final answer."),
        field("answerLinks", "Include reference links", "bool", True, "Include relevant documentation, preview, issue and report links."),
    ]),
    ("Task brief", [
        field("mode", "Work mode", "choice", "Implement & verify", "Plan only makes no changes; Review only reports findings.", ("Implement & verify", "Plan only", "Review only", "Investigate")),
        field("objective", "Objective", "text", "", "What should this agent achieve? This supplements your message."),
        field("acceptance", "Acceptance criteria", "text", "", "What must be true before the task is done? Separate points with semicolons."),
        field("scope", "Scope / exclusions", "text", "", "Files, areas or behaviors to include or leave alone."),
        field("issue", "Issue / ticket", "text", "", "Issue URL or ticket id, for example S3CS-1234."),
    ]),
    ("Project & references", [
        field("stack", "Technology stack", "text", "", "Detected from package.json; editable if the project changes."),
        field("node", "Node version", "text", "", "Use the project's Node version; do not upgrade it as incidental cleanup."),
        field("packageManager", "Package manager", "choice", "Auto", "Respect the existing lockfile.", ("Auto", "npm", "pnpm", "yarn", "bun")),
        field("repository", "Repository URL", "url", "", "GitHub or GitHub Enterprise repository, without credentials."),
        field("links", "Reference links", "text", "", "Documentation, designs, tickets and wiki URLs; one per line or semicolon."),
        field("previewLinks", "Preview / report links", "text", "", "PR preview and test report templates. Keep placeholders until a real PR exists."),
        field("guidance", "Repository guidance", "text", "", "Paths to architecture, PR and coding conventions the agent should read."),
        field("notes", "Additional instructions", "text", "", "Project-specific working preferences. Do not put tokens or passwords here."),
    ]),
    ("Project scripts", []),
    ("Tests & checks", [
        field("testScope", "Test scope", "choice", "Relevant checks", "Choose focused checks first, or the full applicable suite.", ("Relevant checks", "Full suite", "Do not run")),
        field("lint", "Lint", "bool", True, "Run the configured lint check when relevant."),
        field("typecheck", "Type checking", "bool", True, "Run the project's type check."),
        field("unit", "Unit tests", "bool", False, "Opt in when you want to run unit tests; avoid watch mode."),
        field("integration", "Integration tests", "bool", False, "Opt in when you want to run integration tests."),
        field("e2e", "End-to-end tests", "bool", False, "Playwright/browser tests can require credentials and a selected environment."),
        field("build", "Production build", "bool", False, "Verify a production build when relevant to the change."),
        field("regressions", "Create / update tests", "bool", False, "Opt in to writing tests. Independent of running existing tests."),
        field("lintCommand", "Lint command", "text", "", "A command preference for the agent; saving never runs it."),
        field("typecheckCommand", "Typecheck command", "text", "", "Project type-check command."),
        field("unitCommand", "Unit test command", "text", "", "Non-watch unit test command."),
        field("integrationCommand", "Integration command", "text", "", "Non-watch integration test command."),
        field("e2eCommand", "E2E command", "text", "", "Browser test command; use the configured base URL explicitly."),
        field("buildCommand", "Build command", "text", "", "Production build command."),
        field("extraChecks", "Extra checks", "text", "", "Optional i18n validation, circular dependencies or other checks."),
    ]),
    ("Runtime & ports", [
        field("processes", "Development servers", "choice", "Reuse existing", "Whether the agent may start its own persistent Node/dev server.", ("Reuse existing", "Own isolated server", "Do not start")),
        field("devCommand", "Dev server command", "text", "", "Command used only when a dev server is needed."),
        field("devPort", "Developer default port", "port", "", "Project/developer port. Agent-owned servers receive a separate port in Processes."),
        field("testPort", "Test server port", "port", "", "Separate preferred port for an agent-owned test server."),
        field("baseUrl", "Browser test URL", "url", "", "Explicit target for browser tests, including HTTPS if required."),
        field("environment", "Environment", "choice", "Local", "Select the intended target; no environment is contacted by this form.", ("Local", "Test", "QA", "PR preview")),
        field("install", "Install dependencies", "bool", False, "Allow necessary dependency installation using the existing lockfile and auth."),
        field("cleanup", "Stop owned processes", "bool", True, "Stop only processes started for this task when verification is complete."),
        field("artifacts", "Keep failure artifacts", "bool", True, "Retain relevant traces, screenshots and logs on failure."),
    ]),
    ("Infra", [
        field("terraform", "Check infrastructure changes", "bool", True, "Use these checks when the task changes Terraform; skip them otherwise."),
        field("terraformDir", "Terraform directory", "text", "", "Directory containing the Terraform root module, relative to the project."),
        field("terraformWorkspace", "Workspace", "text", "", "Use this existing workspace. Empty means inspect the current workspace."),
        field("terraformVars", "Variable files", "text", "", "Paths to tfvars files; never copy secret values into this form."),
        field("terraformFmt", "Format check", "bool", True, "Run terraform fmt -check for changed infrastructure."),
        field("terraformValidate", "Validate", "bool", True, "Validate configuration using the project's normal initialization workflow."),
        field("terraformPlan", "Generate a plan", "bool", False, "May read remote state/providers; use the selected environment and workspace."),
        field("terraformCommand", "Plan command / notes", "text", "", "Optional project-specific plan command or backend conventions."),
    ]),
    ("Engineering quality", [
        field("designSystem", "Reuse design system", "bool", True, "Prefer existing components and established patterns."),
        field("i18n", "Check translations", "bool", True, "Follow i18n conventions; check user-visible strings when relevant."),
        field("a11y", "Check accessibility", "bool", True, "Check labels, keyboard navigation, focus and semantics for UI changes."),
        field("responsive", "Check responsive layout", "bool", True, "Verify relevant narrow and wide layouts for UI changes."),
        field("dependencies", "Dependency changes", "choice", "Only if necessary", "Avoid unrelated upgrades or lockfile churn.", ("Only if necessary", "No dependency changes", "Allowed within task")),
        field("selfReview", "Review the final diff", "bool", True, "Look for regressions and unrelated edits before handing over."),
    ]),
    ("Git & handover", [
        field("branch", "Branch naming", "text", "", "Branch convention; for example include the ticket id."),
        field("delivery", "Delivery", "choice", "Local changes", "Choose the requested handover. Never merge or deploy implicitly.", ("Local changes", "Commit locally", "Draft pull request")),
        field("prTemplate", "Use PR template", "bool", True, "Follow the repository PR template and fill in real validation results."),
        field("handover", "Handover notes", "text", "", "Anything the reviewer or next developer should know."),
    ]),
]
FIELDS = {f["key"]: f for _, group in SECTIONS for f in group}
DEFAULTS = {k: f["default"] for k, f in FIELDS.items()}


def validate_patch(patch):
    if not isinstance(patch, dict) or set(patch) - FIELDS.keys():
        raise ValueError("Unknown setup setting")
    result = {}
    for key, value in patch.items():
        f = FIELDS[key]
        if f["kind"] == "bool":
            if type(value) is not bool:
                raise ValueError(f["label"] + " must be checked or unchecked")
        else:
            if not isinstance(value, str) or len(value) > 8000 or any(ord(c) < 32 and c not in "\n\t" for c in value):
                raise ValueError(f["label"] + " must be plain text, at most 8000 characters")
            value = value.strip()
            if f["choices"] and value not in f["choices"]:
                raise ValueError("Invalid choice for " + f["label"])
            if f["kind"] == "port" and value and (not value.isascii() or not value.isdigit() or not 1 <= int(value) <= 65535):
                raise ValueError("Ports must be between 1 and 65535")
            if f["kind"] == "url" and value and not safe_url(value):
                raise ValueError("Use an http(s) URL without embedded credentials")
            if key in ("links", "previewLinks") and any(not safe_url(u) for u in re.findall(r"https?://[^\s;]+", value)):
                raise ValueError("Reference URLs must not contain credentials")
        result[key] = value
    return result


def safe_url(value):
    try:
        u = urlsplit(value)
        return u.scheme in ("http", "https") and bool(u.hostname) and not u.username and not u.password
    except ValueError:
        return False


def target_entity(data, target):
    prefix, _, ident = (target or "").partition(":")
    if prefix == "task":
        return next((t for t in data.get("tasks", []) if t["id"] == ident), {})
    if prefix == "hidden":
        return data.get("hiddenThreads", data.get("threads", {})).get(ident, {})
    collection = {"thread": "threads", "archive": "archived", "hidden": "hiddenThreads"}.get(prefix)
    return data.get(collection, {}).get(ident, {}) if collection else {}


def project_root(entity):
    value = entity.get("projectRoot") or entity.get("repo") or entity.get("cwd")
    return str(Path(value).expanduser().resolve()) if value else ""


def effective_config(data, target=None, project=None, defaults_only=False):
    result = dict(DEFAULTS)
    settings = data.get("settings", {})
    result.update({k: settings[v] for k, v in (("model", "nextModel"), ("effort", "nextEffort")) if settings.get(v)})
    result.update(settings.get("agentConfig", {}))
    entity = target_entity(data, target)
    if not defaults_only:
        root = project if project is not None else project_root(entity)
        result.update(data.get("projectProfiles", {}).get(root, {}).get("config", {}))
        if target:
            if not entity.get("inheritsSetupEngine"):
                result.update({k: entity[v] for k, v in (("model", "nextModel"), ("effort", "nextEffort")) if entity.get(v)})
            result.update(entity.get("agentConfig", {}))
    if entity.get("provider") == "copilot" and not defaults_only:
        # Provider-specific engine/access values never inherit Codex defaults.
        own = entity.get("agentConfig", {})
        result.update(model=own.get("model", ""), effort="", tier="",
                      fileAccess="Keep current", approvalMode="Keep current", networkAccess=False)
    return result


def model_info(config, models, entity=None):
    name = config["model"] or (entity or {}).get("model")
    return next((m for m in models if m["model"] == name), None) or next((m for m in models if m.get("isDefault")), {})


def choices_for(key, config, models, entity=None):
    model = model_info(config, models, entity)
    if key == "model":
        return [("", "Keep current / " + ("Copilot" if (entity or {}).get("provider") == "copilot" else "Codex") + " default")] + [(m["model"], m.get("displayName", m["model"])) for m in models if not m.get("hidden")]
    if key == "effort":
        return [("", "Keep current / model default")] + [(e["reasoningEffort"], e["reasoningEffort"].capitalize()) for e in model.get("supportedReasoningEfforts", [])]
    if key == "tier":
        return [("", "Keep current"), ("default", "Standard")] + [(t["id"], t["name"] + " · " + t.get("description", "")) for t in model.get("serviceTiers", [])]
    return [(v, v) for v in FIELDS[key]["choices"]]


def validate_engine(config, models, entity=None):
    model = model_info(config, models, entity)
    if config["model"] and not any(m["model"] == config["model"] for m in models):
        raise ValueError("This model is not in the available model catalog")
    if config["effort"] and config["effort"] not in [e["reasoningEffort"] for e in model.get("supportedReasoningEfforts", [])]:
        raise ValueError("This model does not support the selected reasoning level")
    if config["tier"] not in ("", "default") and config["tier"] not in [t["id"] for t in model.get("serviceTiers", [])]:
        raise ValueError("This model does not advertise the selected speed tier")


def instruction_text(config):
    """The preview and outgoing context share this sectioned source of truth."""
    sections = []
    def section(title, values):
        sections.append("## " + title + "\n" + "\n".join("- " + str(value).replace("\n", "\n  ") for value in values if value))
    def fields(keys):
        return [FIELDS[key]["label"] + ": " + str(config[key]) for key in keys if config[key]]
    mode = config["mode"]
    section("Task", [
        "Explicit task instructions and repository instructions take precedence.",
        "Always maintain a visible plan, even for a one-step task.",
        {"Plan only": "Plan only: investigate read-only and produce a plan; do not modify files or start implementation.",
         "Review only": "Review only: inspect read-only and report actionable findings; do not modify files.",
         "Investigate": "Investigate the problem first. Report evidence and proposed next steps without implementing changes.",
         "Implement & verify": "Implement the requested task, verify the result and review the diff."}[mode],
        *fields(("objective", "acceptance", "scope", "issue")),
    ])
    section("Communication", fields(("verbosity", "language", "updates")) + [
        "Report the checks actually run, their results, and anything not verified." if config["evidence"] else "",
        "Include useful links to changed files." if config["fileLinks"] else "",
        "Include relevant reference and preview/report links; never invent a URL or resolve a PR placeholder without its real id." if config["answerLinks"] else "",
    ])
    section("Project & references", fields(("stack", "node", "packageManager", "repository", "links", "previewLinks", "guidance", "notes")))
    if config["guidance"]:
        section("Repository instructions", [
            "Read the listed repository guidance files, including AGENTS.md, before acting. Read any ancestor or nested AGENTS.md that applies to the files you work on.",
            "Repository instructions take precedence over Setup preferences; explicit user instructions still take precedence over repository guidance. Higher-priority system and developer instructions always apply.",
            "If instructions conflict, identify the sources and the conflicting requirements explicitly before the affected action. Do not silently ignore either source. The Setup conflict check is partial, not a complete review.",
        ])
    checks = []
    if config["testScope"] == "Do not run" or mode in ("Plan only", "Review only", "Investigate"):
        checks.append("Do not execute test/build/dev commands in this mode; list the recommended checks and state they were not run.")
    else:
        checks.append("Verification scope: " + config["testScope"] + ". Run relevant selected checks; do not use watch mode.")
        for key in ("lint", "typecheck", "unit", "integration", "e2e", "build"):
            checks.append(FIELDS[key]["label"] + ": " + (config[key + "Command"] or "discover the repository's existing command")
                          if config[key] else "Not requested by setup: " + FIELDS[key]["label"])
        if config["extraChecks"]:
            checks.append("Additional verification: " + config["extraChecks"])
        checks.append("Do not run unselected tests unless explicitly requested or required by repository instructions.")
    checks.append("Add meaningful regression coverage where warranted; explain when a test is unnecessary." if config["regressions"]
                  else "Do not create or update tests unless explicitly requested or required by repository instructions. Suggest useful tests for later.")
    section("Verification & test creation", checks)
    section("Runtime & ports", [
        {"Reuse existing": "Reuse an appropriate existing dev server; do not start your own persistent server. If none is available, report the missing prerequisite.",
         "Own isolated server": "You may start a separate dev/test server when needed. Check port availability first; keep its PID, command, working directory and URL.",
         "Do not start": "Do not start persistent dev servers or background Node processes."}[config["processes"]],
        *fields(("devCommand", "devPort", "testPort", "baseUrl", "environment")),
        "Pass the chosen URL explicitly to browser tests; do not silently use a shared or production target. Never kill someone else's processes or use broad pkill commands.",
        "Stop the processes you started for this task when done." if config["cleanup"] else "Leave task-owned servers running only if useful; report their PID and URL for later cleanup.",
        "Keep useful failure artifacts and report their paths." if config["artifacts"] else "",
    ])
    if config["terraform"]:
        chosen = [label for key, label in (("terraformFmt", "fmt -check"), ("terraformValidate", "validate"), ("terraformPlan", "plan")) if config[key]]
        section("Infra / Terraform", [
            "For Terraform changes, inspect the repository's root modules, backend and existing workspace first.",
            *fields(("terraformDir", "terraformWorkspace", "terraformVars", "terraformCommand")),
            "Selected Terraform checks: " + (", ".join(chosen) or "none") + ". Obey the work mode and verification scope; do not execute checks in read-only/planning mode.",
            "Never run Terraform apply, destroy, import, state mutation, workspace creation or backend migration as part of verification. A plan may read remote state; do not expose sensitive plan contents in logs or chat.",
        ])
    quality = {"designSystem": "Reuse the existing design system and repository patterns.",
               "i18n": "Check translation conventions and user-visible strings where relevant.",
               "a11y": "Check keyboard access, focus, labels and semantics for UI changes.",
               "responsive": "Check appropriate narrow and wide layouts for UI changes.",
               "selfReview": "Review the final diff for regressions and unrelated changes."}
    section("Engineering quality", [text for key, text in quality.items() if config[key]] + ["Dependency changes: " + config["dependencies"]])
    section("Delivery", [
        {"Local changes": "Leave changes locally for review; do not commit, push or open a PR unless the task explicitly asks.",
         "Commit locally": "Commit the task's changes locally after verification. Do not push or publish unless explicitly requested.",
         "Draft pull request": "After verification, prepare a draft PR for the requested changes using the repository template and actual test evidence."}[config["delivery"]],
        *fields(("branch", "handover")),
        "Follow the repository PR template when preparing a PR." if config["prTemplate"] else "",
        "Never merge or deploy implicitly. Preserve the user's unrelated changes.",
    ])
    section("Boundaries", [
        "These preferences do not change tool permissions. Saving them does not start processes, publish, or run tests.",
        "You may install necessary dependencies using the existing lockfile and configured registry credentials." if config["install"] else "Do not install dependencies automatically; report missing prerequisites.",
        "Never read, print, copy or persist credential values into setup, chat, commits or logs. Use existing authentication mechanisms.",
    ])
    return "Agent setup preferences for this task.\n\n" + "\n\n".join(sections)


SCRIPT_CANDIDATES = {
    "lint": ("lint", "lint:ci", "check:lint"),
    "typecheck": ("typecheck", "type-check", "type:check", "check:types", "check-types", "tsc"),
    "unit": ("test:unit", "test:unit:run", "test"),
    "integration": ("test:int", "test:integration", "test:integration:run"),
    "e2e": ("test:e2e", "e2e", "test:playwright"),
    "build": ("build",),
    "dev": ("dev", "start", "serve", "develop"),
}


def guidance_conflicts(profile, config):
    conflicts, seen = list(profile.get("instructionConflicts", [])), {}
    for rule in profile.get("guidanceRules", []):
        key, value = rule["key"], rule["value"]
        if key not in FIELDS:
            continue
        for previous in seen.get(key, []):
            if previous["value"] != value:
                conflicts.append(previous["source"] + " conflicts with " + rule["source"]
                                 + ": " + FIELDS[key]["label"] + " has opposing requirements. Check file scope and precedence.")
        seen.setdefault(key, []).append(rule)
        mismatch = config[key] not in ("Auto", value) if key == "packageManager" else config[key] != value
        if mismatch:
            conflicts.append(rule["source"] + ": " + FIELDS[key]["label"] + " requires " + str(value)
                             + "; Setup selects " + str(config[key]) + ".")
        if value is True and key in ("unit", "integration", "e2e", "lint", "typecheck", "build"):
            if config["testScope"] == "Do not run" or config["mode"] in ("Plan only", "Review only", "Investigate"):
                conflicts.append(rule["source"] + ": required " + FIELDS[key]["label"]
                                 + " is restricted by the selected work mode / verification scope.")
    return list(dict.fromkeys(conflicts))


def guidance_rules(text, source):
    rules, fence = [], None
    required = r"^(?:(?:you must|must|always|du ska|du måste|ska|måste) )?(?:run|kör)(?: alltid)? (?:the |all |alla )?"
    forbidden = r"^(?:(?:do not|don't|never) run (?:the |any )?|kör (?:inte|aldrig) (?:några )?)"
    targets = [(r"(?:unit tests|unit-tests|enhetstester|tests|tester)", "unit"),
               (r"(?:integration tests|integrationstester)", "integration"),
               (r"(?:e2e tests|end-to-end tests|e2e-tester)", "e2e"),
               (r"(?:lint|linting)", "lint"),
               (r"(?:typecheck|type checking|type check|typkontroll)", "typecheck"),
               (r"(?:production build|produktionsbygge)", "build")]
    for number, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        if stripped.startswith(("```", "~~~")):
            marker = stripped[:3]
            fence = None if fence == marker else marker if fence is None else fence
            continue
        if fence or stripped.startswith(">"):
            continue
        lower = re.sub(r"^(?:[-*+] |\d+[.)] )", "", stripped).replace("**", "").replace("`", "").lower()
        # Conditional guidance needs agent review; do not label it unconditional.
        if re.search(r"\b(if|unless|when|om|såvida|när)\b", lower):
            continue
        origin = source + ":" + str(number)
        manager = re.search(r"^(?:(?:always|must|you must|du ska|du måste) )?(?:use|använd)(?: alltid)? (npm|pnpm|yarn|bun)\b", lower)
        if manager:
            rules.append({"source": origin, "key": "packageManager", "value": manager[1]})
        for target, key in targets:
            for prefix, value in ((required, True), (forbidden, False)):
                if re.search(prefix + target + r"\b", lower):
                    rules.append({"source": origin, "key": key, "value": value})
        for pattern, value in ((r"^(?:do not|don't|never) (?:create|write|add) (?:new )?tests\b|^(?:skriv|skapa|lägg till) (?:inte|aldrig) (?:nya )?tester\b", False),
                               (r"^(?:always|must|you must) (?:create|write|add) (?:new )?tests\b|^(?:skriv|skapa) alltid (?:nya )?tester\b", True)):
            if re.search(pattern, lower):
                rules.append({"source": origin, "key": "regressions", "value": value})
    return rules


def script_catalog(scripts, manager):
    result = []
    for name in sorted(scripts):
        if not isinstance(scripts[name], str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9:._/-]{0,119}", name):
            continue
        role = next((key for key, candidates in SCRIPT_CANDIDATES.items() if name in candidates), "extra")
        result.append({"name": name, "role": role, "command": manager + " run " + shlex.quote(name)})
    return result


def discover_project(directory):
    root = Path(directory).expanduser().resolve()
    if not root.is_dir():
        raise ValueError("Project directory does not exist")
    def read(name):
        path = root / name
        # Discovery never follows an allowlisted filename out of the project.
        if not path.is_file() or path.is_symlink() or not path.resolve().is_relative_to(root) or path.stat().st_size > 500000:
            return ""
        return path.read_text(errors="replace")
    raw = read("package.json")
    package = json.loads(raw) if raw else {}
    if not isinstance(package, dict):
        raise ValueError("package.json must contain an object")
    dependencies = {**package.get("dependencies", {}), **package.get("devDependencies", {})}
    names = {"react": "React", "typescript": "TypeScript", "vite": "Vite", "@mui/material": "MUI", "@reduxjs/toolkit": "Redux Toolkit", "react-hook-form": "React Hook Form", "react-router": "React Router", "i18next": "i18next", "@biomejs/biome": "Biome", "vitest": "Vitest", "@playwright/test": "Playwright", "msw": "MSW"}
    config = {"stack": ", ".join(name + " " + dependencies[key] for key, name in names.items() if key in dependencies)}
    if not raw and (root / "dashboard.py").is_file():
        config["stack"] = "Python 3, curses, asyncio, unittest"
    config["node"] = read(".nvmrc").strip() or package.get("engines", {}).get("node", "")
    manager = package.get("packageManager", "").split("@")[0]
    if manager not in ("npm", "pnpm", "yarn", "bun"):
        manager = next((tool for lock, tool in (("pnpm-lock.yaml", "pnpm"), ("yarn.lock", "yarn"), ("bun.lock", "bun"), ("package-lock.json", "npm")) if (root / lock).is_file()), "npm" if raw else "Auto")
    config["packageManager"] = manager
    scripts = package.get("scripts", {})
    if not isinstance(scripts, dict):
        raise ValueError("package.json scripts must contain an object")
    catalog = script_catalog(scripts, manager)
    commands = {s["name"]: s["command"] for s in catalog}
    for key, candidates in SCRIPT_CANDIDATES.items():
        name = next((n for n in candidates if n in scripts), None)
        if name in commands:
            config[key + "Command"] = commands[name]
    readme = read("README.md")
    urls = [u.rstrip(".,)") for u in re.findall(r"https?://[^\s<>`\"]+", readme)]
    local = next((u for u in urls if safe_url(u) and urlsplit(u).hostname in ("localhost", "127.0.0.1")), "")
    if local:
        config["baseUrl"] = local
        config["devPort"] = str(urlsplit(local).port or (443 if local.startswith("https:") else 80))
    # Read literal port declarations only; never import/execute configuration.
    dev_name = next((n for n in SCRIPT_CANDIDATES["dev"] if n in commands), None)
    dev_script = scripts.get(dev_name, "")
    match = re.search(r"(?:--port(?:=|\s+)|\bPORT=)([0-9]{1,5})\b", dev_script)
    port_source = "package.json" if match else None
    config_sources = []
    if not match:
        for filename in ("vite.config.ts", "vite.config.js", "vite.config.mts", "webpack.config.js", "vue.config.js"):
            contents = read(filename)
            if contents:
                config_sources.append(filename)
                match = re.search(r"\b(?:server|devServer)\s*:\s*\{[^}]*?\bport\s*:\s*([0-9]{1,5})\b", contents, re.S)
                if match:
                    port_source = filename
                    break
    if match and 1 <= int(match[1]) <= 65535:
        config["devPort"] = str(int(match[1]))
        scheme = urlsplit(local).scheme if local else "http"
        config["baseUrl"] = scheme + "://localhost:" + config["devPort"]
    try:
        result = subprocess.run(["git", "-C", str(root), "remote", "get-url", "origin"], capture_output=True, text=True, timeout=3)
        remote = result.stdout.strip() if result.returncode == 0 else ""
    except (OSError, subprocess.TimeoutExpired):
        remote = ""
    if remote.startswith("git@") and ":" in remote:
        remote = "https://" + remote[4:].replace(":", "/", 1)
    if remote.startswith(("https://", "http://", "ssh://")):
        u = urlsplit(remote)
        remote = urlunsplit(("https", u.hostname or "", u.path.removesuffix(".git"), "", ""))
    config["repository"] = remote if safe_url(remote) else ""
    config["links"] = "\n".join(dict.fromkeys(u for u in urls if safe_url(u) and ("/wiki" in u or u.rstrip("/") == config["repository"])))
    template = read(".github/pull_request_template.md")
    config["previewLinks"] = "\n".join(dict.fromkeys(u.rstrip(".,)") for u in re.findall(r"https?://[^\s<>`]+", template) if safe_url(u)))
    candidates = sorted(p.name for p in root.iterdir() if p.name.casefold() in ("agents.md", "agents.override.md", "agent.md", "readme.md"))
    if any(name.casefold() == "agents.override.md" for name in candidates):
        candidates = [name for name in candidates if name.casefold() != "agents.md"]
    candidates += [".github/copilot-instructions.md", ".github/pull_request_template.md"]
    guidance = [name for name in candidates if read(name)]
    inherited = []
    for parent in reversed(root.parents):
        candidate = parent / "AGENTS.override.md"
        if not candidate.is_file():
            candidate = parent / "AGENTS.md"
        if candidate.is_file() and not candidate.is_symlink() and candidate.stat().st_size <= 500000:
            inherited.append(candidate)
    config["guidance"] = "; ".join([str(p) for p in inherited] + guidance)
    rules = []
    for path in inherited:
        rules.extend(guidance_rules(path.read_text(errors="replace"), str(path)))
    for name in guidance:
        if Path(name).name.casefold() in ("agents.md", "agents.override.md", "agent.md"):
            rules.extend(guidance_rules(read(name), name))
    if "PLAYWRIGHT_BASE_URL" in read("playwright.config.ts"):
        config["notes"] = "For Playwright, set PLAYWRIGHT_BASE_URL explicitly to the chosen target. Follow the project's existing authentication and certificate setup."
    terraform_roots = [name for name in (".", "terraform", "infra", "infrastructure") if any((root / name).glob("*.tf"))]
    if len(terraform_roots) == 1:
        config["terraformDir"] = terraform_roots[0]
    git_root = None
    base_branches = []
    try:
        probe = subprocess.run(["git", "-C", str(root), "rev-parse", "--show-toplevel"], capture_output=True, text=True, timeout=3)
        if probe.returncode == 0:
            git_root = probe.stdout.strip()
            branches = subprocess.run(["git", "-C", str(root), "for-each-ref", "--format=%(refname:short)", "refs/heads", "refs/remotes"], capture_output=True, text=True, timeout=3)
            if branches.returncode == 0:
                base_branches = [line for line in branches.stdout.splitlines() if not line.endswith("/HEAD")]
    except (OSError, subprocess.TimeoutExpired):
        pass
    config = validate_patch(config)
    return {"baseBranches": base_branches, "guidanceRules": rules, "gitRoot": git_root, "hasPackageJson": (root / "package.json").is_file(), "name": package.get("name") or root.name, "path": str(root), "config": config,
            "detectedConfig": dict(config), "scripts": catalog, "portSource": port_source or ("README.md" if local else None),
            "sources": list(dict.fromkeys([name for name in ("package.json", ".nvmrc", "playwright.config.ts") if read(name)] + guidance + config_sources))}
