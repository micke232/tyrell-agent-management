# Verification — 0.2.0a4

## Startup and naming

App and dashboard header now say Tyrell Agent Management, with the slogan
“They work. You take the credit.” The preferred command is `tyrell`; previous
commands and state directories remain compatible. Startup animates in soft
green (ANSI 108), displays real provider status, and waits for any key before
entering the dashboard. It does not start agent turns. Noninteractive commands
have no animation or confirmation. Terminal modes and colors are restored.

## Performance review

Reviewed UI rendering, input loop, polling and agent-switch snapshots, service
response time, retained formatting cache, background work and live processes.
The live service snapshot probe measured 3.73 ms median and 4.50 ms maximum
(12 sequential requests). One UI and one service were running; no duplicate
orphan dashboard processes were found. This is a point-in-time observation.

Width/cropping now uses ASCII fast paths and a bounded Unicode character-width
cache. Idle frames are skipped when data, focus and dimensions are unchanged.
Active updates poll every 150 ms; idle polling is 500 ms. Queued responses for
a previously selected agent are discarded at consumption as well as receipt.

Repeatable synthetic renderer benchmark, Python 3.9.6, 120 messages, 80 frames:

| Terminal | Streaming median before | After |
| --- | ---: | ---: |
| 120 × 40 | 2.830 ms | 1.040 ms |
| 240 × 60 | 5.537 ms | 1.220 ms |
| 400 × 80 | 10.275 ms | 1.450 ms |

These timings isolate rendering CPU cost, not model generation time. Run
`scripts/benchmark_ui.py` to reproduce the workload.

`scripts/check_performance.py` exercises the real curses and socket polling
loops in an isolated PTY with fictional agents: 120 messages, concurrent mouse
motion bursts, typed prompts and incoming replies. Final successful run:

- Prompt input under mouse bursts: median 35.69 ms, max 39.05 ms.
- Snapshot change to visible reply: median 110.89 ms, max 527.13 ms (includes idle-to-active transition).
- Unchanged idle repaints over 1.1 seconds: zero.
- Animated green startup pauses until a key is pressed.
- Terminal modes restored; macOS transient PENDIN bookkeeping is ignored.

1000 streamed revisions retained 120 formatting entries; memory growth after
warmup was 3473 bytes and peak traced allocation was 596860 bytes. This tests
cache retention, not a proof that every possible long-running session is leak-free.

## Regression and installation

All 116 discovered tests passed. Two stale assertions were updated: old app
branding and a hardcoded Setup preview row index. Eight focused startup,
installer and Homebrew tests passed again after adding the source-install alias.
Actual Apple Terminal clipboard checks passed after the rendering changes:
new replies visible during selection, forward/reverse selection, real
pbcopy/pbpaste and bracketed paste. Physical gestures are not automated.

The a4 wheel was built offline, checked against source, and installed in a
fresh Python 3.9 virtual environment. Both tyrell and agent-hub launchers report
a4; help and noninteractive setup passed. Homebrew assets are prepared locally
with a placeholder GitHub owner. Nothing has been published.

Follow-up: startup now uses the SYSTEM INITIALIZATION / INTERFACE 2037 text
and an 8 ms character delay. Four startup tests and the PTY check passed.
PTY latency measurement now uses one parent-process clock for both endpoints;
the values above replace earlier cross-process-clock measurements and include
the observation polling interval. This visual follow-up is in the source
checkout; the previously built a4 archive has not been rebuilt.
