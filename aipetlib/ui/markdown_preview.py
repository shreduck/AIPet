"""Local Markdown-to-Tk text preview, with opaque placeholders for revealable spans."""
import re
import uuid

from . import theme


def configure(text):
    for level, size in ((1, 18), (2, 15), (3, 12)):
        text.tag_configure(f'md-h{level}', font=(theme.UI_FONT, size, 'bold'), spacing1=8, spacing3=4)
    text.tag_configure('md-bold', font=(theme.UI_FONT, 10, 'bold'))
    text.tag_configure('md-italic', font=(theme.UI_FONT, 10, 'italic'))
    text.tag_configure('md-code', font=(theme.UI_FONT, 10), background=theme.T['code_bg'], foreground=theme.T['code_fg'])
    text.tag_configure('md-quote', foreground=theme.T['muted'], lmargin1=16, lmargin2=16)


def render(text, source, spans=(), insert_secret=None):
    """Render a small safe Markdown subset. No HTML execution, URL fetching, or image loading."""
    prefix = '\ue000' + uuid.uuid4().hex + ':'
    secrets, parts, cursor = [], [], 0
    for start, end in spans:
        parts.extend((source[cursor:start], prefix + str(len(secrets)) + '\ue001'))
        secrets.append(source[start:end])
        cursor = end
    parts.append(source[cursor:])
    masked = ''.join(parts)
    placeholder = re.escape(prefix) + r'(\d+)\ue001'
    tokens = re.compile(placeholder + r'|\\([\\`*_{}\[\]()#+.!|>~-])|(`[^`\n]+`|\*\*[^*\n]+\*\*|__[^_\n]+__|\*[^*\n]+\*|_[^_\n]+_|\[[^\]\n]+\]\([^\)\n]+\))')

    def inline(value, tags=(), depth=0):
        cursor = 0
        for match in tokens.finditer(value):
            text.insert('end', value[cursor:match.start()], tags)
            token = match.group()
            if match.group(1) is not None:
                insert_secret(secrets[int(match.group(1))])
            elif match.group(2) is not None:
                text.insert('end', match.group(2), tags)
            elif depth < 4 and token.startswith(('**', '__')):
                inline(token[2:-2], tags + ('md-bold',), depth+1)
            elif depth < 4 and token.startswith(('*', '_')):
                inline(token[1:-1], tags + ('md-italic',), depth+1)
            elif token.startswith('`'):
                inline(token[1:-1], tags + ('md-code',), 4)
            elif token.startswith('['):
                label, url = token[1:-1].split('](', 1)
                inline(label + ' (' + url + ')', tags, 4)
            else:
                text.insert('end', token, tags)
            cursor = match.end()
        text.insert('end', value[cursor:], tags)

    fenced = False
    for line in masked.splitlines(keepends=True):
        if re.match(r'^\s*(```|~~~)', line):
            if prefix in line:
                inline(re.sub(r'^\s*(?:```|~~~)', '', line), ('md-code',))
            fenced = not fenced
            continue
        if fenced:
            # Only replace secret placeholders in code blocks; keep all Markdown syntax literal.
            cursor = 0
            for match in re.finditer(placeholder, line):
                text.insert('end', line[cursor:match.start()], 'md-code')
                insert_secret(secrets[int(match.group(1))])
                cursor = match.end()
            text.insert('end', line[cursor:], 'md-code')
            continue
        heading = re.match(r'^(#{1,6})\s+(.*?)(\n?)$', line)
        if heading:
            inline(heading.group(2), (f'md-h{min(3, len(heading.group(1)))}',))
            text.insert('end', '\n')
        elif re.match(r'^\s*([-*_])(?:\s*\1){2,}\s*$', line):
            text.insert('end', '─' * 40 + '\n')
        elif line.startswith('> '):
            inline(line[2:], ('md-quote',))
        else:
            inline(re.sub(r'^(\s*)[-+*]\s+', r'\1• ', line))
