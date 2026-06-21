import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

SCRIPT = str(Path(__file__).resolve().parent.parent / "netdiag.py")


def _random_port():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def init_netdiag_server():
    port = _random_port()
    stderr_f = tempfile.NamedTemporaryFile(mode="w+", suffix=".log", prefix="netdiag_stderr_", delete=False)
    proc = subprocess.Popen(
        [sys.executable, SCRIPT, "--gui", "--port", str(port)],
        stderr=stderr_f,
        stdout=subprocess.DEVNULL,
        text=True,
    )
    stderr_f.flush()

    base_url = f"http://127.0.0.1:{port}"
    deadline = time.time() + 8
    last_err = ""
    while time.time() < deadline:
        rc = proc.poll()
        if rc is not None:
            raise RuntimeError(f"Server exited early (rc={rc})")
        try:
            import urllib.request
            res = urllib.request.urlopen(base_url + "/api/status", timeout=1)
            if res.status == 200:
                break
        except Exception as e:
            last_err = str(e)
        time.sleep(0.3)
    else:
        raise RuntimeError(f"Server did not start within 8s. Last error: {last_err}")

    return {
        "port": port,
        "proc": proc,
        "base_url": base_url,
        "stderr_path": stderr_f.name,
        "_stderr_f": stderr_f,
    }


def shutdown_netdiag_server(srv, test_failed=False):
    proc = srv["proc"]
    proc.kill()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        pass

    srv["_stderr_f"].flush()
    srv["_stderr_f"].seek(0)
    stderr_text = srv["_stderr_f"].read()
    srv["_stderr_f"].close()
    os.unlink(srv["stderr_path"])

    alive = proc.poll() is None
    rc = proc.returncode

    if test_failed or alive:
        print(f"\n=== SERVER STDERR (PID={proc.pid}, rc={rc}, alive={alive}) ===")
        print(stderr_text[-3000:] if len(stderr_text) > 3000 else stderr_text)
        print("=== END STDERR ===\n")

    return {"alive": alive, "rc": rc, "stderr": stderr_text}


def find_cached_chromium():
    """Return the path to a cached Playwright chromium executable, or None.

    Lets the browser e2e run on platforms Playwright won't auto-provision a
    browser for (e.g. an unreleased Ubuntu / Python combo) by reusing an
    already-downloaded build via launch(executable_path=...).
    """
    import glob
    roots = []
    if os.environ.get("PLAYWRIGHT_BROWSERS_PATH"):
        roots.append(os.environ["PLAYWRIGHT_BROWSERS_PATH"])
    roots.append(os.path.expanduser("~/.cache/ms-playwright"))           # Linux
    roots.append(os.path.expanduser("~/Library/Caches/ms-playwright"))   # macOS
    patterns = (
        "chromium-*/chrome-linux*/chrome",
        "chromium_headless_shell-*/chrome-headless-shell-linux*/chrome-headless-shell",
        "chromium-*/chrome-mac*/Chromium.app/Contents/MacOS/Chromium",
        "chromium-*/chrome-win*/chrome.exe",
    )
    for root in roots:
        for pat in patterns:
            hits = sorted(glob.glob(os.path.join(root, pat)), reverse=True)
            if hits:
                return hits[0]
    return None


def launch_chromium(pw, args=None):
    """Launch a headless chromium for the browser e2e.

    Tries Playwright's normally-provisioned browser first; if that fails
    (common on too-new OSes Playwright has no build for), falls back to a
    cached browser binary via executable_path. Calls pytest.skip if neither
    a provisioned nor a cached browser is available.
    """
    import pytest
    if args is None:
        args = ["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"]
    try:
        return pw.chromium.launch(headless=True, args=args)
    except Exception as primary_err:
        exe = find_cached_chromium()
        if not exe:
            pytest.skip(f"no usable chromium (Playwright can't provision one here and "
                        f"none cached): {str(primary_err)[:160]}")
        return pw.chromium.launch(headless=True, args=args, executable_path=exe)

    return {"rc": rc, "alive": alive, "stderr": stderr_text}
