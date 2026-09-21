# Verification — 0.2.0a3

Tyrell Agent Management. Header: Tyrell Hub Management - They work. You take the credit.

56 focused tests passed across connections/header layout, native clipboard,
responsiveness, presentation, clipboard, delivery, composer and Homebrew packaging.
Header checks cover widths 70, 90 and 160 and all provider states; the full slogan
appears when space permits without overlapping connection badges.

The completed-selection freeze was reproduced by a failing regression test,
then fixed. New replies render before Copy while unchanged selections remain
usable. Changed/trimmed source rows invalidate selection safely. Actual Apple
Terminal integration passed for incoming replies before copying, forward/reverse
selection, real pbcopy/pbpaste and prompt input. The original clipboard was
restored. Input reports were injected via AppleScript; physical gestures and
OS-level Cmd+C were not automated.

The a3 wheel was built offline and verified against source. It installs without
network dependencies in a temporary Python 3.9 virtual environment. Version
and help report the new name. Homebrew assets were regenerated locally with a
placeholder GitHub owner; nothing has been published. Existing launch commands,
package identifier and user state paths are preserved.
