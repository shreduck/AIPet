"""One-time introduction to custom hook observers and their AI authoring guide."""
import tkinter as tk

from ..runtime import paths, settings
from ..ui import theme


class AddinIntro:
    def __init__(self, app):
        self.app = app
        self.win = None

    def maybe_show(self):
        app = self.app
        if app.pet.cfg.get('addins_intro_seen') is True or self.win is not None:
            return
        if app.startup_dialogs_open():
            app.root.after(1000, self.maybe_show)
            return

        def content(parent):
            row = tk.Frame(parent)
            row.pack(fill='x', pady=(12, 0))
            note = tk.Label(row, text='', anchor='w', justify='left', wraplength=580,
                            font=(theme.UI_FONT, 9))

            def copy():
                ok = app.copy_addin_guide(notify=False)
                note.configure(text='Copied. Paste into your AI assistant and describe the add-in you want.' if ok
                               else 'Could not copy. Try again or use Add-ins → Copy Markdown for AI later.')

            tk.Button(row, text='Copy Markdown for AI', command=copy,
                      **theme.button_style('secondary')).pack(anchor='w')
            note.pack(fill='x', pady=(6, 0))
            theme.theme_widget(row)

        def dismissed(_result):
            self.win = None
            app.pet.cfg['addins_intro_seen'] = True
            if not settings.save_setting('addins_intro_seen', True):
                app.info('Could not save the add-in introduction preference. It may appear again next time.', error=True)

        self.win = theme.themed_dialog(app.root, 'Build your own AIPet add-ins',
            'AIPet now supports custom add-ins: scripts can observe AI hook events, show status and alerts, '
            'and expose settings in the AIPet menu.\n\n'
            '1. Copy the Markdown guide below and ask your AI assistant to build an add-in.\n'
            f'2. Save its addin.json and script in {paths.HOME_DIR}/addins/<id>/.\n'
            '3. Open AIPet menu → Add-ins → Reload add-ins, then enable it.\n\n'
            'Limits: add-ins observe events; they cannot approve, reject or block requests. Scripts run in '
            'worker processes with time and queue limits. Settings and alerts use AIPet’s interface. '
            'Scripts run with your account permissions, so enable only code you trust.\n\n'
            'You can copy this guide again from Add-ins → Copy Markdown for AI.',
            buttons=(('Dismiss', True, 'primary'),), cancel=True, on_result=dismissed, content_builder=content)
