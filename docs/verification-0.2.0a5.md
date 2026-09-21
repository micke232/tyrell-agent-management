# Verification — 0.2.0a5

Custom per-user interface colors are available in F10 Settings → Appearance.
No theme presets were added. Startup colors remain untouched, and all status
roles are protected (including the secondary planned-state color). Agent names
now have an independent role with the same default yellow.

All 122 discovered tests passed. After adding the protected secondary status
role, the 29 appearance/presentation tests passed again. Coverage includes
persistence, invalid/protected values, preview/cancel, saving, resetting,
70×18 layouts, mouse/keyboard navigation, failed writes, corrupt configuration,
limited-color terminals, default palette immutability and unchanged draft text.

The real curses/PTY integration passed: F10 → C → color picker, save a chosen
color, verify actual terminal color pairs and appearance.json, reset colors,
and verify status pairs and the unsent draft are unchanged. The same run also
verified the animated startup, key confirmation and input/reply updates.
Color-picker palette pairs are initialized only when the visible page changes.
Settings are written atomically with owner-only permissions to appearance.json,
independent of service state, workspaces and agent credentials.

The a5 wheel includes the current startup text and brighter slogan as well as
custom colors. Homebrew files remain local drafts using a placeholder owner.
