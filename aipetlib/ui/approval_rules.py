"""Shared full-list editors and precedence switch for normal and timed approval."""
import tkinter as tk

from .. import permissions
from . import theme
from .toggle_switch import ToggleSwitch


class RuleLists:
    def __init__(self, parent, rules, compact=False):
        self.frame = tk.Frame(parent)
        self.frame.pack(fill='both', expand=True, pady=(10, 0))
        self.white_first = tk.BooleanVar(master=parent, value=rules.get('precedence') == 'whitelist')
        switch_row = tk.Frame(self.frame)
        switch_row.pack(fill='x')
        self.switch = ToggleSwitch(switch_row, self.white_first)
        self.switch.pack(side='left')
        self.switch_label = tk.Label(switch_row, font=(theme.UI_FONT, 9, 'bold'), cursor='hand2')
        self.switch_label.pack(side='left', padx=(8, 0))
        self.switch_label.bind('<Button-1>', self.switch.toggle)
        self.hint = tk.Label(self.frame, justify='left', wraplength=620, fg=theme.T['muted'], font=(theme.UI_FONT, 8))
        self.hint.pack(anchor='w', pady=(2, 8))
        columns = tk.Frame(self.frame)
        columns.pack(fill='both', expand=True)
        columns.rowconfigure(0, weight=1)
        self.boxes = {}
        self.last_errors = []
        self.on_change = None
        for index, (key, title, hint) in enumerate((
                ('whitelist', 'Whitelist — approve matches', 'Full regex match: command/path/URL, tool name or Tool(command).'),
                ('blacklist', 'Blacklist — pause/reject matches', 'Regex search anywhere. Normal mode asks; timed mode pauses or rejects.'))):
            col = tk.Frame(columns)
            col.grid(row=0, column=index, sticky='nsew', padx=(0 if index == 0 else 12, 0))
            columns.columnconfigure(index, weight=1)
            tk.Label(col, text=title, font=(theme.UI_FONT, 9, 'bold')).pack(anchor='w')
            tk.Label(col, text=hint, wraplength=290, height=3, justify='left', anchor='nw',
                     fg=theme.T['muted'], font=(theme.UI_FONT, 8)).pack(fill='x')
            area = tk.Frame(col)
            area.pack(fill='both', expand=True)
            text = tk.Text(area, width=34 if compact else 40, height=5 if compact else 9,
                           wrap='none', font=(theme.UI_FONT, 9), undo=True, padx=6, pady=6)
            vertical = tk.Scrollbar(area, command=text.yview)
            vertical.pack(side='right', fill='y')
            horizontal = tk.Scrollbar(area, orient='horizontal', command=text.xview)
            horizontal.pack(side='bottom', fill='x')
            text.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
            text.pack(fill='both', expand=True)
            lines = rules.get(key)
            if lines is None:
                lines = permissions.DEFAULT_BLACKLIST if key == 'blacklist' else []
            text.insert('1.0', '\n'.join(lines))
            text.edit_modified(False)
            text.bind('<<Modified>>', self.edited)
            # Borderless Windows dialogs need explicit activation after a button was used.
            text.bind('<ButtonPress-1>', lambda event, target=text: target.focus_force())
            # Keep Return inside the multiline editor instead of reaching the modal's cancel binding.
            def newline(_event, target=text):
                target.insert('insert', '\n')
                return 'break'
            text.bind('<Return>', newline)
            text.bind('<KP_Enter>', newline)
            self.boxes[key] = text
        self.white_first.trace_add('write', self.sync_hint)
        self.sync_hint()

    def sync_hint(self, *_):
        white = self.white_first.get()
        self.switch_label.configure(text='Whitelist takes precedence' if white else 'Blacklist takes precedence')
        self.hint.configure(text='When both lists match, the whitelist approves the request.' if white else
                            'When both lists match, the blacklist wins. Switch on to give the whitelist precedence.')

    def read(self):
        # These edits are included in this validation/save attempt. A queued
        # <<Modified>> notification must not erase the error it produces.
        for text in self.boxes.values():
            text.edit_modified(False)
        raw = {key: text.get('1.0', 'end-1c').splitlines() for key, text in self.boxes.items()}
        bad = [(key, number, pattern, error) for key, lines in raw.items()
               for number, pattern, error in permissions.bad_patterns(lines)]
        self.last_errors = bad
        if bad:
            raise ValueError('Fix these patterns first:\n' + '\n'.join(
                f'{key} line {number}: {pattern} ({error})' for key, number, pattern, error in bad[:6]))
        lists = {key: [line.rstrip() for line in lines if line.strip()] for key, lines in raw.items()}
        return dict(lists, precedence='whitelist' if self.white_first.get() else 'blacklist')

    def focus_error(self):
        if not self.last_errors:
            return
        key, line, _, _ = self.last_errors[0]
        text = self.boxes[key]
        text.tag_remove('sel', '1.0', 'end')
        text.tag_add('sel', f'{line}.0', f'{line}.end')
        text.mark_set('insert', f'{line}.0')
        text.see(f'{line}.0')
        text.focus_force()

    def edited(self, event):
        if event.widget.edit_modified():
            event.widget.edit_modified(False)
            self.last_errors = []
            if self.on_change is not None:
                self.on_change()

    def set_disabled(self, disabled):
        self.switch.set_disabled(disabled)
        for widget in self.boxes.values():
            widget.configure(state='disabled' if disabled else 'normal')
