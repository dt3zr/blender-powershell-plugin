"""Dock an interactive Windows PowerShell terminal beside Blender's 3D View."""

bl_info = {
    "name": "PowerShell Console",
    "version": (0, 1, 0),
    "blender": (5, 2, 0),
    "location": "3D View > Sidebar > PowerShell",
    "category": "Development",
}

import os
from pathlib import Path
import queue
import shutil
import subprocess
import sys
import threading

import blf
import bpy
import gpu
from bpy.app.handlers import persistent
from gpu_extras.batch import batch_for_shader


PADDING = 6
FONT_SIZE = 15
FONT_PATH = str(Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts" / "consola.ttf")
BACKGROUND = (0.055, 0.063, 0.075, 1.0)
FOREGROUND = (0.88, 0.89, 0.91, 1.0)
CURSOR_COLOR = (0.55, 0.75, 1.0, 1.0)
ANSI_COLORS = {
    "black": "151821", "red": "f47474", "green": "83d198", "brown": "e6bd73",
    "blue": "7da9f6", "magenta": "d5a0ec", "cyan": "77cbd0", "white": "d5d9df",
    "brightblack": "747c89", "brightred": "ff9292", "brightgreen": "a5e5ac",
    "brightbrown": "ffe39c", "brightblue": "aed0ff", "brightmagenta": "e9bff8",
    "brightcyan": "a3ecf0", "brightwhite": "ffffff",
}
ANSI_COLORS["yellow"] = ANSI_COLORS["brown"]
ANSI_COLORS["brightyellow"] = ANSI_COLORS["brightbrown"]

sessions = {}
draw_handle = None
font_id = None
last_dock_side = None


def load_dependencies():
    vendor = Path(__file__).with_name("powershell_terminal_vendor")
    sys.path.insert(0, str(vendor))
    try:
        import pyte
        from winpty import PTY, WinptyError
        from winpty.enums import Backend
        return pyte, PTY, Backend, WinptyError
    finally:
        sys.path.remove(str(vendor))


class TerminalScreen:
    """Route terminal sequences to the normal or alternate screen buffer."""

    def __init__(self, pyte, columns, rows, reply):
        self.normal = pyte.HistoryScreen(columns, rows, history=2000)
        self.alternate = pyte.Screen(columns, rows)
        self.current = self.normal
        self.normal.write_process_input = reply
        self.alternate.write_process_input = reply

    def __getattr__(self, name):
        # Pyte caches event handlers when the stream attaches. Dispatch each
        # event to the active buffer, including after a full-screen app exits.
        getattr(self.current, name)

        def dispatch(*args, **kwargs):
            return getattr(self.current, name)(*args, **kwargs)

        return dispatch

    def set_mode(self, *modes, **kwargs):
        for mode in modes:
            if kwargs.get("private") and mode in (47, 1047, 1049):
                if self.current is self.normal:
                    self.alternate.reset()
                    self.current = self.alternate
            else:
                self.current.set_mode(mode, **kwargs)

    def reset_mode(self, *modes, **kwargs):
        for mode in modes:
            if kwargs.get("private") and mode in (47, 1047, 1049):
                self.current = self.normal
            else:
                self.current.reset_mode(mode, **kwargs)

    def resize(self, columns, rows):
        self.normal.resize(lines=rows, columns=columns)
        self.alternate.resize(lines=rows, columns=columns)


def terminal_grid(region):
    blf.size(font_id, FONT_SIZE)
    cell_width = max(1, round(blf.dimensions(font_id, "M")[0]))
    line_height = FONT_SIZE + 4
    cols = max(2, (region.width - 2 * PADDING) // cell_width)
    rows = max(2, (region.height - 2 * PADDING) // line_height)
    return cols, rows, cell_width, line_height


class TerminalSession:
    def __init__(self, executable, columns, rows):
        pyte, PTY, backend, self.pty_error = load_dependencies()
        self.closed = False
        self.ended = False
        self.focused = True
        self.output = queue.Queue(maxsize=512)
        self.input = queue.Queue()
        self.columns = columns
        self.rows = rows
        self.screen = TerminalScreen(pyte, columns, rows, self.send)
        self.stream = pyte.Stream(self.screen)

        env = dict(os.environ, TERM="xterm-256color", COLORTERM="truecolor")
        env_block = "\0".join(f"{key}={value}" for key, value in env.items()) + "\0"
        cwd = Path(bpy.data.filepath).parent if bpy.data.filepath else Path.home()
        args = subprocess.list2cmdline(["-NoLogo"])
        try:
            self.pty = PTY(columns, rows, backend=backend.ConPTY)
            started = self.pty.spawn(executable, cmdline=" " + args, cwd=str(cwd), env=env_block)
        except self.pty_error as error:
            raise RuntimeError(str(error)) from error
        if not started:
            raise RuntimeError("Windows could not start the shell in ConPTY")

        threading.Thread(target=self.read_output, args=(self.pty,), daemon=True).start()
        threading.Thread(target=self.write_input, args=(self.pty,), daemon=True).start()

    def read_output(self, pty):
        try:
            while not self.closed:
                text = pty.read(blocking=True)
                if text:
                    self.enqueue_output(text)
                if pty.iseof():
                    break
        except (OSError, RuntimeError, EOFError, self.pty_error) as error:
            if not self.closed and pty.isalive():
                self.enqueue_output(f"\r\nTerminal read failed: {error}\r\n")
        finally:
            self.enqueue_output(None)

    def enqueue_output(self, text):
        while not self.closed:
            try:
                self.output.put(text, timeout=0.1)
                return
            except queue.Full:
                continue

    def write_input(self, pty):
        for event in iter(self.input.get, None):
            if self.closed:
                return
            try:
                if isinstance(event, tuple):
                    pty.set_size(*event)
                else:
                    pty.write(event)
            except (OSError, RuntimeError, self.pty_error) as error:
                if not self.closed and pty.isalive():
                    self.enqueue_output(f"\r\nTerminal input failed: {error}\r\n")
                return

    def send(self, text):
        if not self.closed and not self.ended and text:
            self.input.put(text)

    def resize(self, columns, rows):
        if (columns, rows) != (self.columns, self.rows):
            self.columns, self.rows = columns, rows
            self.screen.resize(columns, rows)
            if not self.ended:
                self.input.put((columns, rows))

    def drain(self):
        changed = False
        for _ in range(80):
            try:
                text = self.output.get_nowait()
            except queue.Empty:
                break
            if text is None:
                self.ended = True
                self.input.put(None)
                text = "\r\n[PowerShell exited. Reopen it from the PowerShell sidebar.]\r\n"
            self.stream.feed(text)
            changed = True
        return changed

    def close(self):
        if self.closed:
            return
        self.closed = True
        self.input.put(None)
        pty, self.pty = self.pty, None
        try:
            pty.cancel_io()
        except (OSError, RuntimeError, self.pty_error):
            pass


def color(value, default):
    if value == "default":
        return default
    value = ANSI_COLORS.get(value, value)
    try:
        if len(value) == 6:
            return tuple(int(value[offset:offset + 2], 16) / 255
                         for offset in (0, 2, 4)) + (1.0,)
    except ValueError:
        pass
    return default


def append_rectangle(vertices, colors, bounds, rgba):
    x, y, right, top = bounds
    vertices.extend(((x, y), (right, y), (right, top),
                     (x, y), (right, top), (x, top)))
    colors.extend((rgba,) * 6)


def terminal_runs(screen, row):
    line = screen.buffer[row]
    start = 0
    last_attributes = None
    characters = []
    for column in range(screen.columns):
        cell = line[column]
        foreground = color(cell.fg, FOREGROUND)
        background = color(cell.bg, BACKGROUND)
        if cell.reverse:
            foreground, background = background, foreground
        attributes = foreground, background, cell.underscore
        if attributes != last_attributes and characters:
            yield start, column, "".join(characters), last_attributes
            start, characters = column, []
        last_attributes = attributes
        characters.append(cell.data)
    if characters:
        yield start, screen.columns, "".join(characters), last_attributes


def draw_terminal():
    context = bpy.context
    if context.area is None or context.region is None:
        return
    session = sessions.get(context.area.as_pointer())
    if session is None:
        return
    region = context.region
    _, _, cell_width, line_height = terminal_grid(region)
    screen = session.screen.current
    vertices, colors, text_runs = [], [], []
    append_rectangle(vertices, colors, (0, 0, region.width, region.height), BACKGROUND)
    for row in range(screen.lines):
        y = region.height - PADDING - (row + 1) * line_height
        for start, end, text, attributes in terminal_runs(screen, row):
            fg, bg, underline = attributes
            left = PADDING + start * cell_width
            right = PADDING + end * cell_width
            if bg != BACKGROUND:
                append_rectangle(vertices, colors, (left, y, right, y + line_height), bg)
            if underline:
                append_rectangle(vertices, colors, (left, y + 2, right, y + 3), fg)
            if text.strip():
                text_runs.append((left, y + 4, fg, text.rstrip()))

    if session.focused and not session.ended and not screen.cursor.hidden:
        y = region.height - PADDING - (screen.cursor.y + 1) * line_height
        x = PADDING + min(screen.cursor.x, screen.columns - 1) * cell_width
        append_rectangle(vertices, colors, (x, y + 1, x + cell_width, y + 3), CURSOR_COLOR)

    shader = gpu.shader.from_builtin("SMOOTH_COLOR")
    batch_for_shader(shader, "TRIS", {"pos": vertices, "color": colors}).draw(shader)
    for x, y, foreground, text in text_runs:
        blf.color(font_id, *foreground)
        blf.position(font_id, x, y, 0)
        blf.draw(font_id, text)


def refresh_terminals():
    live_areas = set()
    for wm in bpy.data.window_managers:
        for window in wm.windows:
            for area in window.screen.areas:
                if area.type != 'CONSOLE':
                    continue
                key = area.as_pointer()
                session = sessions.get(key)
                if session is None:
                    continue
                live_areas.add(key)
                region = next((item for item in area.regions if item.type == 'WINDOW'), None)
                if region is not None and region.width > 0 and region.height > 0:
                    cols, rows, _, _ = terminal_grid(region)
                    session.resize(cols, rows)
                if session.drain():
                    area.tag_redraw()
    for key in sessions.keys() - live_areas:
        sessions.pop(key).close()
    return 0.03


CURSOR_KEYS = {
    "UP_ARROW": "A", "DOWN_ARROW": "B", "RIGHT_ARROW": "C", "LEFT_ARROW": "D",
    "HOME": "H", "END": "F",
}
EDIT_KEYS = {
    "INSERT": 2, "DEL": 3, "PAGE_UP": 5, "PAGE_DOWN": 6,
    "F5": 15, "F6": 17, "F7": 18, "F8": 19, "F9": 20,
    "F10": 21, "F11": 23, "F12": 24,
}
BASIC_KEYS = {
    "RET": "\r", "NUMPAD_ENTER": "\r", "ESC": "\x1b", "BACK_SPACE": "\x7f",
    "TAB": "\t",
}
MODIFIER_KEYS = {
    "LEFT_SHIFT", "RIGHT_SHIFT", "LEFT_CTRL", "RIGHT_CTRL",
    "LEFT_ALT", "RIGHT_ALT", "OSKEY",
}


def key_sequence(event, mode):
    key = event.type
    modifier = 1 + int(event.shift) + 2 * int(event.alt) + 4 * int(event.ctrl)
    if key in CURSOR_KEYS:
        suffix = CURSOR_KEYS[key]
        if modifier != 1:
            return f"\x1b[1;{modifier}{suffix}"
        prefix = "\x1bO" if 1 << 5 in mode else "\x1b["
        return prefix + suffix
    if key in EDIT_KEYS:
        parameter = str(EDIT_KEYS[key])
        if modifier != 1:
            parameter += f";{modifier}"
        return f"\x1b[{parameter}~"
    if key in {"F1", "F2", "F3", "F4"}:
        suffix = "PQRS"[int(key[-1]) - 1]
        return f"\x1bO{suffix}" if modifier == 1 else f"\x1b[1;{modifier}{suffix}"
    if key == "TAB" and event.shift:
        return "\x1b[Z"
    if key in {"RET", "NUMPAD_ENTER"} and event.shift:
        return "\x1b[13;2u"
    if event.ctrl and len(key) == 1 and "A" <= key <= "Z" and not event.alt:
        return chr(ord(key) - 64)
    text = BASIC_KEYS.get(key, event.unicode)
    return "\x1b" + text if text and event.alt and not event.ctrl else text


def region_contains(region, x, y):
    return region.x <= x < region.x + region.width and region.y <= y < region.y + region.height


MOUSE_BUTTONS = {
    "LEFTMOUSE": 0, "MIDDLEMOUSE": 1, "RIGHTMOUSE": 2,
    "WHEELUPMOUSE": 64, "WHEELDOWNMOUSE": 65,
}


def mouse_sequence(event, region, screen):
    if 1006 << 5 not in screen.mode or not any(
            mode << 5 in screen.mode for mode in (1000, 1002, 1003)):
        return ""
    _, _, cell_width, line_height = terminal_grid(region)
    column = (event.mouse_x - region.x - PADDING) // cell_width + 1
    row = (region.height - 1 - (event.mouse_y - region.y) - PADDING) // line_height + 1
    column = min(max(column, 1), screen.columns)
    row = min(max(row, 1), screen.lines)
    code = MOUSE_BUTTONS[event.type] + 8 * int(event.alt) + 16 * int(event.ctrl)
    suffix = "m" if event.value == 'RELEASE' else "M"
    return f"\x1b[<{code};{column};{row}{suffix}"


def mark_preferences_dirty(_preferences, context):
    context.preferences.is_dirty = True


class PowerShellPreferences(bpy.types.AddonPreferences):
    bl_idname = __package__

    dock_side: bpy.props.EnumProperty(
        name="Dock Side",
        description="Side of the 3D View for the next PowerShell pane",
        items=(
            ('LEFT', "Left", "Dock PowerShell to the left of the 3D View"),
            ('RIGHT', "Right", "Dock PowerShell to the right of the 3D View"),
        ),
        default='RIGHT',
        update=mark_preferences_dirty,
    )

    def draw(self, _context):
        self.layout.prop(self, "dock_side")


def adjacent_panes(area, areas):
    for console in areas:
        if console.type != 'CONSOLE' or console.y != area.y or console.height != area.height:
            continue
        session = sessions.get(console.as_pointer())
        if session is None:
            continue
        if 0 <= area.x - (console.x + console.width) <= 8:
            yield 'LEFT', console, session
        elif 0 <= console.x - (area.x + area.width) <= 8:
            yield 'RIGHT', console, session


class POWERSHELL_OT_open_pane(bpy.types.Operator):
    """Open an interactive PowerShell terminal beside the 3D View"""

    bl_idname = "powershell.open_pane"
    bl_label = "Open PowerShell Pane"

    @classmethod
    def poll(cls, context):
        return context.area is not None and context.area.type == 'VIEW_3D'

    def execute(self, context):
        area = context.area
        screen = context.screen
        side = context.preferences.addons[__package__].preferences.dock_side
        panes = list(adjacent_panes(area, screen.areas))
        for pane_side, _, session in panes:
            if not session.ended:
                self.report({'INFO'}, f"PowerShell is already open to the {pane_side.lower()}")
                return {'CANCELLED'}

        console = None
        previous = None
        for pane_side, pane, session in panes:
            if pane_side == side:
                console, previous = pane, session
                break
        if console is None and panes:
            old_side = panes[0][0].lower()
            self.report({'INFO'}, f"Join the {old_side} PowerShell pane back into the 3D View "
                                  f"before opening a pane on the {side.lower()}")
            return {'CANCELLED'}

        executable = shutil.which("pwsh.exe") or shutil.which("powershell.exe")
        if os.name != "nt" or executable is None:
            self.report({'ERROR'}, "PowerShell was not found on this Windows system")
            return {'CANCELLED'}

        region = next(item for item in area.regions if item.type == 'WINDOW')
        target_region = region if console is None else next(
            item for item in console.regions if item.type == 'WINDOW')
        columns, rows, _, _ = terminal_grid(target_region)
        if console is None:
            columns = max(2, columns // 3)
        try:
            session = TerminalSession(executable, columns, rows)
        except (ImportError, OSError, RuntimeError) as error:
            self.report({'ERROR'}, f"Could not start PowerShell: {error}")
            return {'CANCELLED'}

        if console is None:
            existing_areas = {item.as_pointer() for item in screen.areas}
            # Blender keeps the larger half in the original area. The new area's
            # pixel bounds may not be initialized until after the split.
            factor = 0.33 if side == 'LEFT' else 0.67
            with context.temp_override(area=area, region=region):
                status = bpy.ops.screen.area_split(direction='VERTICAL', factor=factor)
            console = next((item for item in screen.areas
                            if item.as_pointer() not in existing_areas), None)
            if 'FINISHED' not in status or console is None:
                session.close()
                self.report({'ERROR'}, "Blender did not split the 3D View")
                return {'CANCELLED'}
            console.type = 'CONSOLE'
        if previous is not None:
            previous.close()

        self.area_key = console.as_pointer()
        self.session = session
        sessions[self.area_key] = session
        context.window_manager.modal_handler_add(self)
        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        session = self.session
        if sessions.get(self.area_key) is not session:
            return {'FINISHED'}
        area = next((item for item in context.window.screen.areas
                     if item.as_pointer() == self.area_key), None)
        if area is None or area.type != 'CONSOLE':
            return {'PASS_THROUGH'}
        region = next((item for item in area.regions if item.type == 'WINDOW'), None)
        if region is None:
            return {'PASS_THROUGH'}

        inside = region_contains(region, event.mouse_x, event.mouse_y)
        if event.type == 'LEFTMOUSE' and event.value == 'PRESS':
            session.focused = inside
            area.tag_redraw()
        if not inside or not session.focused:
            return {'PASS_THROUGH'}
        if session.ended:
            return {'RUNNING_MODAL'}

        screen = session.screen.current
        if event.type in MOUSE_BUTTONS and not event.shift:
            mouse = mouse_sequence(event, region, screen)
            if mouse:
                session.send(mouse)
                return {'RUNNING_MODAL'}
        if event.type in {'WHEELUPMOUSE', 'WHEELDOWNMOUSE'}:
            if screen is session.screen.normal:
                if event.type == 'WHEELUPMOUSE':
                    screen.prev_page()
                else:
                    screen.next_page()
                area.tag_redraw()
            return {'RUNNING_MODAL'}
        if event.type in {'LEFTMOUSE', 'RIGHTMOUSE', 'MIDDLEMOUSE'}:
            return {'RUNNING_MODAL'}
        if event.value != 'PRESS' or event.type in MODIFIER_KEYS:
            return {'PASS_THROUGH'}

        if event.type == 'V' and event.ctrl and not event.alt:
            text = context.window_manager.clipboard
            text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\n", "\r")
            if 2004 << 5 in screen.mode:
                text = "\x1b[200~" + text + "\x1b[201~"
        else:
            text = key_sequence(event, screen.mode)
        if not text:
            return {'PASS_THROUGH'}
        session.send(text)
        return {'RUNNING_MODAL'}


class POWERSHELL_PT_sidebar(bpy.types.Panel):
    bl_label = "PowerShell"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "PowerShell"

    def draw(self, context):
        self.layout.prop(context.preferences.addons[__package__].preferences, "dock_side")
        self.layout.operator(POWERSHELL_OT_open_pane.bl_idname, icon='CONSOLE')


classes = (PowerShellPreferences, POWERSHELL_OT_open_pane, POWERSHELL_PT_sidebar)


def load_terminal_font():
    global font_id
    font_id = blf.load(FONT_PATH)
    if font_id < 0:
        font_id = 0


def unload_terminal_font():
    global font_id
    if font_id is not None and font_id > 0:
        blf.unload(FONT_PATH)
    font_id = None


@persistent
def release_font_before_file_load(_filepath):
    unload_terminal_font()


@persistent
def restore_font_after_file_load(_filepath):
    # Blender can reset the font registry while opening a file.
    if font_id is None:
        load_terminal_font()


def register():
    global draw_handle
    load_terminal_font()
    for cls in classes:
        bpy.utils.register_class(cls)
    addon = bpy.context.preferences.addons.get(__package__)
    if addon is not None and last_dock_side is not None:
        preferences = addon.preferences
        if not preferences.is_property_set("dock_side"):
            preferences.dock_side = last_dock_side
    draw_handle = bpy.types.SpaceConsole.draw_handler_add(draw_terminal, (), 'WINDOW', 'POST_PIXEL')
    bpy.app.timers.register(refresh_terminals, persistent=True)
    bpy.app.handlers.load_pre.append(release_font_before_file_load)
    bpy.app.handlers.load_post.append(restore_font_after_file_load)
    bpy.app.handlers.load_post_fail.append(restore_font_after_file_load)


def unregister():
    global draw_handle, last_dock_side
    # Blender removes the add-on's preferences entry when it is disabled.
    addon = bpy.context.preferences.addons.get(__package__)
    if addon is not None:
        last_dock_side = addon.preferences.dock_side
    bpy.app.timers.unregister(refresh_terminals)
    for session in sessions.values():
        session.close()
    sessions.clear()
    bpy.types.SpaceConsole.draw_handler_remove(draw_handle, 'WINDOW')
    draw_handle = None
    bpy.app.handlers.load_pre.remove(release_font_before_file_load)
    bpy.app.handlers.load_post.remove(restore_font_after_file_load)
    bpy.app.handlers.load_post_fail.remove(restore_font_after_file_load)
    unload_terminal_font()
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
