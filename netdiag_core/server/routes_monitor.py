"""Live-monitor routes: one-shot signal/latency poll + background sampler control."""

from netdiag_core import runtime as rt
from netdiag_core import monitor
from netdiag_core.probes import netinfo, ping
from netdiag_core.probes import wifi as wifi_probe


def register(app, state):
    from fastapi.responses import JSONResponse

    @app.get("/api/monitor")
    def api_monitor():
        try:
            wifi = None
            if rt.IS_LINUX:
                wifi = wifi_probe._proc_net_wireless_any()
                if not wifi and rt.has_tool("iw"):
                    iface = netinfo.detect_wireless_interface()
                    if iface:
                        info = wifi_probe.wifi_info(iface)
                        if info and info.get("signal_dbm") is not None and info["signal_dbm"] < 0:
                            wifi = info
            elif rt.IS_MACOS:
                iface = netinfo.detect_wireless_interface()
                if iface:
                    info = wifi_probe.wifi_info(iface)
                    if info and info.get("signal_dbm") is not None:
                        wifi = info
            elif rt.IS_WINDOWS:
                iface = netinfo.detect_wireless_interface()
                if iface:
                    info = wifi_probe.wifi_info(iface)
                    if info and info.get("signal_dbm") is not None:
                        wifi = info

            gateway = netinfo.detect_gateway()
            latency = None
            if gateway:
                r = ping.ping_once(gateway, timeout_s=1)
                if r and r.get("ok"):
                    latency = r["rtt_ms"]
                if latency is None:
                    t = ping._tcp_ping(gateway, port=80, timeout_s=1)
                    if t and t.get("ok"):
                        latency = t["rtt_ms"]
            health = 50
            if wifi and wifi.get("signal_dbm") is not None:
                sig = wifi["signal_dbm"]
                sig_score = max(1, min(100, 100 - (max(0, -55 - sig) * 3)))
                health = sig_score
            if latency is not None:
                lat_score = max(1, 100 - max(0, latency - 10) * 2)
                health = (health + lat_score) // 2 if wifi and wifi.get("signal_dbm") is not None else lat_score
            rt.log.info("poll ok sig=%s lat=%s health=%s",
                        wifi.get("signal_dbm") if wifi else None, latency, health)
            return JSONResponse(content={
                "wifi": wifi,
                "gateway_latency_ms": latency,
                "health_score": health,
                "timestamp": rt.now_iso(),
                "advanced": monitor.monitor_snapshot(),
            })
        except Exception as e:
            rt.log.error("poll error: %s", str(e), exc_info=True)
            return JSONResponse(content={"error": str(e)}, status_code=500)

    @app.post("/api/monitor/start")
    def api_monitor_start():
        started = monitor.monitor_start()
        return JSONResponse(content={"status": "ok", "started": started})

    @app.post("/api/monitor/stop")
    def api_monitor_stop():
        stopped = monitor.monitor_stop()
        return JSONResponse(content={"status": "ok", "stopped": stopped})
