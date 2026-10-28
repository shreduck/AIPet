"""Copy to ~/.aipet/addins/token-guard-custom/ (the built-in token-guard ID is reserved)."""
import json
import sys

message = json.loads(sys.stdin.buffer.read().decode('utf-8'))
sys.path.insert(0, message['context']['sdk_dir'])
from aipet_addin import AIPet
from aipet_token_guard import on_event

api = AIPet(message['context'])
api.status('working', 'Checking hook data for possible credentials')
on_event(message['event'], message['context'])
# AIPet also reports completion automatically, preserving outstanding attention.
