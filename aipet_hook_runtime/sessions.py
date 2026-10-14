"""Explicit wiring from runtime owners to the event engine."""
from . import events
from . import config as hook_config
from . import diagnostics as hook_diagnostics
from . import hosts as hook_hosts
from . import io as hook_io
from . import requests as hook_requests
from . import transcripts as hook_transcripts

def _update_session(path, target, event, data, wsl, auto=None):
    api = events.HookAPI(
        ACTIONABLE_NOTIFICATIONS=hook_config.ACTIONABLE_NOTIFICATIONS,
        AGENT=hook_config.AGENT,
        CODEX_QUESTION_TOOLS=hook_config.CODEX_QUESTION_TOOLS,
        QUESTION_TOOL=hook_config.QUESTION_TOOL,
        TOOL_DONE_EVENTS=hook_config.TOOL_DONE_EVENTS,
        WORKING_EVENTS=hook_config.WORKING_EVENTS,
        build_request=hook_requests.build_request,
        codex_questions=hook_requests.codex_questions,
        codex_session_transcript=hook_transcripts.codex_session_transcript,
        codex_thread_name=hook_transcripts.codex_thread_name,
        conversation_title=hook_transcripts.conversation_title,
        cowork_folders=hook_hosts.cowork_folders,
        debug_log=hook_diagnostics.debug_log,
        detect_ide=hook_hosts.detect_ide,
        find_claude_pid=hook_hosts.find_claude_pid,
        find_host_window=hook_hosts.find_host_window,
        is_cowork=hook_hosts.is_cowork,
        is_home_or_root=hook_hosts.is_home_or_root,
        pending_requests=hook_requests.pending_requests,
        permission_message=hook_requests.permission_message,
        read_json=hook_io.read_json,
        request_from_transcript=hook_transcripts.request_from_transcript,
        request_id=hook_requests.request_id,
        stop_failure_kind=hook_requests.stop_failure_kind,
        stop_failure_message=hook_requests.stop_failure_message,
        tool_signature=hook_requests.tool_signature,
        window_alive=hook_hosts.window_alive,
        write_atomic=hook_io.write_atomic,
    )
    return events._update_session(api, path, target, event, data, wsl, auto)
