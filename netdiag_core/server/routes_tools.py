"""Tools-tab routes: list the tool catalog, run a single tool, poll its status."""

import threading

from netdiag_core import runtime as rt
from netdiag_core.server import tools_menu


def register(app, state):
    from fastapi import Request
    from fastapi.responses import JSONResponse

    @app.get("/api/tools/menu")
    def api_tools_menu():
        tlist = []
        for t in tools_menu.TOOLS_MENU:
            entry = {k: t[k] for k in ("id", "name", "layer", "layer_name", "desc", "docs", "params", "presets")}
            # Strip run function for JSON
            tlist.append(entry)
        return JSONResponse(content={"tools": tlist})

    @app.post("/api/tool/run")
    async def api_tool_run(request: Request):
        try:
            body = await request.json()
        except Exception:
            return JSONResponse(content={"error": "Invalid JSON"}, status_code=400)
        tool_id = body.get("tool_id")
        params = body.get("params", {})
        if not isinstance(params, dict):
            params = {}

        tool = None
        for t in tools_menu.TOOLS_MENU:
            if t["id"] == tool_id:
                tool = t
                break
        if not tool:
            return JSONResponse(content={"error": f"Tool '{tool_id}' not found"}, status_code=404)

        with state.lock:
            if state.tools_run_state["running"]:
                return JSONResponse(content={"error": "A tool is already running", "tool_id": state.tools_run_state["tool_id"]}, status_code=409)
            state.tools_run_state["running"] = True
            state.tools_run_state["tool_id"] = tool_id
            state.tools_run_state["result"] = None
            state.tools_run_state["error"] = None

        def _run_tool():
            try:
                result = tool["run"](params)
                with state.lock:
                    state.tools_run_state["result"] = result
                    state.tools_run_state["running"] = False
            except Exception as e:
                rt.log.error("tool %s error: %s", tool_id, str(e), exc_info=True)
                with state.lock:
                    state.tools_run_state["error"] = str(e)
                    state.tools_run_state["running"] = False

        thread = threading.Thread(target=_run_tool, daemon=True)
        thread.start()
        return JSONResponse(content={"status": "ok", "tool_id": tool_id})

    @app.get("/api/tool/status")
    def api_tool_status():
        with state.lock:
            return JSONResponse(content={
                "running": state.tools_run_state["running"],
                "tool_id": state.tools_run_state["tool_id"],
                "result": state.tools_run_state["result"],
                "error": state.tools_run_state["error"],
            })
