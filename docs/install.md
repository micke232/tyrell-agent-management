# Tyrell Agent Management — install and share

They work. You take the credit.

A terminal dashboard for independent Codex and GitHub Copilot agents.
Start the app with `tyrell`. The Python package is `tyrell-agent-management`
and its module is `tyrell`.
This is a preview release: macOS is verified; Linux support is experimental.
Native Windows is not supported. The app needs Python 3.9+ with curses support.

## Install the dashboard

Share the wheel file and this guide. On macOS, if Python and pipx are missing:

```sh
brew install python pipx
pipx ensurepath
```

Open a new terminal, then install the wheel you received:

```sh
pipx install ./tyrell_agent_management-0.2.0a6-py3-none-any.whl
tyrell
```

The package is local; it has not been published to PyPI. Do not use
`pipx install tyrell-agent-management` until a trusted package registry is configured.
For Linux pipx installation, see https://pipx.pypa.io/stable/how-to/install-pipx.html.
No third-party Python runtime packages are required. Git is needed for worktrees,
file diffs and handovers. Ghostty is optional; terminal features vary.

## First start and F10 Settings

The first interactive start checks for Ghostty in PATH and the standard macOS
application folders. Choose Ghostty or keep your current terminal. If Ghostty is
missing and Homebrew is installed, you can explicitly choose to run
`brew install --cask ghostty`. Nothing is installed by default. Without Homebrew,
the setup provides the official download link; it does not install Homebrew.
Run `tyrell setup` later to repeat this choice. On other platforms, follow
https://ghostty.org/docs/install/binary for installation.

Other terminals are supported on a best-effort basis; mouse pointers, clipboard
shortcuts and keyboard reporting may differ. Choosing Ghostty does not replace
or reconfigure your current terminal: open Ghostty and run `tyrell` there.

After terminal selection, the first start opens **Settings**. Press **F10** or type `/settings` to return.
Settings displays provider connections, CLI compatibility, installed versions,
GitHub host and the local data folder. Click an action or press its displayed key.

- **1**: Codex installation and sign-in instructions.
- **2**: Copilot installation and sign-in instructions for the selected GitHub host.
- **D**: Recheck installed CLIs and required features.
- **H**: Change the GitHub host; use your organization's own hostname when applicable.

The dashboard is not a model runtime. Install and sign into at least one provider's
CLI separately. You do not need both. Subscriptions, organization policies and
model access come from your own provider account; no accounts or credentials are bundled.
The dashboard does not silently install CLIs or start browser login.

## Codex

```sh
npm install -g @openai/codex
tyrell login codex
tyrell doctor
tyrell start-provider codex
```

This integration currently requires `codex app-server proxy` and
`codex app-server daemon`. An arbitrary Codex CLI version is not guaranteed to work.
The diagnostics checks for these capabilities; local validation used Codex CLI
0.155.1. If your CLI does not provide them, use a compatible installation or Copilot.
Starting the provider is explicit; the hub never silently restarts a shared Codex server.
Official installation: https://developers.openai.com/codex/cli/.

## GitHub Copilot

The npm installation requires Node.js 22 or later:

```sh
npm install -g @github/copilot
tyrell login copilot
```

For GitHub Enterprise Cloud, set the correct host before signing in:

```sh
tyrell login copilot --host https://company.ghe.com
```

Alternatively set the host in F10 Settings and follow its sign-in instructions.
Your account needs Copilot access and your organization must allow CLI usage.
The integration requires headless JSON-RPC protocol 3; local validation used
Copilot CLI 1.0.86. The runtime checks protocol compatibility before starting work.
Official installation: https://docs.github.com/en/copilot/get-started/cli-quickstart.

## Troubleshooting

```sh
tyrell --version
tyrell setup
tyrell doctor
tyrell connections
```

`setup` prints installation and sign-in guidance without launching the UI.
`doctor` checks local features and reports live connections when the hub service
is running. CLI installation is not the same as authentication or model access.
If a new CLI is not detected, reopen your terminal and restart the idle hub service
so it receives the updated PATH. Use `tyrell demo` to preview without provider login.

## Update and uninstall

Closing the view leaves background work running. Before upgrading, wait until all
agents are ready, then:

```sh
tyrell stop
pipx install --force ./tyrell_agent_management-0.2.0a6-py3-none-any.whl
tyrell
```

Use the filename of the new release when upgrading. To remove the app:

```sh
tyrell stop
pipx uninstall tyrell-agent-management
```

History, worktrees and settings remain in `~/.tyrell`.
These are personal data: share the wheel, not this directory.
The existing source-based `codex dashboard` integration remains supported;
installing this package does not modify your shell or replace the Codex executable.

## Build a release

With Python build tools available:

```sh
python3 -m pip install build
python3 -m build
```

The wheel in `dist/` is the installable artifact. The source archive can also be
installed with pipx, but requires build dependencies. Build inputs are declared
in `pyproject.toml` and `setup.cfg`. Do not package a developer's home directory,
state, credentials, repositories or worktrees. Version tags trigger tested GitHub release drafts; see [the release guide](homebrew.md).

## Apple Terminal clipboard

Drag over chat text to select it, then click **Copy** in the header. This copies
only the selected text to the macOS clipboard, without the sidebar or box borders.
Selecting does not copy automatically. The **Text** button opens a separate plain
text view for Terminal's own selection and Cmd+C. Tab or Esc returns to the dashboard.
Live chat continues outside this explicit text view; a selected app region stays
stable until copied or deselected. Automated native-window checks cover the Copy
button path; OS-level Cmd+C and physical mouse gestures are not automated.

## File changes

Files refreshes automatically while open. Agent workspaces are also refreshed
when a Codex or Copilot turn completes. A folder opened with **Open folder…** is
refreshed independently and does not change the agent's working folder.
Feature-branch views include committed changes since the branch diverged from the
default branch; isolated worktrees use their stored base commit. The comparison
base is shown in Files. Large change sets show a limited list with an explicit warning.

## Startup

Run `tyrell` to show the green animated system readout. Provider connection
statuses are real; unavailable providers are shown as waiting, not OK. Press
any key to enter the dashboard. Ctrl+C or Ctrl+Q exits the startup screen.
Animation and key confirmation run only in an interactive terminal; status,
setup, help and other command output remain scriptable. `NO_COLOR` disables color.
The boot sequence reconnects to saved agents; it does not start new agent turns.

## Custom interface colors

Open **F10 Settings → Appearance** (shortcut **C**). Choose an element using
Up/Down and Text or Background using Left/Right, then press Enter. Choose among
named **Swatches**, the full **Palette**, or **Type color**. Typed input accepts
a displayed color name, `#RRGGBB` / `#RGB`, or `R,G,B` (also `rgb(R,G,B)`).
Hex and RGB values use the closest available terminal color, shown in the picker.
Tab switches input methods. Pasting a color stays in the color editor.

Click a swatch or use arrows to preview. Enter or **Save** commits the change;
Esc or **Cancel** discards the preview. The miniature app below shows all editable
color roles, including the sidebar, messages, code, selection and prompt. Click
an element in the miniature to edit it directly. In small windows, scroll the
preview; it also follows the selected element automatically. R restores the
selected element and **Use default colors** (D) restores all interface colors.

Settings are stored per user in `~/.tyrell/appearance.json` (or the
chosen state directory), independent of repositories, agents and model accounts.
Startup colors and every status color are fixed. Agent names have a separate
color so changing them cannot change the Waiting status. The editor keeps its
own readable colors even when previewing low-contrast combinations.
Terminals without 256-color support keep the existing fallback colors.

## Existing installations

If `~/.codex-dashboard` already exists, Tyrell reuses it so saved chats and worktrees
remain available. Nothing is moved automatically. `TYRELL_HOME` or `--state-dir`
selects another directory; `CODEX_DASHBOARD_HOME` remains a legacy fallback.
The old source launcher and `agent-hub` command remain compatibility entry points.
