"""Nonmodal, scrollable add-in interjections. Called only by the Tk thread."""
import tkinter as tk
import math
import os
from datetime import datetime
from tkinter.scrolledtext import ScrolledText

from .. import geometry
from ..addin_messages import LEVELS, RANK, AlertCarousel
from . import theme
from .secret_value import inline as inline_secret
from .warning_beacon import WarningBeacon
from . import markdown_preview
from .toggle_switch import ToggleSwitch
from .window_keys import WindowKeys
from aipet_addin import format_markdown_report


def warning_timestamp(message):
    context = (message.get('report') or {}).get('context', {})
    for value in (context.get('event_time'), message.get('timestamp')):
        if value is None:
            continue
        try:
            return datetime.fromtimestamp(float(value)).astimezone().isoformat(timespec='seconds')
        except (ValueError, TypeError, OverflowError, OSError):
            pass
    return 'Not available'


def show(parent, rows, on_close=None, screen=None, options=None, on_options=None,
         on_whitelist=None, on_configure=None):
    options = dict(options or {})
    options.setdefault('addin_alert_topmost', True)
    options.setdefault('addin_alert_no_focus', True)
    fonts, metrics, padding, switches = [], [], [], []
    window_bounds = [None]
    key_router = [None]

    def component(widget, font=None, **sizes):
        if font:
            fonts.append((widget, font))
        if sizes:
            metrics.append((widget, sizes))
        return widget

    def pack(widget, **kwargs):
        sizes = {key: value for key, value in kwargs.items() if key in ('padx', 'pady', 'ipadx', 'ipady')}
        if sizes:
            padding.append((widget, sizes))
        widget.pack(**kwargs)

    def scaled(value, scale):
        return tuple(round(part*scale) for part in value) if isinstance(value, tuple) else round(value*scale)

    win = tk.Toplevel(parent)
    win.title('AIPet · add-in messages')
    messages = [message for row in rows for message in row['messages']]
    if len(messages) == 1:
        win.title(f"AIPet · {messages[0]['title']} · {warning_timestamp(messages[0])}")
    win.attributes('-topmost', options['addin_alert_topmost'])
    if parent.winfo_viewable():
        win.transient(parent.winfo_toplevel())
    header = component(tk.Frame(win, padx=16, pady=12), padx=16, pady=12)
    header.pack(fill='x')
    beacon = WarningBeacon(header)
    pack(beacon.canvas, side='right', padx=(8, 0))
    component(tk.Label(header, text='Add-in alerts', font=(theme.UI_FONT, 14, 'bold'), anchor='w'), (14, 'bold')).pack(fill='x')
    subtitle = component(tk.Label(header, text='Full report · values stay censored until you use an eye control.',
             font=(theme.UI_FONT, 9), fg=theme.T['muted'], anchor='w', wraplength=560,
             justify='left'), (9,), wraplength=560)
    pack(subtitle, fill='x', pady=(4, 0))
    subtitle.bind('<Configure>', lambda event: subtitle.configure(wraplength=max(80, event.width)))
    text = component(ScrolledText(win, wrap='word', width=72, height=22, padx=12, pady=12,
                        font=(theme.UI_FONT, 10)), padx=12, pady=12)
    pack(text, fill='both', expand=True, padx=8, pady=(8, 0))
    component(text.vbar, width=14)
    fields = []
    content_scale = [1.0]
    preview = tk.BooleanVar(master=win, value=any((message.get('report') or {}).get('markdown_template') for message in messages))
    preview_button = component(tk.Checkbutton(header, text='Markdown preview', variable=preview,
                   command=lambda: render(list(displayed)), font=(theme.UI_FONT, 9)), (9,))
    pack(preview_button, anchor='w', pady=(6, 0))
    hint = component(tk.Label(header, text='← / → Navigate · D Dismiss', font=(theme.UI_FONT, 9),
             fg=theme.T['muted']), (9,))
    pack(hint, anchor='w', pady=(4, 0))
    option_vars = {}
    updating_options = [False]

    def apply_options(values):
        updating_options[0] = True
        try:
            options.update(values)
            for key, variable in option_vars.items():
                variable.set(options[key])
            win.attributes('-topmost', options['addin_alert_topmost'])
        finally:
            updating_options[0] = False

    def option_changed(key):
        if updating_options[0]:
            return
        value = bool(option_vars[key].get())
        if on_options is None or on_options(key, value) is not False:
            apply_options({key: value})
        else:
            apply_options({})

    for key, label in (('addin_alert_topmost', 'Always on top'), ('addin_alert_no_focus', 'Do not steal focus')):
        row = tk.Frame(header)
        pack(row, fill='x', pady=(4, 0))
        variable = tk.BooleanVar(master=win, value=options[key])
        option_vars[key] = variable
        switch = ToggleSwitch(row, variable)
        switches.append(switch)
        pack(switch, side='left', padx=(0, 8))
        component(tk.Label(row, text=label, font=(theme.UI_FONT, 9)), (9,)).pack(side='left')
        trace = variable.trace_add('write', lambda *_args, key=key: option_changed(key))
        win.bind('<Destroy>', lambda event, variable=variable, trace=trace:
                 variable.trace_remove('write', trace) if event.widget is win else None, add='+')
    def style_tags(_event=None):
        scale = content_scale[0]
        for widget, font in fonts:
            widget.configure(font=(theme.UI_FONT, max(1, round(font[0]*scale)), *font[1:]))
        for widget, sizes in metrics:
            widget.configure(**{key: scaled(value, scale) for key, value in sizes.items()})
        for widget, sizes in padding:
            widget.pack_configure(**{key: scaled(value, scale) for key, value in sizes.items()})
        for switch in switches:
            switch.set_scale(scale)
        text.configure(font=(theme.UI_FONT, max(1, round(10 * scale))))
        markdown_preview.configure(text, scale)
        for level, (_, color, _) in LEVELS.items():
            text.tag_configure(level, foreground=theme.themed_color(color), font=(theme.UI_FONT, max(1, round(11 * scale)), 'bold'))
        text.tag_configure('secret-emphasis', background=theme.T['select_bg'], foreground=theme.T['win_fg'],
                           font=(theme.UI_FONT, max(1, round(10 * scale)), 'bold'))
        for field in fields:
            field.set_content_scale(scale)
        beacon.set_scale(scale)
    style_tags()
    win.bind('<<AIPetThemeChanged>>', style_tags, add='+')
    displayed = []
    carousel = AlertCarousel()
    confirmation = [None]
    def insert_secret(value, row):
        if on_whitelist is not None and row.get('addin_id') == 'token-guard':
            field = inline_secret(text, value, on_whitelist=lambda done: on_whitelist(row, value, done))
        else:
            field = inline_secret(text, value)
        fields.append(field)

    def insert_hook(source, spans, row):
        def secret(value):
            insert_secret(value, row)
        if preview.get():
            markdown_preview.render(text, source, spans, secret)
        else:
            cursor = 0
            for start, end in spans:
                text.insert('end', source[cursor:start])
                secret(source[start:end])
                cursor = end
            text.insert('end', source[cursor:])

    def render(rows):
        top = text.yview()[0]
        displayed[:] = rows
        text.configure(state='normal')
        for widget in fields:
            widget.destroy()
        fields.clear()
        text.delete('1.0', 'end')
        for row in rows:
            if not row['messages']:
                continue
            if not all((message.get('report') or {}).get('markdown_template') for message in row['messages']):
                text.insert('end', f"{row['name']}\n", row['level'])
            for message in reversed(row['messages']):
                report = message.get('report')
                if report and report.get('markdown_template'):
                    document, spans = format_markdown_report(report, message.get('timestamp'))
                    insert_hook(document, spans, row)
                    text.insert('end', '\n')
                    continue
                text.insert('end', f"\nTimestamp: {warning_timestamp(message)}\n")
                if report:
                    text.insert('end', f"\nWARNING LEVEL: {report['warning_level'].upper()}\n", message['level'])
                    text.insert('end', f"Rule: {report['rule']} · {report['name']}\n", message['level'])
                    context = report.get('context', {})
                    for key, label in (('conversation_title', 'Conversation'), ('harness', 'AI harness'),
                                       ('session_id', 'Session'), ('project', 'Project'), ('cwd', 'Directory'),
                                       ('environment', 'Environment'), ('model', 'Model'), ('hook_event', 'Hook event'),
                                       ('agent', 'AI engine'), ('source', 'Session source'), ('entry', 'Harness entry'),
                                       ('tool', 'Tool'), ('agent_name', 'Agent name'), ('agent_id', 'Agent ID'), ('event_id', 'Event ID')):
                        value = context.get(key)
                        if value or key in ('conversation_title', 'harness', 'session_id'):
                            text.insert('end', f"{label}: {value or 'Not available'}\n")
                    text.insert('end', f"\nExplanation: {report['explanation']}\n")
                    text.insert('end', f"Fingerprint: {report['fingerprint']}\n\n")
                    if 'hook_text' in report:
                        text.insert('end', f"Hook text · {report['hook_field']}\n")
                        insert_hook(report['hook_text'], report['hook_spans'], row)
                    else:
                        text.insert('end', 'Detected value: ')
                        insert_secret(report['value'], row)
                    if 'decoded' in report:
                        text.insert('end', '\nDecoded content: ')
                        insert_secret(report['decoded'], row)
                    text.insert('end', '\n')
                    if report.get('truncated'):
                        text.insert('end', 'Decoded/legacy preview limited to 4 KiB; hook text and its matches are preserved in full.\n'
                                    if 'hook_text' in report else 'Value preview limited to 4 KiB; fingerprint covers the complete value.\n')
                else:
                    text.insert('end', f"\n{message['level'].upper()} · {message['title']}\n", message['level'])
                    if preview.get():
                        markdown_preview.render(text, message['content'])
                        text.insert('end', '\n')
                    else:
                        text.insert('end', message['content'] + '\n')
            text.insert('end', '\n' + '─' * 35 + '\n\n')
        text.configure(state='disabled')
        beacon.set_level(max((m['level'] for r in rows for m in r['messages']), key=RANK.get, default='info'))
        style_tags()
        if key_router[0] is not None:
            key_router[0].install(win)
        text.yview_moveto(top)
    scale_row = component(tk.Frame(win, padx=12, pady=4), padx=12, pady=4)
    scale_row.pack(side='bottom', fill='x', before=text)
    component(tk.Label(scale_row, text='Window scale', font=(theme.UI_FONT, 9)), (9,)).pack(side='left')
    scale_label = component(tk.Label(scale_row, text='100%', width=5, font=(theme.UI_FONT, 9)), (9,))
    scale_dragging, scale_commit = [False], [None]
    def set_content_scale(percent):
        old_scale = content_scale[0]
        content_scale[0] = min(2.5, max(0.5, float(percent)/100))
        scale_label.configure(text=f'{round(content_scale[0]*100)}%')
        style_tags()  # Restyle in place: keep scroll position and each eye's reveal state.
        if window_bounds[0] is not None and old_scale != content_scale[0]:
            left, top, right, bottom = window_bounds[0]
            ratio = content_scale[0]/old_scale
            width, height = win.winfo_width(), win.winfo_height()
            new_width = min(right-left, max(round(420*content_scale[0]), round(width*ratio)))
            new_height = min(bottom-top, max(round(260*content_scale[0]), round(height*ratio)))
            x = max(left, min(right-new_width, win.winfo_x()+(width-new_width)//2))
            y = max(top, min(bottom-new_height, win.winfo_y()+(height-new_height)//2))
            win.minsize(min(right-left, round(420*content_scale[0])), min(bottom-top, round(260*content_scale[0])))
            win.geometry(f'{new_width}x{new_height}'+geometry.geo(x, y))
    def preview_scale(percent):
        scale_label.configure(text=f'{round(float(percent))}%')

    def apply_pending_scale():
        scale_commit[0] = None
        if not scale_dragging[0] and win.winfo_exists():
            set_content_scale(slider.get())

    def commit_scale(_event=None):
        if not scale_dragging[0] and scale_commit[0] is None:
            # Tk must finish releasing its thumb/track-repeat state before resizing the slider itself.
            scale_commit[0] = win.after_idle(apply_pending_scale)

    def begin_scale_drag(_event=None):
        scale_dragging[0] = True
        if scale_commit[0] is not None:
            win.after_cancel(scale_commit[0])
            scale_commit[0] = None

    def end_scale_drag(_event=None):
        scale_dragging[0] = False
        commit_scale()

    def cancel_scale_commit(event):
        if event.widget is win and scale_commit[0] is not None:
            win.after_cancel(scale_commit[0])
            scale_commit[0] = None

    slider = component(tk.Scale(scale_row, from_=50, to=250, resolution=10, orient='horizontal',
        showvalue=False, length=120, sliderlength=14, width=8, highlightthickness=0,
        sliderrelief='flat', bd=0, command=preview_scale), length=120, sliderlength=14, width=8)
    slider.bind('<ButtonPress-1>', begin_scale_drag, add='+')
    slider.bind('<ButtonRelease-1>', end_scale_drag, add='+')
    slider.bind('<KeyRelease>', commit_scale, add='+')
    slider.bind('<MouseWheel>', commit_scale, add='+')
    slider.bind('<ButtonRelease-4>', commit_scale, add='+')
    slider.bind('<ButtonRelease-5>', commit_scale, add='+')
    win.bind('<Destroy>', cancel_scale_commit, add='+')
    slider.set(100)
    pack(slider, side='left', padx=8)
    scale_label.pack(side='left')
    footer = component(tk.Frame(win, padx=12, pady=8), padx=12, pady=8)
    footer.pack(side='bottom', fill='x', before=scale_row)
    counter = component(tk.Label(footer, font=(theme.UI_FONT, 10)), (10,))

    def refresh(reset_scroll=True):
        count = len(carousel.entries)
        counter.configure(text=f'{carousel.index+1} / {count}')
        for button in (previous, next_button):
            button.configure(state='normal' if count > 1 else 'disabled')
        if reset_scroll:
            render(carousel.current())
            text.yview_moveto(0)
            current = carousel.current()[0]['messages'][0]
            win.title(f"AIPet · {current['title']} · {warning_timestamp(current)}")

    def append_rows(rows):
        empty = not carousel.entries
        carousel.add(rows)
        if carousel.entries:
            refresh(reset_scroll=empty)

    def navigate(delta):
        carousel.step(delta)
        refresh()
        text.focus_set()

    def dismiss(rows):
        carousel.dismiss(rows)
        if on_close:
            on_close(rows)
        if carousel.entries:
            refresh()
            text.focus_set()
        else:
            win.destroy()

    def close():
        if confirmation[0] is not None:
            confirmation[0].lift()
            return
        rows = list(carousel.entries)
        if len(rows) <= 1:
            dismiss(rows)
            return
        def confirmed(accepted):
            confirmation[0] = None
            if win.winfo_exists():
                if accepted:
                    # Alerts arriving while this question is open remain in the carousel.
                    dismiss(rows)
                if carousel.entries:
                    text.focus_force()
        confirmation[0] = theme.themed_dialog(win, 'Dismiss all add-in alerts?',
            f'Dismiss all {len(rows)} pending alerts? You can keep reviewing and dismiss them individually.',
            buttons=(('Keep reviewing', False, 'secondary'), ('Dismiss all', True, 'danger')),
            kind='warning', cancel=False, enter_confirms=False, on_result=confirmed)

    previous = component(tk.Button(footer, text='‹ Previous', command=lambda: navigate(-1),
                        **theme.button_style('secondary')), (9,), padx=12, pady=3)
    previous.pack(side='left')
    pack(counter, side='left', padx=12)
    next_button = component(tk.Button(footer, text='Next ›', command=lambda: navigate(1),
                           **theme.button_style('secondary')), (9,), padx=12, pady=3)
    next_button.pack(side='left')
    component(tk.Button(footer, text='Dismiss', command=lambda: dismiss(carousel.current()),
              **theme.button_style('primary')), (9, 'bold'), padx=14, pady=4).pack(side='right')
    if on_configure is not None:
        configure = component(tk.Button(scale_row, text='Configure add-in filters…',
            command=lambda: on_configure(carousel.current()[0]), **theme.button_style('secondary')),
            (9,), padx=12, pady=3)
        configure.pack(side='right')

    def shortcut(event, action, navigation=False):
        # Windows Tk uses Mod1 (0x8) for NumLock, not Alt. Ignore lock/extended-key flags.
        modifiers = 0x4 | ((0x8 | 0x10) if theme.IS_MAC else 0x20000 if os.name == 'nt' else 0x8)
        if event.state & modifiers or confirmation[0] is not None or not carousel.entries:
            return None
        widget_class = event.widget.winfo_class()
        if widget_class in ('Entry', 'TEntry', 'TCombobox', 'Spinbox', 'TSpinbox'):
            return None
        if navigation and widget_class in ('Scale', 'TScale'):
            return None
        action()
        return 'break'

    def on_key(event):
        key = event.keysym.lower()
        if key in ('left', 'right'):
            return shortcut(event, lambda: navigate(-1 if key == 'left' else 1), navigation=True)
        if key == 'd':
            return shortcut(event, lambda: dismiss(carousel.current()))

    def on_click(event):
        if event.widget.winfo_class() not in ('Scale', 'TScale', 'Entry', 'TEntry', 'TCombobox', 'Spinbox', 'TSpinbox'):
            # Activating labels/disabled Text does not consistently move Windows Tk's keyboard focus.
            target = event.widget if event.widget.winfo_class() in ('Button', 'Checkbutton', 'Canvas') else text
            target.focus_set()

    key_router[0] = WindowKeys(win, on_key, on_click)
    win.set_addin_rows = append_rows
    win.append_addin_rows = append_rows
    def focus_report():
        if os.name == 'nt':
            import ctypes
            user = ctypes.windll.user32
            user.GetAncestor.argtypes = [ctypes.c_void_p, ctypes.c_uint]
            user.GetAncestor.restype = ctypes.c_void_p
            user.SetForegroundWindow.argtypes = [ctypes.c_void_p]
            user.SetForegroundWindow(user.GetAncestor(win.winfo_id(), 2))
        text.focus_force()
    win.focus_addin_report = focus_report
    win.set_addin_options = apply_options
    append_rows(rows)
    win.protocol('WM_DELETE_WINDOW', close)
    win.bind('<Escape>', lambda event: close())
    theme.theme_window(win)
    style_tags()
    win.update_idletasks()
    screen = screen or geometry.screen_info(0, 0, win)
    left, top, right, bottom = screen['work_area']
    width = min(right-left, max(340, round((right-left) * math.sqrt(0.6))))
    height = min(bottom-top, max(220, round((bottom-top) * math.sqrt(0.6))))
    win.minsize(min(420, right-left), min(260, bottom-top))
    x, y = left + (right-left-width)//2, top + (bottom-top-height)//2
    win.geometry(f'{width}x{height}' + geometry.geo(x, y))
    window_bounds[0] = screen['work_area']
    win.lift()
    # Raising a Windows Toplevel does not give its disabled report Text keyboard focus.
    # Wait until it is mapped, then make the shortcuts available without requiring a click.
    if not options['addin_alert_no_focus']:
        win.after_idle(focus_report)
    return win
