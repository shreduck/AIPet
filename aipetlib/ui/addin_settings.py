"""Generic property editor for built-in and user add-ins."""
import tkinter as tk
from tkinter import filedialog
from tkinter.scrolledtext import ScrolledText

from . import theme


def _slider(parent, master, prop):
    var = (tk.IntVar if prop['type'] == 'integer-slider' else tk.DoubleVar)(master, value=prop['value'])
    tk.Scale(parent, variable=var, from_=prop['min'], to=prop['max'], resolution=prop['step'],
             orient='horizontal', showvalue=True, highlightthickness=0).pack(fill='x', expand=True)
    return var


def build(parent, properties, save, font=None):
    """save(values, completed) must marshal completed(error) back to Tk."""
    body = tk.Frame(parent, padx=16, pady=12)
    win = parent.winfo_toplevel()
    font = font or (lambda size, *style: (theme.UI_FONT, size, *style))
    fields = []
    for prop in properties:
        tk.Label(body, text=prop['label'], anchor='w', font=font(10, 'bold')).pack(fill='x', pady=(8, 2))
        row = tk.Frame(body)
        row.pack(fill='x')
        if prop['type'] == 'boolean':
            var = tk.BooleanVar(win, value=prop['value'])
            tk.Checkbutton(row, variable=var, text='Enabled').pack(side='left')
        elif prop['type'] in ('integer-slider', 'number-slider'):
            var = _slider(row, win, prop)
        elif prop['type'] == 'list':
            editor = ScrolledText(row, height=4, width=32, wrap='none', font=font(10))
            editor.insert('1.0', '\n'.join(prop['value']))
            editor.pack(fill='both', expand=True)
            var = editor
        else:
            var = tk.StringVar(win, value=str(prop['value']))
            tk.Entry(row, textvariable=var, width=24, font=font(10)).pack(side='left', fill='x', expand=True)
            if prop['type'] == 'directory':
                def browse(var=var):
                    folder = filedialog.askdirectory(parent=win, initialdir=var.get(), title='Choose directory')
                    if folder:
                        var.set(folder)
                tk.Button(row, text='Browse…', command=browse, **theme.button_style('secondary')).pack(side='left', padx=(6, 0))
        if prop.get('description'):
            description = tk.Label(body, text=prop['description'], wraplength=360, justify='left', anchor='w', font=font(9))
            description.pack(fill='x')
            description.bind('<Configure>', lambda event, label=description: label.configure(wraplength=max(100, event.width)))
        fields.append((prop, var))
    footer = tk.Frame(body, pady=8)
    footer.pack(fill='x')
    note = tk.Label(footer, text='', wraplength=480, justify='left', anchor='w')
    note.pack(fill='x', pady=8)
    actions = tk.Frame(footer)
    actions.pack(fill='x')

    def completed(error):
        if not body.winfo_exists():
            return
        if error is None:
            note.configure(text='Saved')
        else:
            note.configure(text=error)
        button.configure(state='normal')

    def submit():
        try:
            values = {}
            for prop, var in fields:
                value = ([line.strip() for line in var.get('1.0', 'end-1c').splitlines() if line.strip()]
                         if prop['type'] == 'list' else var.get())
                if prop['type'] in ('integer', 'integer-slider'):
                    value = int(value)
                elif prop['type'] in ('number', 'number-slider'):
                    value = float(value)
                values[prop['key']] = value
        except ValueError:
            note.configure(text=f"Enter a valid {prop['type']} for {prop['label']}.")
            return
        button.configure(state='disabled')
        note.configure(text='Saving…')
        save(values, completed)

    button = tk.Button(actions, text='Save', command=submit, **theme.button_style('primary'))
    button.pack(side='right')
    theme.theme_widget(body)
    return body
