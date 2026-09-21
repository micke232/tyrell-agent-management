# OpenCode in Tyrell

OpenCode is a separate agent provider, alongside Codex and GitHub Copilot.
Tyrell uses OpenCode's CLI and HTTP/SSE server; it does not include model access,
an OpenCode subscription, API credits or credentials.

## Connect

```sh
npm install -g opencode-ai
tyrell login opencode
tyrell
```

`tyrell login opencode` runs OpenCode's own `auth login` command. Choose the
provider you want to use and follow its instructions. A supported existing login,
your own API key, or a configured local model can supply model access. An OpenCode
account is not a prerequisite imposed by Tyrell. Provider fees and restrictions
still apply; a subscription for one service does not imply access through another.

For local models, configure OpenCode using its
[provider documentation](https://opencode.ai/docs/providers/); login is unnecessary
when your local endpoint does not require it. Configure the model in your user
OpenCode configuration so it appears in Tyrell's model picker.

F10 shows the connection and available models. F3 creates an OpenCode agent.
Models use `provider/model` identifiers and remain separate from similarly named
Codex or Copilot models. Choose a folder in Setup; Git worktrees, instructions,
Plans, Files and Processes use the same Tyrell workflow as the other providers.

## Permissions and conversations

In the agent's Setup, **Ask** shows OpenCode tool permission requests in Tyrell.
**Autonomous** permits tools without routine approval dialogs. These are OpenCode
permission rules, not an operating-system sandbox. Questions from the model still
require an answer. Saved access changes apply to the next new turn.

Tool output appears in Tools. Text streams into Chat; plans appear in Plan.
Follow-up messages are handed to OpenCode's asynchronous prompt endpoint. A model
or workspace change cannot be applied while a turn is active. An existing OpenCode
session keeps its working folder; use a new agent or handover to change it.

Tyrell starts an authenticated server on a random loopback port and keeps OpenCode
session data in its own data folder. It reuses the user's OpenCode configuration
and file-based credentials, but does not import conversations from OpenCode's own
CLI or IDE. Login/config changes are picked up when no OpenCode agent is working.
External OpenCode plugins are disabled for this integration (`--pure`).

If a connection fails, Tyrell does not automatically resend the prompt. Inspect
its status and history before retrying. Session history is restored on reconnect;
closing only the dashboard leaves the background service working.

## Compatibility and verification

Validated with OpenCode CLI **1.18.31**. The integration requires the 1.x server API
and `serve --pure`; it checks the server version and required API endpoints before
using them. A future incompatible CLI is reported as unavailable.

The standard test suite covers provider routing, permission/question responses,
model isolation and delivery failures. An optional test exercises the actual CLI
against a local fake model, including streamed output, a harmless shell tool,
approval, plan updates and session restart. It makes no cloud model requests:

```sh
python3 scripts/check_opencode.py /path/to/opencode
```

Official references: [CLI](https://opencode.ai/docs/cli/),
[server API](https://opencode.ai/docs/server/),
[permissions](https://opencode.ai/docs/permissions/).
