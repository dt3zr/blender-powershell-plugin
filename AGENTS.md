# Repository Guidelines

## Project Structure

- `src/powershell_console/__init__.py` is the Blender add-on entry point. It contains registration, the 3D View sidebar, pane creation, input handling, rendering, and terminal session cleanup.
- `src/powershell_console/powershell_terminal_vendor/` bundles `pywinpty`, `pyte`, and `wcwidth`, including native binaries, licenses, metadata, and upstream WinPTY tests.
- `scripts/build.ps1` packages the add-on. `docs/specs.md` records installation steps, architecture, dependency versions, and previous verification.

## Build and Run

From the repository root, run `powershell -NoProfile -File .\scripts\build.ps1`. The script checks bundled dependencies and writes `output/powershell_console-<version>.zip` using the version in `bl_info`. It excludes Python bytecode caches and verifies the ZIP layout.

In Blender, open **Edit → Preferences → Add-ons**, choose **Install from Disk**, select the ZIP, and enable **PowerShell Console**. In the 3D View, press **N**, select **PowerShell**, and click **Open PowerShell Pane**. Use Windows x64 with ConPTY and a Blender build with matching Python 3.13 x64 binaries. Keep `pwsh.exe` or `powershell.exe` on Blender's PATH.

## Coding Style

Use four-space indentation, `snake_case` for functions and variables, `PascalCase` for ordinary Python classes, and Blender's existing `POWERSHELL_OT_*` and `POWERSHELL_PT_*` class patterns. Follow the local PowerShell style in the build script. Keep vendor changes focused on dependency updates and preserve package metadata and licenses.

## Testing

Use the packaged ZIP for Blender smoke tests. Open the docked pane, run `Get-Location`, resize the pane, type `exit`, and reopen it. Verify that disabling and re-enabling the add-on succeeds. For changes to input or rendering, also exercise an installed full-screen terminal application. The upstream WinPTY tests live in `powershell_terminal_vendor/winpty/tests/test_*.py`. Verify add-on behavior in Blender alongside any dependency tests.

## Commits and Pull Requests

Use short, imperative commit subjects, such as “Fix terminal cleanup on add-on disable.” In PRs, describe the behavior change and list the Blender, Python, and PowerShell versions used for verification. Link any related issue and include a screenshot for visible pane or rendering changes.
