# Blender PowerShell Terminal Add-on

## Purpose and current status

PowerShell Console 0.1.0 adds an interactive Windows PowerShell terminal to Blender. From the 3D View, press **N → PowerShell → Open PowerShell Pane**. The add-on splits that viewport vertically and docks the terminal on the right, in the same Blender window. Click inside the terminal and keep the pointer over it while typing. Drag the shared border to resize the pane.

The terminal supports ordinary PowerShell commands and interactive terminal applications. Type `codex` to enter Codex’s full-screen interface. `codex exec` still works as a CLI command, but is no longer required for running Codex from Blender. OpenCode and Claude Code also reached their interactive startup screens in testing. The installed copy was verified in Blender 5.2.3 LTS, and Codex was left running in the docked pane at the end of the test.

## Files and dependencies

| Component | Requirement or version tested | Role |
| --- | --- | --- |
| [`src/powershell_console/__init__.py`](../src/powershell_console/__init__.py) | Add-on 0.1.0 | Registers the N-panel, pane operator, input handler, renderer, and terminal lifecycle. |
| [`powershell_terminal_vendor/`](../src/powershell_console/powershell_terminal_vendor/) | Bundled beside the package entry point | Holds the Python dependencies and their package metadata. |
| Windows | x64 host with ConPTY available | Provides the pseudoconsole used by PowerShell and its child applications. |
| Blender and its bundled Python | Blender 5.2.3 LTS with Python 3.13.13 x64 tested. Add-on metadata declares Blender 5.2.0 as the minimum. | Supplies `bpy`, `blf`, `gpu`, and `gpu_extras`. The bundled native wheel must match Blender’s Python ABI and host architecture. |
| PowerShell | Windows PowerShell 5.1 tested | The add-on looks for `pwsh.exe` first and falls back to `powershell.exe`. The executable must be discoverable through Blender’s PATH. |
| `pywinpty` | 3.1.2, CPython 3.13 Windows x64 wheel | Exposes the `winpty` module and the native ConPTY interface used to start, read, write, resize, and close the pseudoterminal. |
| `pyte` | 0.8.2 | Parses VT escape sequences and maintains the contents of the terminal grid. |
| `wcwidth` | 0.9.5 | Transitive dependency of `pyte` for Unicode cell width. |
| Consolas | `%WINDIR%\Fonts\consola.ttf` when available | Monospaced terminal font. The add-on falls back to Blender’s default font if loading fails. |
| Agent CLIs | Optional. Codex CLI 0.171.0 tested. | Codex, OpenCode, Claude Code, and Pi must be installed and discoverable in the shell to launch by name. Each CLI manages its own configuration and authentication. |

The Blender MCP add-on was used for development and verification. The PowerShell add-on runs independently of MCP. Python’s standard library supplies filesystem paths, executable discovery, queues, and background threads. No separate terminal application or browser is required.

## Installation and use

Run `.\scripts\build.bat` from the project root to produce `output/powershell_console-0.1.0.zip`. You can also invoke the batch file by its full path from another working directory. It runs the adjacent `build.ps1` with `-NoProfile` and a process-only `-ExecutionPolicy Bypass`, then returns the PowerShell exit code. The PowerShell script derives the version from the add-on metadata and packages the source and native dependencies under a single `powershell_console/` directory.

To reproduce the vendor directory, run the following with Blender’s bundled Python from the project root. The pinned versions match the tested build.

```powershell
& 'C:\Program Files\Blender Foundation\Blender 5.2\5.2\python\bin\python.exe' -m pip install --target .\src\powershell_console\powershell_terminal_vendor pywinpty==3.1.2 pyte==0.8.2 wcwidth==0.9.5
```

1. In **Edit → Preferences → Add-ons**, choose **Install from Disk** and select the ZIP. If upgrading from the earlier loose-file installation, first disable that add-on and remove its old `powershell_console.py` and sibling `powershell_terminal_vendor` from the Blender user add-ons directory. Restart Blender before installing the ZIP to clear the old module.
2. Enable **PowerShell Console** in **Edit → Preferences → Add-ons**. If replacing an already loaded copy, reload the add-on or restart Blender.
3. Open the pane from the 3D View sidebar. The initial working directory is the saved blend file’s parent directory, or the Windows user’s home directory for an unsaved scene. Use `Set-Location` to change it.
4. Click the terminal and run the desired command. The terminal forwards ordinary text, arrows, Enter, Escape, and Ctrl combinations. Ctrl+V pastes from Blender’s clipboard. In the main shell buffer, scrolling moves through terminal history when the application has not requested mouse tracking.
5. Type `exit` at the PowerShell prompt to end the shell. Use the sidebar button to start a fresh shell in its existing adjacent pane. You can also join the editor area back into the viewport or change its editor type to close the terminal session.

## High-level design

```text
Blender N-panel → pane operator → docked Console editor area
                                        │
Blender input events → input queue → ConPTY → PowerShell → interactive CLI
                                        │
         GPU and BLF renderer ← pyte ← output queue ← ConPTY
```

1. **Pane creation.** The sidebar operator `powershell.open_pane` is available from the 3D View. It checks for PowerShell, starts the terminal session, and uses Blender’s `screen.area_split` operator with a vertical split factor of 0.67. The newly created area on the right becomes a Console editor. The left area retains the 3D View. The operator avoids opening a duplicate active adjacent terminal and can reuse an adjacent pane whose shell has exited.
2. **Terminal process.** `TerminalSession` creates a Windows pseudoconsole through `winpty.PTY` with its ConPTY backend. PowerShell starts with `-NoLogo` and inherits the environment, with `TERM=xterm-256color` and `COLORTERM=truecolor` set for terminal applications. Child applications see terminal handles and can use interactive input, terminal sizing, cursor control, and full-screen redraws.
3. **I/O and resizing.** Background threads read terminal output and dispatch queued input and size changes. A Blender timer runs roughly every 30 ms to size the terminal from the editor region, drain output into the emulator, request redraws, and close sessions whose areas are no longer visible Console editors. Blender UI drawing and screen-buffer updates happen on the Blender thread.
4. **Terminal state.** `TerminalScreen` wraps a normal `pyte.HistoryScreen` with 2,000 lines of history and a separate `pyte.Screen` for full-screen programs. It routes terminal events to the current buffer, switches buffers for DEC alternate-screen sequences, and forwards terminal replies from the parser to ConPTY. Returning from the interactive CLI restores the shell buffer.
5. **Drawing.** A `SpaceConsole` post-pixel draw handler renders the terminal over the editor region. GPU batches draw the background, colored cells, underlines, and cursor. Blender’s font API draws the text. The add-on uses the Console editor as a host for the drawing and input surface.
6. **Input.** A Blender modal operator handles events in the focused terminal region and passes other events back to Blender. It maps text, cursor keys, editing keys, function keys, and modifiers to terminal input. It supports bracketed paste when requested by the application, and forwards SGR mouse button and wheel reports when the application enables the relevant modes.
7. **Cleanup.** Closing a session stops accepting input and cancels pending pseudoterminal I/O. Disabling the add-on unregisters its timer, draw handler, operator, and panel, closes its active sessions, and unloads the font.

## Development phases and issues encountered

### Phase 1. Separate Blender window with a persistent shell

1. Inspected the running Blender installation and its Console operator source. Blender 5.2 dispatches console languages through modules named `_console_<language>`. An initial import of `console_python` failed because the installed module was named `_console_python`.
2. Registered a `_console_powershell` language adapter in memory. Its execute hook took a line from Blender’s Console history and forwarded it to a PowerShell process. The adapter set the `PS>` prompt and used Blender’s Console scrollback for output.
3. Started one hidden PowerShell process per window. The bootstrap read base64-framed UTF-8 command lines from a pipe and dot-sourced each decoded script block in the same runspace. This preserved variables and directory changes between commands. Worker threads handled the pipes, and a Blender timer drained output into the editor.
4. Used `screen.area_dupli` to duplicate the viewport into a separate Blender window, then converted that area to Console. The first test using `EXEC_DEFAULT` returned `CANCELLED`. `INVOKE_DEFAULT` opened the separate window successfully.
5. Verified state persistence by assigning a variable in one command and reading `42` in the next. The initial output included `#< CLIXML` because Windows PowerShell emitted a serialized stream header through the redirected streams. The bootstrap was changed to suppress progress rendering and convert output to plain text with `Out-String -Stream`. A retest showed the expected output and no CLIXML marker. The error from `Write-Error 'test error'` was deliberately generated to verify error forwarding.

### Phase 2. Docked pane and non-interactive Codex

1. Replaced `area_dupli` with `screen.area_split` after approval. The first docked test split the correct viewport but converted the left area to Console. The code had compared editor coordinates immediately after splitting, when Blender had not yet initialized the new area’s pixel bounds. The fix identified the new area by its Blender pointer. With the 0.67 split on the tested Blender version, the original area stays on the left and the new area occupies the right.
2. The first live reload still exposed the old operator because Blender had imported its user-installed copy. Loaded the workspace source for testing, then updated the installed file and verified the two copies matched.
3. Ran `codex --version` and bare `codex` in the docked pane. The version command worked. Bare `codex` returned `Error: stdin is not a terminal` because the previous shell implementation exposed redirected pipes.
4. Tried `codex exec` with an explicit prompt. Codex detected redirected stdin and waited for EOF while Blender kept that same stream open for future PowerShell commands. The interim fix gave `codex exec` a short, separate pipeline that closed after supplying an empty line.
5. Windows PowerShell 5.1 wrapped native stderr status text such as `Reading additional input from stdin...` in a `NativeCommandError` record. The interim wrapper converted these records to their message text so routine CLI status no longer produced a PowerShell stack trace.
6. Verified the installed 1.1.0 copy opened one right-hand pane with no extra Blender window. Codex returned `BLENDER_CODEX_OK` in a read-only smoke test, and PowerShell processed `POWERSHELL_STILL_READY` immediately afterward. This proved the interim batch path. Interactive Codex remained blocked by the pipe-based architecture.

### Phase 3. Interactive terminal

1. Checked the local environment. Blender’s Python did not have `pyte` or `winpty` installed. The first dependency download failed because the shell sandbox blocked socket access. With approval, downloaded the Windows x64 dependencies into the project-local vendor directory.
2. Replaced the pipe-based shell, custom console language, and interim Codex wrapper with ConPTY, the terminal emulator, Blender’s drawing hook, and the modal input handler. Bare `codex` now reaches the actual CLI in PowerShell. The terminal backend is shared by all child applications.
3. Implemented normal and alternate screen buffers, color rendering, terminal resizing, scrollback, keyboard translation, bracketed paste, and basic mouse reporting. Added logic to reuse an exited pane and release I/O resources when a terminal closes.
4. Verified that PowerShell produced a prompt and that Codex entered the alternate screen buffer. Exercised `/status` in the interactive session and observed its response. A separate check passed simulated Blender keyboard events through the modal handler and confirmed the typed text appeared in Codex. Backspace events removed the test input afterward.
5. The Blender screenshot tools returned black frames while the Blender window was minimized. A desktop capture attempt did not return a usable image of the Blender window. Verified the draw path in a Blender GPU offscreen framebuffer and checked that drawing produced pixel output. The keyboard check exercised the add-on’s event handler directly.
6. OpenCode displayed its interactive prompt in the docked test session. Claude Code displayed its interactive startup UI in a separate session using the same ConPTY backend. Pi was not discoverable on PowerShell’s PATH and was untested. No OpenCode or Claude Code task was submitted.
7. Found a reload error during teardown. `blf.unload` accepts a font path, but the first implementation passed the integer font ID returned by `blf.load`. Updated it to unload by path, then verified a register → unregister → register cycle in the installed copy.
8. The first manual source reload of the installed file also failed because a UTF-8 byte-order mark was passed through to `compile()` as decoded text. Reloaded from file bytes so Python handled the encoding marker the same way it does during a normal module import.
9. Installed the final Python file and vendor directory in Blender’s user add-ons folder. Verified that the installed dependencies imported in Blender’s bundled Python, confirmed the add-on was enabled, and checked that Blender showed one window with the terminal directly to the right of the viewport. Left the installed version’s Codex session open in the project directory.

### Phase 4. Packaged release 0.1.0

1. Moved the add-on and its vendor dependencies under `src/powershell_console/` and added the Windows build script. Verified that the ZIP builds from the project root and from another working directory and excludes bytecode caches.
2. Tested the ZIP in an isolated Blender 5.2.3 profile. Blender loaded version 0.1.0 and the packaged ConPTY session launched interactive Codex.
3. The packaging smoke test exposed a native `WinptyError` when shutdown tried to cancel I/O after the pending operation was already gone. Updated the session to handle the native exception and verified Codex startup and session shutdown with the rebuilt ZIP.

## Current limitations

- **Platform and ABI.** The implementation is Windows-specific and requires ConPTY. The supplied native packages target the tested Windows x64 and Blender Python 3.13 combination. Other Windows architectures and Blender or Python versions need compatible wheels and their own validation.
- **Terminal feature coverage.** The renderer implements a subset of terminal behavior through `pyte` and Blender. Mouse forwarding covers buttons and the wheel in SGR mode, with no drag or motion reporting. Terminal text selection, a copy-selection gesture, and a terminal settings UI are not implemented. Advanced escape-sequence and keyboard protocol compatibility can vary by CLI.
- **Input focus.** The pointer must be inside the terminal region, and clicking outside removes terminal focus. Blender still owns window-level shortcuts and the editor header. Ctrl+V is reserved for clipboard paste in the terminal.
- **Session lifecycle.** Shell and agent processes are tied to the active pane. Joining the area, changing its editor type, switching to a workspace that hides it, or disabling the add-on ends the managed session. A saved blend file may retain Blender’s editor layout, but it cannot restore a running terminal process or its scrollback.
- **Application setup.** Installing this add-on does not install agent CLIs or configure their accounts, models, or credentials. Application-specific prompts and shortcuts depend on the CLI and its configuration.
- **Verification scope.** Codex startup, keyboard-event forwarding, and an interactive command were tested. OpenCode and Claude Code were verified through startup only. Pi, PowerShell 7, and other terminal applications were not tested. The GPU draw-path check did not establish every detail of live rendering or DPI behavior.
