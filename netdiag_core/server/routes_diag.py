"""Core diagnostic routes: index page, run/stop/status, activity, tools, config."""

import threading

from netdiag_core import runtime as rt
from netdiag_core import config
from netdiag_core import cli
from netdiag_core.server import page


def register(app, state):
    from fastapi import Request, Response
    from fastapi.responses import HTMLResponse, JSONResponse

    @app.get("/", response_class=HTMLResponse)
    def index():
        return Response(content=page.assemble_index(), media_type="text/html")

    @app.get("/api/status")
    def api_status(response_class=JSONResponse):
        with state.lock:
            r = dict(state.current_run)
        r.pop("_lock", None)
        if r.get("results"):
            r["results"] = {k: v for k, v in r["results"].items()
                            if k not in ("raw", "stdout", "stderr")}
            for key in ["gateway_ping", "internet_ping"]:
                if isinstance(r["results"].get(key), list):
                    for item in r["results"][key]:
                        item.pop("samples", None)
                elif isinstance(r["results"].get(key), dict):
                    r["results"][key].pop("samples", None)
        return JSONResponse(content=r)

    @app.get("/api/activity")
    def api_activity():
        return JSONResponse(content={"activity": rt.get_activity_log(50)})

    @app.get("/api/tools")
    def api_tools():
        return JSONResponse(content=rt.check_tools())

    @app.get("/api/config")
    def api_config_get():
        return JSONResponse(content=config.load_config())

    @app.post("/api/config")
    async def api_config_post(request: Request):
        try:
            body = await request.json()
        except Exception:
            body = {}
        if not isinstance(body, dict):
            return JSONResponse(content={"error": "expected a JSON object"}, status_code=400)
        cfg = config.save_config(body)
        return JSONResponse(content=cfg)

    @app.post("/api/run")
    async def api_run(request: Request):
        body = {}
        try:
            body = await request.json()
        except Exception:
            pass

        with state.lock:
            if state.current_run["status"] == "running":
                return JSONResponse(content={"status": "error", "message": "Diagnostic already running"})
            state.current_run["status"] = "running"
            state.current_run["progress"] = {}
            state.current_run["results"] = None
            state.current_run["error"] = None
            # Reset the Stop flag atomically with the run reset so a stale stop from
            # a prior run can't cancel this one before it starts.
            state.stop_event.clear()

        parser = cli.build_parser()
        args = parser.parse_args([])
        if not rt.IS_LINUX:
            args.no_bufferbloat = True

        args.no_speedtest = not body.get("speedtest", False)
        args.no_trace = not body.get("trace", True)
        args.no_bufferbloat = not body.get("bufferbloat", rt.IS_LINUX)
        args.no_iperf = not body.get("iperf3", False)
        args.download_test = body.get("download_test", False)
        args.connection_test = body.get("connection_test", False)
        args.reliability_test = body.get("reliability_test", False)
        args.wellknown_test = body.get("wellknown_test", False)
        if body.get("hosts"):
            hs = body["hosts"]
            args.hosts = [h.strip() for h in hs.split(",")] if isinstance(hs, str) else list(hs)
        if body.get("count"):
            args.count = int(body["count"])

        thread = threading.Thread(target=state.run_diag, args=(args,), daemon=True)
        thread.start()
        return JSONResponse(content={"status": "ok", "session_id": rt.now_iso().replace(":", "")})

    @app.post("/api/stop")
    def api_stop():
        # Cooperative cancel: set the flag the running diagnostic polls at probe
        # boundaries and inside its progress callback. The worker finishes the
        # current in-flight probe, then unwinds and saves a partial report.
        with state.lock:
            was_running = state.current_run["status"] == "running"
        state.stop_event.set()
        return JSONResponse(content={"status": "ok", "stopping": was_running})
