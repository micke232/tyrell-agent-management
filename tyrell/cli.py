#!/usr/bin/env python3
import argparse
import asyncio
import json
import os
import shutil
import subprocess
import sys
import termios
from pathlib import Path

if sys.platform not in ('darwin', 'linux'):
    raise SystemExit('Tyrell Agent Management currently supports macOS and Linux terminals. Native Windows is not supported.')
try:
    import curses
except ImportError:
    raise SystemExit('Tyrell Agent Management needs a Python installation with curses support.')

from tyrell import __version__
from tyrell.paths import state_directory
from tyrell.hub_settings import local_report, diagnostics_text, provider_guide
from tyrell.client import ensure_service, request
from tyrell.codex_storage import environment as codex_environment
from tyrell.service import Service
from tyrell.ui import Dashboard
from tyrell.connections import copilot_executable, normalize_host
from tyrell.opencode_runtime import opencode_executable
from tyrell.terminal_setup import setup_terminal
from tyrell.startup import Startup, provider_status


def run_dashboard(ui):
    fd = sys.stdin.fileno()
    terminal = termios.tcgetattr(fd)
    try:
        curses.wrapper(ui.run)
    finally:
        try:
            termios.tcsetattr(fd, termios.TCSADRAIN, terminal)
        except termios.error:
            pass  # A closed terminal cannot be restored.


def prepare_dashboard(directory, codex, first_run):
    startup = Startup()
    startup.begin()
    startup.step('PRIMARY PROCESSOR ONLINE', lambda: ensure_service(directory, codex))
    saved = startup.step('MEMORY CORE VERIFIED',
                         lambda: request(directory, 'snapshot'))

    def restore():
        ui = Dashboard(directory)
        ui.data = saved
        return ui

    ui = startup.step('NAVIGATION INTERFACE ACTIVE', restore)

    def ready():
        connected = any(p.get('connected') for p in saved.get('providers', {}).values())
        if first_run or not connected:
            ui.panel = 'HUB SETTINGS'
        return connected

    startup.step('ENVIRONMENTAL CONTROL ONLINE', ready)
    if provider_status(saved) != 'OK':
        startup.write('PROVIDERS: ' + provider_status(saved) + '\n')
    startup.finish()
    return ui


def main():
    parser = argparse.ArgumentParser(prog="tyrell", description="Tyrell Agent Management. Closing the UI leaves agent work running.")
    parser.add_argument("--version", action="version", version="Tyrell Agent Management " + __version__)
    parser.add_argument("--state-dir", default=state_directory())
    parser.add_argument("--codex", default=os.environ.get("TYRELL_CODEX") or os.environ.get("CODEX_DASHBOARD_CODEX", shutil.which("codex") or "codex"))
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("service", help=argparse.SUPPRESS)
    sub.add_parser("status", help="Print persistent service state as JSON")
    sub.add_parser("setup", help="Choose a terminal and show installation checks and provider login instructions")
    sub.add_parser("stop", help="Stop the dashboard background service when all agents are idle")
    provider_start = sub.add_parser("start-provider", help="Start an installed provider server")
    provider_start.add_argument("provider", choices=["codex"])
    sub.add_parser("doctor", help="Read-only connection check; no turns are started")
    sub.add_parser("demo", help="Preview the terminal interface with example data")
    sub.add_parser("connections", help="Show Codex and Copilot connection status and reported models")
    login = sub.add_parser("login", help="Sign in to an agent provider using its own CLI")
    login.add_argument("provider", choices=["codex", "copilot", "opencode"])
    login.add_argument("--host", help="GitHub host, for example https://company.ghe.com")
    plan = sub.add_parser("plan", help="Save a planned task without starting it")
    plan.add_argument("--repo", required=True)
    plan.add_argument("--title", required=True)
    plan.add_argument("prompt")
    start = sub.add_parser("start", help="Start a planned task in an isolated Git worktree")
    start.add_argument("task_id")
    send = sub.add_parser("send", help="Send a message to a thread (steer if already active)")
    send.add_argument("thread_id")
    send.add_argument("message")
    select = sub.add_parser("select", help="Load and subscribe to a saved chat")
    select.add_argument("thread_id")
    interrupt = sub.add_parser("interrupt", help="Interrupt one active turn")
    interrupt.add_argument("thread_id")
    respond = sub.add_parser("respond", help="Respond to a pending server request using an explicit JSON object")
    respond.add_argument("request_id")
    respond.add_argument("response_json")
    args = parser.parse_args()
    directory = Path(args.state_dir).expanduser().resolve()
    try:
        if args.command == "service":
            asyncio.run(Service(directory, [args.codex, "app-server", "proxy"]).run())
            return
        if args.command in ("doctor", "setup"):
            report = local_report(args.codex)
            if args.command == "doctor":
                try:
                    snapshot = request(directory, "snapshot")
                    report["connections"] = snapshot.get("providers", {})
                    report["serviceVersion"] = snapshot.get("appVersion")
                except (OSError, RuntimeError):
                    report["serviceVersion"] = "Not running"
                print(json.dumps(report, indent=2))
            else:
                setup_terminal(directory)
                print(diagnostics_text(report))
                print("\n" + provider_guide("codex"))
                print("\n" + provider_guide("copilot"))
                print("\n" + provider_guide("opencode"))
                print("\nStart with: tyrell\nF10 Settings shows connections and lets you change the GitHub host.")
            return
        if args.command == "stop":
            import signal
            snapshot = request(directory, "snapshot")
            if snapshot.get("requests") or any(t.get("status", {}).get("type") == "active" for t in list(snapshot.get("threads", {}).values()) + list(snapshot.get("hiddenThreads", {}).values())):
                raise RuntimeError("Agents are working or waiting. Let them finish or interrupt them before stopping the service.")
            os.kill(snapshot["pid"], signal.SIGTERM)
            print("Dashboard service stop requested. Saved history and worktrees are kept.")
            return
        if args.command == "start-provider":
            report = local_report(args.codex)["providers"]["codex"]
            if not report["compatible"]:
                raise RuntimeError("Codex CLI lacks required app-server stdio support. Run tyrell doctor and install a compatible CLI.")
            ensure_service(directory, args.codex)
            print("Tyrell owns its private Codex server; connection status is available in F10 Settings.")
            return
        if args.command == "demo":
            demo = json.loads((Path(__file__).parent / "demo.json").read_text())
            run_dashboard(Dashboard(directory, demo))
            return
        if args.command == "login" and args.provider == "opencode":
            executable = opencode_executable()
            if not executable:
                raise RuntimeError("Install OpenCode CLI before signing in")
            if subprocess.call([executable, "auth", "login"]):
                raise RuntimeError("OpenCode sign-in did not complete")
            return
        if args.command == "login" and args.provider == "codex":
            executable = shutil.which(args.codex)
            if not executable:
                raise RuntimeError("Codex CLI is missing. Run tyrell setup for installation instructions.")
            codex_env, _ = codex_environment(directory)
            if subprocess.call([executable, "login"], env=codex_env):
                raise RuntimeError("Codex sign-in did not complete")
            return
        if args.command == "login":
            executable = copilot_executable()
            if not executable:
                raise RuntimeError("Install Copilot CLI before signing in")
            ensure_service(directory, args.codex)
            info = request(directory, "connections").get("copilot", {})
            host = normalize_host(args.host or info.get("expectedHost") or info.get("host") or "https://github.com")
            if args.host:
                request(directory, "copilot_host", host=host)
            # Let Copilot handle browser/terminal auth and its own credential store.
            result = subprocess.call([executable, "login", "--host", host])
            if result:
                raise RuntimeError("Copilot sign-in did not complete")
            print("Copilot sign-in completed. Tyrell Agent Management will refresh its connection automatically.")
            return
        if args.command is None and not (sys.stdin.isatty() and sys.stdout.isatty()):
            raise RuntimeError("Open the dashboard in an interactive terminal, or use `status` for JSON output")
        first_run = not (directory / "state.json").exists()
        if args.command is None and first_run and not (directory / "terminal.json").exists():
            setup_terminal(directory)
        if args.command is None:
            ui = prepare_dashboard(directory, args.codex, first_run)
            run_dashboard(ui)
            print("Dashboard detached. Agent work continues in the background.")
            return
        ensure_service(directory, args.codex)
        if args.command == "status":
            result = request(directory, "snapshot")
        elif args.command == "connections":
            result = request(directory, "connections")
        elif args.command == "plan":
            result = request(directory, "plan", repo=args.repo, title=args.title, prompt=args.prompt)
        elif args.command == "start":
            result = request(directory, "start", taskId=args.task_id)
        elif args.command == "send":
            result = request(directory, "send", threadId=args.thread_id, text=args.message)
        elif args.command == "select":
            result = request(directory, "select", threadId=args.thread_id)
        elif args.command == "interrupt":
            result = request(directory, "interrupt", threadId=args.thread_id)
        else:
            result = request(directory, "respond", requestId=args.request_id, response=json.loads(args.response_json))
        print(json.dumps(result, indent=2, ensure_ascii=False))
    except KeyboardInterrupt:
        print("\nDashboard detached. Agent work continues in the background.")
    except (OSError, ValueError, RuntimeError, curses.error) as error:
        print("tyrell: " + str(error), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
