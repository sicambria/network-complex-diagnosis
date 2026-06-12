import os
import time

import pytest

from tests.server_helpers import init_netdiag_server, shutdown_netdiag_server

PLAYWRIGHT = None
try:
    from playwright.sync_api import sync_playwright
    PLAYWRIGHT = True
except ImportError:
    PLAYWRIGHT = False

HTTPX = None
try:
    import httpx
    HTTPX = True
except ImportError:
    HTTPX = False

MONITOR_DURATION_S = int(os.environ.get("NETDIAG_MONITOR_DURATION", "60"))


class TestLiveMonitorBrowser:
    @pytest.mark.skipif(not PLAYWRIGHT, reason="playwright not installed")
    def test_live_monitor_no_page_crash(self):
        srv = init_netdiag_server()
        test_failed = False
        try:
            with sync_playwright() as pw:
                browser = pw.chromium.launch(
                    headless=True,
                    args=[
                        "--disable-dev-shm-usage",
                        "--disable-gpu",
                        "--no-sandbox",
                        "--disable-extensions",
                        "--mute-audio",
                    ],
                )
                context = browser.new_context()
                page = context.new_page()

                console_msgs = []
                page.on("console", lambda msg: console_msgs.append({
                    "type": msg.type,
                    "text": msg.text,
                    "timestamp": time.time(),
                }))

                page_errors = []
                page.on("pageerror", lambda err: page_errors.append(str(err)))

                crash_detected = [False]
                page.on("crash", lambda: crash_detected.__setitem__(0, True))

                page.goto(f"{srv['base_url']}/", timeout=10000)

                page.wait_for_selector("button[data-tab=\"monitor\"]", timeout=5000)
                page.click("button[data-tab=\"monitor\"]")

                page.wait_for_selector("#live-toggle-btn", timeout=5000)
                page.click("#live-toggle-btn")

                page.wait_for_selector("#live-container[style*=\"block\"]", timeout=5000)

                start = time.time()
                deadline = start + MONITOR_DURATION_S
                last_poll_sig = None
                last_poll_lat = None
                while time.time() < deadline:
                    if crash_detected[0]:
                        break
                    if page_errors:
                        break
                    try:
                        sig_el = page.query_selector("#live-sig-val")
                        lat_el = page.query_selector("#live-latency")
                        if sig_el:
                            txt = sig_el.text_content()
                            if txt and txt != "--":
                                last_poll_sig = txt
                        if lat_el:
                            txt = lat_el.text_content()
                            if txt and txt != "--":
                                last_poll_lat = txt
                    except Exception:
                        pass
                    time.sleep(0.5)

                elapsed = time.time() - start

                assert not crash_detected[0], f"Page crash detected after {elapsed:.1f}s"

                error_msgs = [m for m in console_msgs if m["type"] == "error"]
                poll_ok_msgs = [m for m in console_msgs if "poll ok" in m["text"]]

                acceptable_errors = []
                for m in error_msgs:
                    if "favicon.ico" in m["text"]:
                        continue
                    if "WebSocket" in m["text"]:
                        continue
                    acceptable_errors.append(m)

                assert len(acceptable_errors) == 0, (
                    f"Console errors: {[m['text'] for m in acceptable_errors]}"
                )

                assert len(poll_ok_msgs) > 0, (
                    f"No 'poll ok' console messages found in {elapsed:.1f}s "
                    f"(got {len(console_msgs)} total msgs)"
                )

                assert last_poll_sig is not None or last_poll_lat is not None, (
                    "Live monitor never updated signal or latency values"
                )

                context.close()
                browser.close()
        except Exception:
            test_failed = True
            raise
        finally:
            result = shutdown_netdiag_server(srv, test_failed=test_failed)
            assert not result["alive"], "Server should be dead after kill"


class TestMonitorServerStress:
    @pytest.mark.skipif(not HTTPX, reason="httpx not installed")
    def test_rapid_poll_monitor(self):
        srv = init_netdiag_server()
        test_failed = False
        try:
            responses = []
            errors = []
            with httpx.Client(timeout=httpx.Timeout(10.0)) as client:
                for i in range(100):
                    try:
                        t0 = time.monotonic()
                        r = client.get(f"{srv['base_url']}/api/monitor")
                        elapsed_ms = (time.monotonic() - t0) * 1000
                        responses.append({
                            "status": r.status_code,
                            "elapsed_ms": round(elapsed_ms, 1),
                            "ok": r.status_code == 200,
                        })
                    except Exception as e:
                        errors.append(str(e))
                        break

            assert len(errors) == 0, f"HTTP errors during stress test: {errors}"
            assert len(responses) == 100, f"Expected 100 responses, got {len(responses)}"

            ok_count = sum(1 for r in responses if r["ok"])
            assert ok_count == 100, f"Only {ok_count}/100 responses were 200 OK"

            times = [r["elapsed_ms"] for r in responses]
            avg = sum(times) / len(times)
            sorted_times = sorted(times)
            p50 = sorted_times[len(sorted_times) // 2]
            p95 = sorted_times[int(len(sorted_times) * 0.95)]

            assert p50 < 5000, f"Median latency {p50:.0f}ms > 5000ms (server struggles)"
            assert p95 < 15000, f"P95 latency {p95:.0f}ms > 15000ms (outliers too high)"

        except Exception:
            test_failed = True
            raise
        finally:
            result = shutdown_netdiag_server(srv, test_failed=test_failed)
            assert not result["alive"], "Server should be dead after kill"
