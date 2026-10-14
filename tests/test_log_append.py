"""The agent window's log appends new lines instead of rebuilding: the result must equal a full rebuild."""
import importlib.util
import unittest
from types import SimpleNamespace
from unittest.mock import patch


class TextModel:
    """Just enough of a Tk Text to compare what the log holds: characters with their tags, and marks."""

    def __init__(self):
        self.chars, self.marks = [], {}

    # -- indexes: offsets into chars ("end" and "end-1c" are the end: no implicit final newline here)
    def _off(self, spec):
        spec = str(spec)
        if spec in ("end", "end-1c", "insert"):
            return len(self.chars)
        if spec.startswith("@"):
            return 0
        if spec in self.marks:
            return self.marks[spec]
        if spec.endswith(".first") or spec.endswith(".last"):
            tag, _, edge = spec.rpartition(".")
            spots = [k for k, (_, tags) in enumerate(self.chars) if tag in tags]
            if not spots:
                raise ValueError(spec)
            return spots[0] if edge == "first" else spots[-1] + 1
        line, col = (int(x) for x in spec.split("."))
        k = 0
        for _ in range(line - 1):
            k = next(j for j in range(k, len(self.chars)) if self.chars[j][0] == "\n") + 1
        return k + col

    def index(self, spec):
        k = self._off(spec)
        text = "".join(c for c, _ in self.chars[:k])
        return f"{text.count(chr(10)) + 1}.{len(text) - text.rfind(chr(10)) - 1}"

    def insert(self, spec, text, tags=()):
        k = self._off(spec)
        tags = {tags} if isinstance(tags, str) else set(tags)
        self.chars[k:k] = [[c, set(tags)] for c in text]
        for name, at in self.marks.items():
            if at > k:
                self.marks[name] = at + len(text)

    def delete(self, a, b=None):
        a = self._off(a)
        b = self._off(b) if b is not None else a + 1
        del self.chars[a:b]
        for name, at in self.marks.items():
            self.marks[name] = a if a < at < b else (at - (b - a) if at >= b else at)

    def tag_add(self, tag, a, b):
        for c in self.chars[self._off(a):self._off(b)]:
            c[1].add(tag)

    def tag_delete(self, tag):
        for c in self.chars:
            c[1].discard(tag)

    def tag_ranges(self, tag):
        return (1,) if any(tag in tags for _, tags in self.chars) else ()

    def mark_set(self, name, spec):
        self.marks[name] = self._off(spec)

    def cget(self, key):
        return 0

    def winfo_width(self):
        return 900

    def yview(self, *args):
        return (0.0, 1.0)

    def __getattr__(self, name):  # configure, tag_configure, tag_bind, see, yview_moveto, after_idle...
        return lambda *a, **k: None

    def runs(self):
        """[(text, tags)] with entry tags numbered in order of appearance (appending numbers them on)."""
        names, out = {}, []
        for c, tags in self.chars:
            shown = []
            for t in sorted(tags):
                if t.startswith("entry"):
                    t = "entry#" + str(names.setdefault(t, len(names)))
                shown.append(t)
            key = tuple(x for x in shown if x != "odd")
            if out and out[-1][1] == key:
                out[-1][0] += c
            else:
                out.append([c, key])
        return [tuple(r) for r in out]

    def odd_alternates(self):
        """Every other entry shaded, whatever number the first one has."""
        flags = []
        seen = set()
        for c, tags in self.chars:
            entry = next((t for t in tags if t.startswith("entry")), None)
            if entry and entry not in seen:
                seen.add(entry)
                flags.append("odd" in tags)
        return all(a != b for a, b in zip(flags, flags[1:]))


class Font:
    def measure(self, text):
        return 7 * len(text)


P = {"mu": "#888888", "blue": "#3366ff", "hi": "#ffffff", "red": "#ff0000", "amb": "#ffaa00", "bar": "#222222",
     "bg": "#111111", "grn": "#00aa00", "tx": "#dddddd"}


def make_window(net, sel=-1):
    return SimpleNamespace(net=net, sel=sel, theme="dark", s=1.5, opts={"log_tail": True, "text_scale": 100},
                           P=P, f_main=Font(), f_bold=Font(), px=lambda n: n, color=lambda i: "#123456")


def agent(aid, name, state="working"):
    return {"id": aid, "name": name, "main": aid == "", "kind": "", "state": state, "model_label": "opus",
            "effort": "high", "ctx": 1000, "tokens": None, "usage_at": {}, "lines": [], "parent": ""}


@unittest.skipUnless(importlib.util.find_spec("tkinter"), "Tk is not available")
class LogAppendTests(unittest.TestCase):
    def panel(self, net, sel=-1):
        from aipetlib.ui.agent_logs import LogPanel
        panel = LogPanel(make_window(net, sel))
        panel.text = TextModel()
        return panel

    def net(self, n):
        agents = [agent("", "main"), agent("s1", "helper")]
        for k in range(n):
            a = agents[k % 2]
            a["lines"].append((1000.0 + k, ("say", "tool", "think")[k % 3], f"line {k} " + "text " * (k % 7)))
        agents[0]["usage_at"] = {1000.0 + k: (k * 100, k) for k in range(0, n, 4)}
        return {"agents": agents, "log": []}

    def grow(self, net, start, count):
        for k in range(start, start + count):
            net["agents"][k % 2]["lines"].append((1000.0 + k, "say", f"new line {k}"))

    def test_appending_new_lines_equals_a_full_rebuild(self):
        net = self.net(170)  # more than the 160 shown: the oldest drop off as lines arrive
        panel = self.panel(net)
        panel.draw()
        self.grow(net, 170, 7)
        with patch.object(type(panel), "rebuild", wraps=panel.rebuild) as rebuild:
            panel.draw()
            rebuild.assert_not_called()  # appended, not rebuilt
        fresh = self.panel(net)
        fresh.draw()
        self.assertEqual(panel.text.runs(), fresh.text.runs())
        self.assertTrue(panel.text.odd_alternates())
        self.assertEqual(sorted(e[0] for e in panel.entries.values()), sorted(e[0] for e in fresh.entries.values()))

    def test_selected_agent_header_follows_its_figures(self):
        net = self.net(20)
        panel = self.panel(net, sel=0)
        panel.draw()
        net["agents"][0]["ctx"] = 84000
        self.grow(net, 20, 2)
        panel.draw()
        fresh = self.panel(net, sel=0)
        fresh.draw()
        self.assertEqual(panel.text.runs(), fresh.text.runs())
        self.assertIn("ctx 84k", "".join(c for c, _ in panel.text.chars).splitlines()[0])

    def test_a_finished_agent_marks_its_result_even_without_new_lines(self):
        net = self.net(20)
        panel = self.panel(net)
        panel.draw()
        net["agents"][1]["state"] = "done"
        panel.draw()  # no new line: its last reply turns into its result
        fresh = self.panel(net)
        fresh.draw()
        self.assertEqual(panel.text.runs(), fresh.text.runs())
        self.assertTrue(any("final" in tags for _, tags in panel.text.runs()))

    def test_anything_else_rebuilds(self):
        net = self.net(20)
        panel = self.panel(net)
        panel.draw()
        net["agents"][0]["lines"].insert(0, (999.0, "say", "an older line read late"))
        with patch.object(type(panel), "rebuild", wraps=panel.rebuild) as rebuild:
            panel.draw()
            rebuild.assert_called_once()


if __name__ == "__main__":
    unittest.main()
