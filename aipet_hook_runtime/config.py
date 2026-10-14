"""Provider identity and event/approval constants for one hook process."""
import sys

AGENT = "codex" if "--codex" in sys.argv[1:] else "claude"


WORKING_EVENTS = {"UserPromptSubmit", "PreToolUse", "PostToolUse", "SubagentStart", "PostToolUseFailure", "PermissionDenied"}


TOOL_DONE_EVENTS = {"PostToolUse", "PostToolUseFailure", "PermissionDenied"}


STOP_FAILURES = {"rate_limit": "Usage limit reached", "overloaded": "Claude is overloaded - try again shortly",
                 "billing_error": "Billing problem: usage is off for this account",
                 "authentication_failed": "Signed out: log in again", "oauth_org_not_allowed": "Organisation not allowed",
                 "account_on_hold": "Account on hold", "invalid_request": "The request was rejected",
                 "model_not_found": "Model not available", "server_error": "API server error",
                 "max_output_tokens": "Reply too long: hit the output limit",
                 "cloud_credential_error": "Cloud credentials could not be loaded"}


ACTIONABLE_NOTIFICATIONS = {"permission_prompt", "elicitation_dialog", "elicitation_url_dialog", "agent_needs_input",
                            "quota_auto_resume_stale"}


QUESTION_TOOL = "AskUserQuestion"


CODEX_QUESTION_TOOLS = {"request_user_input_async", "request_user_input"}


AUTO_APPROVE_FILE = "auto-approve.json"


DEFAULT_BLACKLIST = [".*"]  # a config without a blacklist asks for everything


MAX_WAIT = 1800.0  # the pet's slider: 0 (no limit) to 30 minutes


STOP_SIGNALS = ("SIGTERM", "SIGHUP", "SIGINT", "SIGBREAK")


CODEX_RAW_LOG = False  # switched off for releases; set True to collect Codex test logs
