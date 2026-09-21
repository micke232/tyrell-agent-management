# Verification — 0.2.0a7

Local validation on macOS, Python 3.14.7:

- 152 unit/integration tests passed (`python3 -B -m unittest discover -s tests`).
- Real Codex CLI 0.155.1: two temporary homes cannot read each other's sessions;
  private sessions survive restart; copied legacy history resumes from the private
  path and the shared original is archived. No model request was sent.
  Reproduce with `python3 scripts/check_session_isolation.py`.
- Real OpenCode CLI 1.18.31 with a local fake model: model discovery, authenticated
  HTTP/SSE, streamed reply, shell permission, harmless command output, plan,
  completion and session restart passed. No cloud model requests or account login.
  Reproduce with `python3 scripts/check_opencode.py /path/to/opencode`.
- Real PTY performance check passed: median input 15.64 ms, maximum 18.39 ms;
  mouse-burst input median 12.46 ms; no idle history redraws. These are fixture
  measurements, not a claim about every terminal or live model latency.
- Reproducible wheel/Homebrew archive and formula checksum verification passed.
- Wheel installed in a clean virtual environment; CLI checked outside the checkout.
- Actual Homebrew virtualenv installer and linked command passed in a temporary
  prefix, without replacing the user's installed application.

The existing CI job now also runs the pinned OpenCode CLI test with the local
model fixture. Paid-provider authentication, billing and organization-specific
access policies were not exercised. The user's running dashboard and shared
Codex daemon were not restarted during these checks.
