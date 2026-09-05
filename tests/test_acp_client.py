from __future__ import annotations

import json
import sys

from harness_framework.acp_client import ACPClient


FAKE_AGENT = r'''
import json, sys

def send(value):
    sys.stdout.write(json.dumps(value) + "\n")
    sys.stdout.flush()

for line in sys.stdin:
    message = json.loads(line)
    method = message.get("method")
    if method == "initialize":
        send({"jsonrpc":"2.0","id":message["id"],"result":{
            "protocolVersion":1,"agentCapabilities":{},
            "agentInfo":{"name":"fake-acp","version":"1"}}})
    elif method == "session/new":
        send({"jsonrpc":"2.0","id":message["id"],"result":{"sessionId":"session-1"}})
    elif method == "session/prompt":
        sid = message["params"]["sessionId"]
        send({"jsonrpc":"2.0","method":"session/update","params":{
            "sessionId":sid,"update":{"sessionUpdate":"agent_message_chunk",
            "content":{"type":"text","text":"finished"}}}})
        send({"jsonrpc":"2.0","id":99,"method":"session/request_permission",
            "params":{"sessionId":sid,"options":[
                {"optionId":"yes","name":"Allow","kind":"allow_once"}]}})
    elif message.get("id") == 99:
        assert message["result"]["outcome"]["optionId"] == "yes"
        send({"jsonrpc":"2.0","id":3,"result":{"stopReason":"end_turn"}})
'''


def test_stdio_acp_lifecycle_and_permission_handling(tmp_path):
    updates = []
    client = ACPClient(
        [sys.executable, "-u", "-c", FAKE_AGENT],
        cwd=str(tmp_path), update_handler=updates.append,
    )
    try:
        initialized = client.initialize(timeout=3)
        assert initialized["protocolVersion"] == 1
        assert client.new_session(timeout=3) == "session-1"
        result = client.prompt("do the task", timeout=3)
    finally:
        client.close()

    assert result.stop_reason == "end_turn"
    assert result.session_id == "session-1"
    assert updates[0]["update"]["sessionUpdate"] == "agent_message_chunk"


def test_deny_permission_policy_returns_cancelled():
    client = ACPClient(["unused"], cwd="/tmp", permission_policy="deny")
    assert client._permission_response({"options": [{"optionId": "yes"}]}) == {
        "outcome": {"outcome": "cancelled"}
    }
