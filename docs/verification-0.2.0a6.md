# Verification — 0.2.0a6

F10 contents are indented below headings; action lookup and keyboard scrolling
handle that indentation. Appearance now offers named swatches, an unnumbered
full palette, and typed/pasted names, hex or RGB. Changes preview before Save.
The clickable mock app covers every editable color role and scrolls in small
terminals. Startup/status locking remains enforced without explanatory UI text.

All 126 discovered tests passed. Tests cover F10 arrow/Enter and mouse navigation, named/hex/RGB parsing and
invalid input, paste isolation from the chat draft, preview/cancel/save/reset,
all mock-preview roles, clicking the preview, small windows and persistence.
Real curses/PTY verification passed for F10 → four Down keys → Enter, typing
a hex color, actual terminal color pair changes, persistence and reset. Existing
status pairs and the unsent draft remain unchanged.

The a6 package and Homebrew release files are prepared locally; no publishing
has taken place. The startup implementation was not changed for this revision.
