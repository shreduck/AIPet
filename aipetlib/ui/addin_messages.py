"""Nonmodal, scrollable add-in interjections. Called only by the Tk thread."""
import tkinter as tk
import math
from datetime import datetime
from tkinter.scrolledtext import ScrolledText

from .. import geometry
from ..addin_messages import LEVELS
from . import theme
from .secret_value import inline as inline_secret
from .warning_beacon import WarningBeacon
from . import markdown_preview
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


def show(parent, rows, on_close=None, screen=None):
    win = tk.Toplevel(parent)
    win.title('AIPet · add-in messages')
    messages = [message for row in rows for message in row['messages']]
    if len(messages) == 1:
        win.title(f"AIPet · {messages[0]['title']} · {warning_timestamp(messages[0])}")
    win.attributes('-topmost', True)
    if parent.winfo_viewable():
        win.transient(parent.winfo_toplevel())
    header = tk.Frame(win, padx=16, pady=12)
    header.pack(fill='x')
    beacon = WarningBeacon(header)
    beacon.canvas.pack(side='right', padx=(8, 0))
    tk.Label(header, text='Add-in alerts', font=(theme.UI_FONT, 14, 'bold'), anchor='w').pack(fill='x')
    tk.Label(header, text='Full report · values stay censored until you use an eye control.',
             font=(theme.UI_FONT, 9), fg=theme.T['muted'], anchor='w', wraplength=560,
             justify='left').pack(fill='x', pady=(4, 0))
    text = ScrolledText(win, wrap='word', width=72, height=22, padx=12, pady=12,
                        font=(theme.UI_FONT, 10))
    text.pack(fill='both', expand=True, padx=8, pady=(8, 0))
    fields = []
    preview = tk.BooleanVar(master=win, value=any((message.get('report') or {}).get('markdown_template') for message in messages))
    tk.Checkbutton(header, text='Markdown preview', variable=preview,
                   command=lambda: render(list(displayed)), font=(theme.UI_FONT, 9)).pack(anchor='w', pady=(6, 0))
    def style_tags(_event=None):
        markdown_preview.configure(text)
        for level, (_, color, _) in LEVELS.items():
            text.tag_configure(level, foreground=theme.themed_color(color), font=(theme.UI_FONT, 11, 'bold'))
        text.tag_configure('secret-emphasis', background=theme.T['select_bg'], foreground=theme.T['win_fg'],
                           font=(theme.UI_FONT, 10, 'bold'))
        for field in fields:
            field.retheme_secret()
        beacon.draw()
    style_tags()
    win.bind('<<AIPetThemeChanged>>', style_tags, add='+')
    displayed = []
    def insert_hook(source, spans):
        def secret(value):
            fields.append(inline_secret(text, value))
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
                    insert_hook(document, spans)
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
                        insert_hook(report['hook_text'], report['hook_spans'])
                    else:
                        text.insert('end', 'Detected value: ')
                        fields.append(inline_secret(text, report['value']))
                    if 'decoded' in report:
                        text.insert('end', '\nDecoded content: ')
                        fields.append(inline_secret(text, report['decoded']))
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
        text.yview_moveto(top)
        beacon.set_level('error' if any(m['level'] == 'error' for r in rows for m in r['messages']) else 'warning')
    win.set_addin_rows = render
    render(rows)

    def close():
        if on_close:
            on_close(list(displayed))
        win.destroy()
    tk.Button(win, text='Close', command=close, **theme.button_style('secondary')).pack(anchor='e', padx=12, pady=8)
    win.protocol('WM_DELETE_WINDOW', close)
    win.bind('<Escape>', lambda event: close())
    theme.theme_window(win)
    style_tags()
    win.update_idletasks()
    screen = screen or geometry.screen_info(0, 0, win)
    left, top, right, bottom = screen['work_area']
    width = min(right-left, max(340, round((right-left) * math.sqrt(0.6))))
    height = min(bottom-top, max(220, round((bottom-top) * math.sqrt(0.6))))
    win.minsize(min(340, right-left), min(220, bottom-top))
    x, y = left + (right-left-width)//2, top + (bottom-top-height)//2
    win.geometry(f'{width}x{height}' + geometry.geo(x, y))
    win.lift()
    return win
