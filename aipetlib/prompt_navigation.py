"""Presentation-only selection of waiting prompts; never settles a request."""


class PromptNavigation:
    def __init__(self):
        self.selected = {}

    def advance(self, item, step=1):
        requests = item.get("requests") or []
        if item.get("state") != "needs_input" or len(requests) < 2:
            return False
        current = (item.get("request") or {}).get("id")
        index = next((i for i, request in enumerate(requests) if request.get("id") == current), 0)
        target = requests[(index + step) % len(requests)].get("id")
        if not target or target == current:
            return False
        self.selected[item["key"]] = target
        return True

    def project(self, items):
        active = {item["key"] for item in items}
        self.selected = {key: value for key, value in self.selected.items() if key in active}
        result = []
        for item in items:
            key = item["key"]
            selected = self.selected.get(key)
            request = next((r for r in item.get("requests", []) if r.get("id") == selected), None)
            if selected and request and item.get("state") == "needs_input":
                item = dict(item, request=request, wait_agent=request.get("agent", ""))
            else:
                self.selected.pop(key, None)
            result.append(item)
        return result
