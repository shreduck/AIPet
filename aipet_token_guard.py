"""Named credential rules. Values are isolated in masked, locally revealable reports."""
import base64
import binascii
import hashlib
import json
import math
import re
from bisect import bisect_right
from collections import Counter
from urllib.parse import unquote

from aipet_addin import AIPet, format_markdown_report

PATTERNS = (
    ('API token', re.compile(r'\b(?:sk-(?:proj-|ant-)?[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9]{20,}'
                            r'|github_pat_[A-Za-z0-9_]{20,}|xox[baprs]-[A-Za-z0-9-]{15,}|(?:AKIA|ASIA)[0-9A-Z]{16}'
                            r'|(?:sk|rk)_(?:live|test)_[A-Za-z0-9]{16,}|AIza[A-Za-z0-9_-]{35})\b')),
    ('Bearer token', re.compile(r'\bBearer(?:\s*[:=]\s*|\s+)([A-Za-z0-9_~+./=-]{20,})', re.I)),
    ('JWT', re.compile(r'\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b')),
    ('Private key', re.compile(r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----[\s\S]*?(?:-----END (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|$)')),
    ('Credential assignment', re.compile(r'''\b(?:api[_-]?key|(?:api|access|auth|refresh|id)[_-]?token|token|secret|client[_-]?secret|password)\b["']?\s*(?:[:=]|is\s)\s*["']?([A-Za-z0-9_./+=-]{12,})''', re.I)),
    ('Plain credential pair', re.compile(r'''\b(?:credentials?|login|basic(?:\s+auth)?)\s*(?:are\b|is\b|[:=])?\s*["']?([^\s:"'<>]+:[^\s"'<>]+)''', re.I)),
    ('Connection credentials', re.compile(r'''\b(?:postgres(?:ql)?|mysql|mongodb(?:\+srv)?|redis(?:s)?|amqps?|mssql|https?|s?ftp)://([^\s/@:]+:[^\s/@]+)@''', re.I)),
    ('CLI credentials', re.compile(r'''\bcurl\b[^\r\n]{0,2048}?(?:--user(?:=|\s+)|-u\s*)["']?([^\s"']+:[^\s"']+)''', re.I)),
    ('CLI secret flag', re.compile(r'''--(?:password|passwd|client-secret|token|api-key)(?:=|\s+)["']?([^\s"'<>]+)''', re.I)),
    ('MySQL password flag', re.compile(r'''\bmysql\b[^\r\n]{0,2048}?\s-p([^\s"'<>]+)''', re.I)),
    ('Password assignment', re.compile(r'''\b(?:password|passwd|pwd)\b["']?\s*[:=]\s*["']?([^\s"'<>;,}\]]+)''', re.I)),
)
SENSITIVE_KEY = re.compile(r'^(?:api[_-]?key|(?:api|access|auth|refresh|id)[_-]?token|token|client[_-]?secret|secret|password|authorization|private[_-]?key)$', re.I)
HASH = re.compile(r'(?<![A-Za-z0-9_])[a-fA-F0-9]{32,128}(?![A-Za-z0-9_])')
BASE64 = re.compile(r'(?<![A-Za-z0-9_+/-])[A-Za-z0-9_+/-]{4,8192}={0,2}(?![A-Za-z0-9_+/=-])')
PAIR = re.compile(r'''[^:\s"'{}\[\]<>]{1,128}:[^:\s"'{}\[\]<>]{1,256}''')
BARE_PAIR = re.compile(r'(?<![\w:./\\@-])[A-Za-z0-9]{1,128}:[A-Za-z0-9]{1,128}(?![\w:./\\@-])')
PAIR_CONTEXT = re.compile(r'\b(?:basic(?:\s+auth)?|credentials?|login)\s*(?:[:=]\s*)?$', re.I)
# Inspect each bounded token once. An unanchored greedy prefix would retry at
# every character of large non-encoded strings, holding the GIL and freezing Tk.
_URL_CHARS = r'A-Za-z0-9_.~+/@:%=?&-'
URL_ENCODED = re.compile(r'(?<![' + _URL_CHARS + r'])(?=[' + _URL_CHARS + r']{0,8189}%[0-9a-fA-F]{2})['
                         + _URL_CHARS + r']{3,8192}(?![' + _URL_CHARS + r'])')
COOKIE_HEADER = re.compile(r'\b(?:Cookie|Set-Cookie)\s*:\s*([^\r\n]+)', re.I)
COOKIE = re.compile(r'''(?:^|[;\s])(?:session(?:id|_id|_token)?|connect\.sid|PHPSESSID|JSESSIONID|auth(?:_token)?|access_token|refresh_token|__Secure-[\w-]+)=([^;\s"']{8,})''', re.I)
ENTROPY_ASSIGN = re.compile(r'''\b([A-Za-z_][\w.-]{0,100})\s*(?::=|=>|<-|[:=])\s*["']?([A-Za-z0-9_./+=~$-]{16,1024})''')
RULES = {
    'Possible credential pair': ('credential.possible_pair', 'Possible bare credential pair', 'low',
        'Two alphanumeric values are separated by a colon without explicit credential context; this can also be ordinary text or code.'),
    'Base64 possible pair': ('base64.possible_pair', 'Encoded possible credential pair', 'standard',
        'Local decoding reveals a bare alphanumeric pair separated by a colon, without explicit credential context.'),
    'API token': ('token.known_prefix', 'Known API token prefix', 'standard', 'Matches a recognized provider token prefix and token-shaped suffix.'),
    'Bearer token': ('token.bearer', 'Bearer credential', 'standard', 'A Bearer label is followed by a token-shaped value.'),
    'JWT': ('token.jwt', 'JSON Web Token', 'standard', 'Matches the three encoded segments of a JWT.'),
    'Private key': ('token.private_key', 'Private key header', 'standard', 'A private-key opening header appears in the hook data.'),
    'Credential assignment': ('credential.assignment', 'Credential assignment', 'standard', 'A credential label assigns a value using punctuation or “is”.'),
    'Credential field': ('credential.field', 'Sensitive JSON field', 'standard', 'A field named for a credential contains a non-placeholder value.'),
    'Hash-like value': ('hash.hex', 'Hexadecimal hash or opaque token', 'low', 'A standalone hexadecimal value has a common hash length. It may be a harmless checksum or a credential.'),
    'Base64 credentials': ('base64.credentials', 'Encoded credential structure', 'high', 'Base64 decodes to a username/password pair or JSON containing a credential field.'),
    'Base64 token structure': ('base64.token', 'Encoded token structure', 'high', 'Base64 decodes to a known token format, credential assignment, or hash-like token.'),
    'Plain credential pair': ('credential.basic_plain', 'Labeled username/password pair', 'high', 'Credential or login wording introduces a username/password pair, including short passwords.'),
    'Connection credentials': ('credential.uri', 'Credentials in a connection URL', 'high', 'A connection URL embeds a username and password before the host.'),
    'CLI credentials': ('credential.cli_user', 'CLI username/password argument', 'high', 'curl receives a username/password pair through its authentication flag.'),
    'CLI secret flag': ('credential.cli_secret', 'CLI secret argument', 'high', 'A command-line flag explicitly supplies a password, token, API key or client secret.'),
    'MySQL password flag': ('credential.mysql_flag', 'Inline MySQL password', 'high', 'MySQL receives a password directly after its -p flag.'),
    'Password assignment': ('credential.password', 'Explicit password assignment', 'high', 'A password variable is assigned a non-placeholder value, including short passwords.'),
    'Password field': ('credential.password_field', 'Password JSON field', 'high', 'A password field contains a literal non-placeholder value.'),
    'Session cookie': ('credential.session_cookie', 'Authentication session cookie', 'high', 'A Cookie/Set-Cookie header contains a value under a recognized authentication cookie name.'),
    'Contextual random secret': ('token.entropy_context', 'Random secret in credential context', 'standard', 'A sensitive variable name contains a sufficiently random-looking value; identifier and counter names are excluded.'),
    'Layered encoded credentials': ('encoding.layered', 'Credentials behind multiple encodings', 'high', 'Bounded local decoding reveals credential or token structures behind multiple encoding layers.'),
    'URL encoded credentials': ('encoding.url', 'URL-encoded credential structure', 'high', 'Percent-decoding reveals a credential pair or token structure.'),
}
HIGH_KINDS = {kind for kind, rule in RULES.items() if rule[2] == 'high'}
SEVERITY = {'low': 0, 'standard': 1, 'high': 2}
SETTINGS = [
    dict(key='minimum_warning_level', label='Minimum warning level', type='integer-slider', min=0, max=2, default=0,
         description='0 = Low, 1 = Medium, 2 = High. Blacklist matches always produce High warnings.'),
    dict(key='pair_min_length', label='Minimum length on each side of a bare pair', type='integer-slider',
         min=1, max=32, default=3, description='Applies to bare user:password pairs and their decoded forms; explicit credential labels keep their rules.'),
    dict(key='whitelist', label='Whitelist — ignore matches', type='list', default=[],
         description='One entry per line: exact value, rule:<rule ID>, or sha256:<full value hash>. Inline Whitelist buttons save hashes.'),
    dict(key='blacklist', label='Blacklist — always warn', type='list', default=[],
         description='One entry per line. Literal text is also searched in hook data; rule:<rule ID> and sha256:<hash> upgrade detected matches to High. No regex.'),
    dict(key='whitelist_precedes', label='Whitelist takes precedence over blacklist', type='boolean', default=True),
]
RULES['Blacklist match'] = ('custom.blacklist', 'Configured blacklist value', 'high',
                          'The hook text contains a literal value from the configured blacklist.')


def validate_settings(settings):
    for name in ('whitelist', 'blacklist'):
        for entry in settings.get(name, []):
            if entry.startswith('sha256:') and not re.fullmatch(r'sha256:[0-9a-fA-F]{64}', entry):
                raise ValueError(f'{name}: sha256 entries require a complete 64-digit hexadecimal hash')
            if entry.startswith('rule:') and entry[5:] not in {rule[0] for rule in RULES.values()}:
                raise ValueError(f'{name}: unknown Token guard rule ID')


def _listed(entries, rule, value, decoded=None):
    values = {value, decoded} - {None}
    hashes = {'sha256:' + hashlib.sha256(item.encode('utf-8')).hexdigest() for item in values}
    return any(entry == 'rule:' + rule or entry in values or entry.lower() in hashes for entry in entries)


def report_markdown(report):
    """The add-in owns the report's Markdown layout; AIPet hosts the generic renderer."""
    content = ('# Token guard report\n\n'
               '- **Warning level:** {{warning_level}}\n'
               '- **Rule:** {{rule}} — {{name}}\n'
               '- **Timestamp:** {{timestamp}}\n\n'
               '## Conversation\n\n'
               '- **Title:** {{context.conversation_title}}\n'
               '- **AI harness:** {{context.harness}}\n'
               '- **Session:** {{context.session_id}}\n')
    for key, label in (('project', 'Project'), ('cwd', 'Directory'), ('environment', 'Environment'),
                       ('model', 'Model'), ('agent', 'AI engine'), ('source', 'Session source'), ('entry', 'Harness entry'),
                       ('hook_event', 'Hook event'), ('tool', 'Tool'),
                       ('agent_name', 'Agent name'), ('agent_id', 'Agent ID'), ('event_id', 'Event ID')):
        if report.get('context', {}).get(key):
            content += '- **' + label + ':** {{context.' + key + '}}\n'
    content += ('\n## Finding\n\n{{explanation}}\n\n'
                '**Fingerprint:** {{fingerprint}}\n\n'
                '## Hook text\n\n**Field:** {{hook_field}}\n\n{{hook_text}}\n')
    if 'decoded' in report:
        content += '\n## Decoded content\n\n{{decoded}}\n'
    if report.get('truncated'):
        content += '\n_Decoded/legacy previews are limited to 4 KiB; matching hook text is preserved in full._\n'
    return content + '\n_Values stay censored until you use their eye controls._\n'


def _entropy_secret(name, value):
    name = re.sub(r'([a-z0-9])([A-Z])', r'\1_\2', name)
    if not re.search(r'secret|token|api[_.-]?key|credential|auth', name, re.I):
        return False
    if re.search(r'(?:^|[_.-])(?:id|uuid|identifier|count|length|size|hash|sha\d*|checksum|public|input|output)(?:$|[_.-])|tokenizer', name, re.I):
        return False
    if len(value) < 16 or _placeholder(value):
        return False
    return -sum((count / len(value)) * math.log2(count / len(value)) for count in Counter(value).values()) >= 3.5


def _credential_pair(value):
    if '://' in value or ':' not in value:
        return False
    user, password = value.split(':', 1)
    password = unquote(password)
    return bool(user and password and not _placeholder(password))


def _bare_pair(value, min_length=3):
    if not BARE_PAIR.fullmatch(value):
        return False
    user, password = value.split(':', 1)
    # Numeric prefixes are common in grep/compiler output: 19:import is not a login.
    return (min(len(user), len(password)) >= min_length and not user.isdigit() and not password.isdigit()
            and user.lower() not in ('http', 'https', 'ftp', 'mailto', 'urn'))


def _decode_candidate(encoded, url=False, credential_context=False, pair_min_length=3):
    current = encoded
    for depth in range(3):
        try:
            if re.search(r'%[0-9a-fA-F]{2}', current):
                decoded = unquote(current, errors='strict')
            else:
                decoded = base64.b64decode(current + '=' * (-len(current) % 4), altchars=b'-_', validate=True).decode('utf-8')
        except (ValueError, UnicodeError, binascii.Error):
            return None
        if decoded == current:
            return None
        kind = _decoded_kind(decoded.strip(), credential_context=credential_context or url, pair_min_length=pair_min_length)
        if kind:
            return (kind if kind == 'Base64 possible pair' else
                    'Layered encoded credentials' if depth else 'URL encoded credentials' if url else kind), decoded
        current = decoded
    return None


def _placeholder(value):
    return value.startswith(('${', '<', 'your_', 'YOUR_', '{{')) or bool(re.fullmatch(r'\$[A-Za-z_]\w*|%[A-Za-z_]\w*%', value))


def _decoded_kind(text, credential_context=False, pair_min_length=3):
    """Recognize credential structures in decoded text, never report that text."""
    if not all(char.isprintable() or char in '\r\n\t' for char in text):
        return None
    if PAIR.fullmatch(text) and _credential_pair(text):
        if not credential_context and (text.split(':', 1)[1].isdigit() or re.match(r'^[A-Za-z]:[\\/]', text)):
            return None
        if BARE_PAIR.fullmatch(text):
            if not credential_context and not _bare_pair(text, pair_min_length):
                return None
            if not credential_context:
                return 'Base64 possible pair'
        return 'Base64 credentials'
    for kind, pattern in PATTERNS:
        for match in pattern.finditer(text):
            secret = match.group(1) if pattern.groups else match.group()
            if _placeholder(secret):
                continue
            if kind in ('Plain credential pair', 'Connection credentials', 'CLI credentials') and not _credential_pair(secret):
                continue
            return 'Base64 credentials' if RULES[kind][2] == 'high' else 'Base64 token structure'
    if HASH.fullmatch(text):
        return 'Base64 token structure'
    for header in COOKIE_HEADER.finditer(text):
        if any(not _placeholder(match.group(1)) for match in COOKIE.finditer(header.group(1))):
            return 'Base64 credentials'
    try:
        pending = [json.loads(text)]
    except (ValueError, RecursionError):
        return None
    for _ in range(128):
        if not pending:
            break
        value = pending.pop()
        if isinstance(value, dict):
            for key, child in value.items():
                if SENSITIVE_KEY.fullmatch(str(key)) and isinstance(child, str) and child and not _placeholder(child):
                    return 'Base64 credentials'
                if isinstance(child, str) and _entropy_secret(str(key), child):
                    return 'Base64 token structure'
                if isinstance(child, (dict, list)):
                    pending.append(child)
        elif isinstance(value, list):
            pending.extend(child for child in value[:128] if isinstance(child, (dict, list)))
    return None


def detect(data, settings=None):
    """Return structured reports; values must only be rendered through masked controls."""
    found = {}
    settings = settings or {}
    minimum_level = settings.get('minimum_warning_level', 0)
    pair_min_length = settings.get('pair_min_length', 3)
    whitelist, blacklist = settings.get('whitelist', []), settings.get('blacklist', [])
    source_spans = {}
    allowed_spans = {}
    def record(kind, value, decoded=None, preserve=False, source=None, path=(), span=None):
        source = value if source is None else source
        fingerprint = hashlib.sha256(value.encode('utf-8')).hexdigest()[:16]
        rule, name, level, explanation = RULES[kind]
        blocked = _listed(blacklist, rule, value, decoded)
        if _listed(whitelist, rule, value, decoded) and (settings.get('whitelist_precedes', True) or not blocked):
            allowed_spans.setdefault(path, []).append(span or (0, len(source)))
            return False
        if blocked:
            level, explanation = 'high', explanation + ' This finding matches the configured blacklist.'
        if SEVERITY[level] < minimum_level:
            return False
        source_spans.setdefault(path, []).append(span or (0, len(source)))
        if fingerprint in found:
            old_severity, new_severity = SEVERITY[found[fingerprint]['warning_level']], SEVERITY[level]
            if old_severity > new_severity or preserve and old_severity >= new_severity:
                return True
        # Bound transport size while making truncation explicit in the report.
        def bounded(text):
            return text.encode('utf-8')[:4096].decode('utf-8', errors='ignore')
        report = dict(kind=kind, rule=rule, name=name, warning_level=level, explanation=explanation,
                      fingerprint=fingerprint, value=bounded(value), truncated=len(value.encode('utf-8')) > 4096,
                      hook_text=source, hook_field='.'.join(path) or '$', _path=path, _span=span or (0, len(source)))
        if decoded is not None:
            report.update(decoded=bounded(decoded), truncated=report['truncated'] or len(decoded.encode('utf-8')) > 4096)
        found[fingerprint] = report
        return True
    pending = [(data, 0, ())]
    budget = 262144
    decode_budget, node_budget = 128, 4096
    while pending and budget > 0 and node_budget > 0:
        node_budget -= 1
        value, depth, path = pending.pop()
        if depth > 30:
            continue
        if isinstance(value, dict):
            for key, child in value.items():
                if SENSITIVE_KEY.fullmatch(str(key)) and isinstance(child, str) and (len(child) >= 12 or str(key).lower() == 'password' and child):
                    if not _placeholder(child):
                        record('Password field' if str(key).lower() == 'password' else 'Credential field', child, path=path + (str(key),))
                elif isinstance(child, str) and _entropy_secret(str(key), child):
                    record('Contextual random secret', child, path=path + (str(key),))
                pending.append((child, depth + 1, path + (str(key),)))
        elif isinstance(value, list):
            pending.extend((child, depth + 1, path + (str(index),)) for index, child in enumerate(value))
        elif isinstance(value, str):
            text = value[:budget]
            budget -= len(text)
            for match in HASH.finditer(text):
                if len(match.group()) in (32, 40, 64, 96, 128):
                    record('Hash-like value', match.group(), preserve=True, source=text, path=path, span=match.span())
            for match in BARE_PAIR.finditer(text):
                if _bare_pair(match.group(), pair_min_length):
                    record('Possible credential pair', match.group(), preserve=True, source=text, path=path, span=match.span())
            for kind, pattern in PATTERNS:
                for match in pattern.finditer(text):
                    secret = match.group(1) if pattern.groups else match.group()
                    if _placeholder(secret):
                        continue
                    if kind in ('Plain credential pair', 'Connection credentials', 'CLI credentials') and not _credential_pair(secret):
                        continue
                    record(kind, secret, source=text, path=path, span=match.span(1) if pattern.groups else match.span())
            for match in ENTROPY_ASSIGN.finditer(text):
                if _entropy_secret(match.group(1), match.group(2)):
                    record('Contextual random secret', match.group(2), preserve=True, source=text, path=path, span=match.span(2))
            cookie_sources = [(match.group(1), match.start(1)) for match in COOKIE_HEADER.finditer(text)]
            if path and path[-1].lower() in ('cookie', 'set-cookie'):
                cookie_sources.append((text, 0))
            for header, offset in cookie_sources:
                for match in COOKIE.finditer(header):
                    if not _placeholder(match.group(1)):
                        record('Session cookie', match.group(1), source=text, path=path,
                               span=(offset + match.start(1), offset + match.end(1)))
            candidates = [(match, True) for match in URL_ENCODED.finditer(text)]
            candidates.extend((match, False) for match in BASE64.finditer(text))
            for match, url in candidates:
                if decode_budget <= 0:
                    break
                decode_budget -= 1
                encoded = match.group()
                explicit = bool(PAIR_CONTEXT.search(text[max(0, match.start()-80):match.start()]))
                decoded_match = _decode_candidate(encoded, url=url, credential_context=explicit, pair_min_length=pair_min_length)
                if decoded_match:
                    kind, decoded = decoded_match
                    record(kind, encoded, decoded, source=text, path=path, span=match.span())
            for literal in dict.fromkeys(entry for entry in blacklist if not entry.startswith(('rule:', 'sha256:'))):
                first = text.find(literal)
                if first >= 0:
                    if record('Blacklist match', literal, source=text, path=path, span=(first, first+len(literal))):
                        start = text.find(literal, first+len(literal))
                        while start >= 0:
                            source_spans[path].append((start, start+len(literal)))
                            start = text.find(literal, start+len(literal))
    # A flood of low-confidence hashes must not hide a stronger encoded credential finding.
    for path, ranges in allowed_spans.items():
        merged = []
        for start, end in sorted(set(ranges)):
            if merged and start <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(merged[-1][1], end))
            else:
                merged.append((start, end))
        allowed_spans[path] = merged

    def allowed(path, span):
        if not settings.get('whitelist_precedes', True):
            return False
        ranges = allowed_spans.get(path, [])
        index = bisect_right(ranges, (span[0], math.inf))-1
        return index >= 0 and span[1] <= ranges[index][1]
    reports = sorted((report for report in found.values() if not allowed(report['_path'], report['_span'])),
                     key=lambda item: (-SEVERITY[item['warning_level']], item['kind'], item['fingerprint']))[:10]
    for report in reports:
        spans = []
        path = report.pop('_path')
        report.pop('_span')
        for start, end in sorted(set(source_spans[path])):
            if allowed(path, (start, end)):
                continue
            if spans and start <= spans[-1][1]:
                spans[-1][1] = max(spans[-1][1], end)
            else:
                spans.append([start, end])
        report['hook_spans'] = spans
    return reports


def findings(data):
    """Compatibility summary: constant labels and fingerprints only."""
    return [(report['kind'], report['fingerprint']) for report in detect(data)]


def on_event(event, context):
    api = AIPet(context)
    for report in detect(event.get('data'), context.get('settings')):
        details = dict(context.get('session') or {})
        data = event.get('data') if isinstance(event.get('data'), dict) else {}
        agent = str(event.get('agent') or context.get('agent') or '')
        details.update(agent=agent, session_id=str(event.get('session_id') or context.get('session_id') or ''),
                       hook_event=str(event.get('event') or ''), event_id=str(event.get('id') or ''),
                       event_time=str(event.get('timestamp') or ''), tool=str(data.get('tool_name') or ''),
                       agent_id=str(data.get('agent_id') or data.get('subagent_id') or ''))
        details['harness'] = details.get('harness') or ('Codex' if agent == 'codex' else 'Claude Code' if agent == 'claude' else agent)
        details['cwd'] = details.get('cwd') or str(data.get('cwd') or '')
        details['model'] = details.get('model') or (data.get('model') if isinstance(data.get('model'), str) else '')
        details['agent_name'] = str(data.get('agent_name') or data.get('agent_type') or '')
        report['context'] = {key: str(value).encode('utf-8')[:256].decode('utf-8', errors='ignore')
                             for key, value in details.items() if value}
        report['markdown_template'] = report_markdown(report)
        level = report['warning_level']
        notify = api.error if level == 'high' else api.warning
        title = {'low': 'Low warning: possible secret', 'standard': 'Possible secret in hook data',
                 'high': 'High warning: potential credentials'}[level]
        content, _ = format_markdown_report(report, event.get('timestamp'), include_source=False)
        notify(title,
                    content,
                    key=report['rule'] + ':' + report['fingerprint'], window='pet', report=report)
