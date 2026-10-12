"""Standalone MCP transport and Workbench session polling."""
import json
import os
import threading
import time
import urllib.error
import urllib.request
from .records import STATE_ORDER, first


class McpHttpClient:
    """Minimal MCP streamable-HTTP client: initialize + tools/call."""

    def __init__(self, wcfg):
        self.cfg = wcfg
        self.session_id = None
        self.protocol = None
        self._next_id = 0

    def _headers(self):
        h = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
        if self.session_id:
            h["Mcp-Session-Id"] = self.session_id
        if self.protocol:
            h["MCP-Protocol-Version"] = self.protocol
        key = os.environ.get(self.cfg.get("api_key_env") or "", "")
        if key:  # key is read from the environment, never stored in config
            scheme = self.cfg.get("auth_scheme") or ""
            h[self.cfg.get("auth_header") or "Authorization"] = f"{scheme} {key}".strip()
        return h

    def _post(self, method, params=None, notify=False):
        payload = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            payload["params"] = params
        if not notify:
            self._next_id += 1
            payload["id"] = self._next_id
        req = urllib.request.Request(self.cfg["mcp_url"], data=json.dumps(payload).encode("utf-8"),
                                     headers=self._headers(), method="POST")
        with urllib.request.urlopen(req, timeout=self.cfg.get("timeout_seconds", 20)) as resp:
            sid = resp.headers.get("Mcp-Session-Id")
            if sid:
                self.session_id = sid
            if notify:
                return None
            ctype = resp.headers.get("Content-Type", "")
            raw = resp.read().decode("utf-8", "replace")
        msg = self._parse(raw, ctype, payload["id"])
        if "error" in msg:
            raise RuntimeError(f"MCP error: {msg['error']}")
        return msg.get("result", {})

    @staticmethod
    def _parse(raw, ctype, want_id):
        if "text/event-stream" in ctype:
            for block in raw.replace("\r\n", "\n").split("\n\n"):
                data = "\n".join(l[5:].lstrip() for l in block.split("\n") if l.startswith("data:"))
                if not data:
                    continue
                try:
                    msg = json.loads(data)
                except ValueError:
                    continue
                if isinstance(msg, dict) and msg.get("id") == want_id:
                    return msg
            raise RuntimeError("no matching response in event stream")
        return json.loads(raw)

    def _ensure_session(self):
        if self.protocol:
            return
        res = self._post("initialize", {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "aipet", "version": "1.0"},
        })
        self.protocol = res.get("protocolVersion", "2025-06-18")
        try:
            self._post("notifications/initialized", notify=True)
        except Exception:
            pass

    def call_tool(self, name, args):
        self._ensure_session()
        try:
            return self._post("tools/call", {"name": name, "arguments": args})
        except urllib.error.HTTPError as e:
            if e.code in (400, 404):  # session expired -> re-initialize once
                self.session_id = self.protocol = None
                self._ensure_session()
                return self._post("tools/call", {"name": name, "arguments": args})
            raise


def tool_result_to_obj(result):
    texts = [c.get("text", "") for c in result.get("content", []) if c.get("type") == "text"]
    joined = "\n".join(texts)
    if result.get("isError"):
        raise RuntimeError("tool error: " + joined[:200])
    if isinstance(result.get("structuredContent"), (dict, list)):
        return result["structuredContent"]
    try:
        return json.loads(joined)
    except ValueError:
        return {"text": joined}


ID_KEYS = ("chatId", "id", "uuid")


def find_chat_list(obj, depth=0):
    """Find the first list of chat-like dicts anywhere in the response."""
    if depth > 5:
        return None
    if isinstance(obj, list):
        if not obj:
            return []
        if all(isinstance(x, dict) for x in obj) and any(k in obj[0] for k in ID_KEYS):
            return obj
        for x in obj:
            r = find_chat_list(x, depth + 1)
            if r:
                return r
        return None
    if isinstance(obj, dict):
        for k in ("items", "chats", "content", "results", "data", "rows"):
            if k in obj:
                r = find_chat_list(obj[k], depth + 1)
                if r is not None:
                    return r
        for v in obj.values():
            if isinstance(v, (list, dict)):
                r = find_chat_list(v, depth + 1)
                if r:
                    return r
    return None


def normalize_state(raw, smap):
    if isinstance(raw, dict):
        raw = raw.get("state") or raw.get("status") or ""
    s = str(raw or "").strip().lower().replace("-", "_").replace(" ", "_")
    if not s:
        return "idle"
    for st in STATE_ORDER:
        if s in smap.get(st, []):
            return st
    for st in STATE_ORDER:
        if any(term in s for term in smap.get(st, [])):
            return st
    return "idle"


def normalize_chat(c, wcfg):
    cid = first(c, ID_KEYS)
    raw_state = first(c, wcfg["state_fields"])
    state = normalize_state(raw_state, wcfg["state_map"])
    if c.get("needsInput") or c.get("awaitingInput") or c.get("pendingApproval"):
        state = "needs_input"
    return {
        "key": f"wb:{cid}",
        "source": "WB",
        "badges": ["Workbench"],
        "where": "Workbench",
        "title": str(first(c, ("title", "name", "label", "summary")) or f"chat {cid}"),
        "state": state,
        "message": str(first(c, ("statusMessage", "lastMessagePreview", "preview", "message")) or ""),
        "detail": f"chat {cid} · raw state: {raw_state}",
        "changed": 0,
    }


class WorkbenchPoller(threading.Thread):
    def __init__(self, cfg):
        super().__init__(daemon=True)
        self.wcfg = cfg["workbench"]
        self.client = McpHttpClient(self.wcfg)
        self.lock = threading.Lock()
        self._items = []
        self.seen = {}
        self.dismissed = set()
        self.first = True
        self.status = "starting…"

    def fetch(self):
        res = self.client.call_tool(self.wcfg["tool_name"], self.wcfg.get("tool_arguments") or {})
        obj = tool_result_to_obj(res)
        chats = find_chat_list(obj)
        if chats is None:
            raise RuntimeError("no chat list in response (run --probe-workbench)")
        return [normalize_chat(c, self.wcfg) for c in chats], obj

    def run(self):
        while True:
            try:
                chats, _ = self.fetch()
                self._update(chats)
                self.status = f"ok · {len(chats)} chats"
            except Exception as e:
                self.status = f"error: {e}"[:110]
            time.sleep(max(2, self.wcfg.get("poll_seconds", 10)))

    def _update(self, chats):
        now = time.time()
        out = []
        with self.lock:
            for c in chats:
                key = c["key"]
                prev = self.seen.get(key)
                if prev is None:
                    # Chats already finished when the pet started stay hidden.
                    self.seen[key] = {"state": c["state"], "changed": 0 if self.first else now}
                elif prev["state"] != c["state"]:
                    prev["state"], prev["changed"] = c["state"], now
                    self.dismissed.discard(key)
                c["changed"] = self.seen[key]["changed"]
                if key in self.dismissed:
                    continue
                if c["state"] in ("working", "needs_input", "error") or (c["state"] == "done" and c["changed"]):
                    out.append(c)
            self.first = False
            self._items = out[: self.wcfg.get("max_chats", 8)]

    def items(self):
        with self.lock:
            return [dict(i) for i in self._items]

    def dismiss(self, key):
        with self.lock:
            self.dismissed.add(key)
            self._items = [i for i in self._items if i["key"] != key]


