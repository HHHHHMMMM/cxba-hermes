"""Local-only Hermes Gateway harness for the cross-repository Spring E2E test."""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI, WebSocket

from hermes_state import SessionDB
from tui_gateway import server
from tui_gateway.ws import handle_ws


_state_path = Path(os.environ["CXBA_E2E_STATE_DB"])
_db = SessionDB(db_path=_state_path)
server._get_db = lambda: _db
server.resolve_skin = lambda: {}
server._ensure_skin_watcher = lambda: None
server._schedule_agent_build = lambda *_args: None
server._schedule_session_cap_enforcement = lambda: None
server._start_agent_build = lambda *_args: None
server._wait_agent_for_prompt = lambda *_args: None


def _complete_fake_turn(_rid, sid, session, _text, **_kwargs):
    """Stand in only for the model turn; transport, auth and RPC stay real."""
    with session["history_lock"]:
        session["running"] = False
        session["history"].append({"role": "assistant", "content": "synthetic-ok"})
    server._emit("message.end", sid, {"text": "synthetic-ok"})
    return True


server._run_prompt_submit = _complete_fake_turn

app = FastAPI()


@app.websocket("/api/ws")
async def gateway_ws(websocket: WebSocket):
    await handle_ws(websocket)
