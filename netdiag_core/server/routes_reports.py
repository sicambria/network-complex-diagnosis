"""Report/history/export routes: list reports, fetch sessions, export json/csv/html/isp."""

import csv
import json
from datetime import datetime, timezone

from netdiag_core import config
from netdiag_core import reporting


def register(app, state):
    from fastapi import Response
    from fastapi.responses import JSONResponse

    @app.get("/api/reports")
    def api_reports(response_class=JSONResponse):
        files = []
        if state.report_dir.is_dir():
            for f in sorted(state.report_dir.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
                files.append({
                    "name": f.name,
                    "size": f.stat().st_size,
                    "mtime": datetime.fromtimestamp(f.stat().st_mtime, tz=timezone.utc).isoformat()
                })
        return JSONResponse(content={"reports": files, "dir": str(state.report_dir)})

    @app.get("/api/report/{name}")
    def api_report(name: str, response_class=Response):
        fpath = state.report_dir / name
        if not fpath.exists() or not fpath.is_file():
            return JSONResponse(content={"error": "Not found"}, status_code=404)
        media = "text/plain"
        if name.endswith(".json"):
            media = "application/json"
        elif name.endswith(".csv"):
            media = "text/csv"
        return Response(content=fpath.read_bytes(), media_type=media)

    @app.get("/api/history")
    def api_history(response_class=JSONResponse):
        sessions = config.load_history("~/.netdiag")
        for s in sessions:
            s.pop("raw", None)
            s.pop("stdout", None)
            s.pop("stderr", None)
            for key in list(s.keys()):
                if isinstance(s.get(key), list):
                    pass
                elif isinstance(s.get(key), dict) and "samples" in (s.get(key) or {}):
                    s[key].pop("samples", None)
        return JSONResponse(content={"sessions": sessions})

    @app.get("/api/session/{file}")
    def api_session(file: str, response_class=JSONResponse):
        d = config.ensure_history_dir("~/.netdiag")
        fpath = d / file
        if not fpath.exists():
            return JSONResponse(content={"error": "Not found"}, status_code=404)
        try:
            data = json.loads(fpath.read_text(encoding="utf-8"))
            return JSONResponse(content=data)
        except:
            return JSONResponse(content={"error": "Parse error"}, status_code=500)

    @app.get("/api/export/{file}")
    def api_export(file: str, format: str = "json", response_class=Response):
        d = config.ensure_history_dir("~/.netdiag")
        fpath = d / file
        if not fpath.exists():
            return Response(content="Not found", status_code=404)
        try:
            data = json.loads(fpath.read_text(encoding="utf-8"))
        except:
            return Response(content="Parse error", status_code=500)

        if format == "json":
            return Response(content=json.dumps(data, indent=2, ensure_ascii=False),
                            media_type="application/json",
                            headers={"Content-Disposition": f"attachment; filename={file}"})

        if format == "csv":
            rows = reporting.flatten_ping(data)
            if rows:
                import io
                buf = io.StringIO()
                fieldnames = sorted({k for row in rows for k in row})
                w = csv.DictWriter(buf, fieldnames=fieldnames)
                w.writeheader()
                w.writerows(rows)
                return Response(content=buf.getvalue(), media_type="text/csv",
                                headers={"Content-Disposition": f"attachment; filename={file.replace('.json','.csv')}"})
            return Response(content="No ping data", status_code=404)

        if format == "isp":
            text = reporting.build_isp_report(data)
            return Response(content=text, media_type="text/plain; charset=utf-8",
                            headers={"Content-Disposition":
                                     f"attachment; filename={file.replace('.json','_ISP_report.txt')}"})

        if format == "html":
            import html as _h
            esc = _h.escape
            html = "<!DOCTYPE html><html><head><meta charset=utf-8><title>NetDiag Report</title>"
            html += "<style>body{font:14px system-ui;max-width:820px;margin:40px auto;padding:20px;background:#0f172a;color:#e2e8f0;line-height:1.6}"
            html += "h1{color:#38bdf8}h2{color:#e2e8f0;margin-top:24px;border-bottom:1px solid #334155;padding-bottom:6px}"
            html += ".card{background:#1e293b;border:1px solid #334155;border-radius:8px;padding:16px;margin:10px 0;border-left:3px solid #334155}"
            html += ".card.bad{border-left-color:#ef4444}.card.warning{border-left-color:#eab308}.card.info{border-left-color:#64748b}.card.clean{border-left-color:#22c55e}"
            html += ".bad{color:#ef4444}.warning{color:#eab308}.clean{color:#22c55e}.info{color:#94a3b8}"
            html += ".lbl{font-size:11px;text-transform:uppercase;letter-spacing:.05em;font-weight:700;margin-top:10px}"
            html += ".facts .lbl{color:#22c55e}.assume .lbl{color:#38bdf8}.fix .lbl{color:#f97316}"
            html += "ul{margin:4px 0}.conf{font-size:11px;color:#94a3b8} pre{background:#0f172a;padding:12px;border-radius:4px;overflow-x:auto}</style></head><body>"
            html += f"<h1>NetDiag Report</h1><p>{esc(str(data.get('timestamp','')))} | {esc(str(data.get('platform','')))} | Score: {esc(str(data.get('health_score','?')))}/100</p>"
            html += "<p style='color:#94a3b8'>Each finding separates <b>measured facts</b> from <b>interpretation</b>. ICMP ping loss to public resolvers that rate-limit ping is excluded from real-loss findings.</p>"
            html += "<h2>Findings</h2>"
            ranked = sorted(data.get("diagnosis", []), key=lambda d: {"bad":0,"warning":1,"info":2,"clean":3}.get(d.get("severity"),2))
            for d in ranked:
                sev = d.get("severity", "info")
                conf = f"<span class='conf'> &middot; confidence: {esc(str(d['confidence']))}</span>" if d.get("confidence") else ""
                html += f"<div class='card {sev}'><strong class='{sev}'>[{esc(str(d.get('layer','')))}] {esc(str(d.get('title','')))}</strong>{conf}"
                if d.get("detail"):
                    html += f"<br>{esc(str(d['detail']))}"
                if d.get("facts"):
                    html += "<div class='lbl facts'>Measured facts</div><ul>" + "".join(f"<li>{esc(str(f))}</li>" for f in d["facts"]) + "</ul>"
                if d.get("assumption"):
                    html += f"<div class='lbl assume'>Interpretation</div><div>{esc(str(d['assumption']))}</div>"
                if d.get("fix"):
                    html += f"<div class='lbl fix'>What to do</div><div>{esc(str(d['fix']))}</div>"
                html += "</div>"
            html += "<h2>ISP evidence report (copy/paste into a ticket)</h2><pre>" + esc(reporting.build_isp_report(data)) + "</pre>"
            html += "</body></html>"
            return Response(content=html, media_type="text/html",
                            headers={"Content-Disposition": f"attachment; filename={file.replace('.json','.html')}"})

        return Response(content="Unknown format", status_code=400)

    @app.get("/api/results/{file}/json")
    def api_results_json(file: str, response_class=Response):
        d = config.ensure_history_dir("~/.netdiag")
        fpath = d / file
        if not fpath.exists():
            return Response(content="Not found", status_code=404)
        data = json.loads(fpath.read_text(encoding="utf-8"))
        return Response(content=json.dumps(data, indent=2, ensure_ascii=False),
                        media_type="application/json")
