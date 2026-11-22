# PowerShell Console for Blender

Version 0.1.0 adds a docked PowerShell terminal beside Blender’s 3D View. The packaged add-on was tested with Windows PowerShell 5.1 and the interactive Codex CLI in Blender 5.2.3 LTS.

Requires Windows x64 with ConPTY, Blender 5.2 or later with Python 3.13 x64, and `pwsh.exe` or `powershell.exe` on Blender’s `PATH`. The add-on bundles its Python dependencies.

## Build and install

From the repository root, run the Windows build wrapper:

```bat
.\scripts\build.bat
```

In Blender, go to **Edit → Preferences → Add-ons → Install from Disk**, select `output/powershell_console-0.1.0.zip`, and enable **PowerShell Console**. In the 3D View, press **N**, select **PowerShell**, set **Dock Side** to **Left** or **Right**, and click **Open PowerShell Pane**. Blender saves your selection in the add-on preferences. The default is **Right**.

See the [project specifications](docs/specs.md) for compatibility details and limitations.
