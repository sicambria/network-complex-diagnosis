# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

See @AGENTS.md for the full project guide: architecture of `netdiag.py` (single ~3340-line file), code conventions, commands, workflow rules (server restart / frontend regeneration procedures), and no-go rules. Follow it strictly — especially:

- CLI core is stdlib-only (Python 3.12+); fastapi/uvicorn are optional and only for GUI mode. Never add other pip dependencies.
- No type hints, no docstrings; functions return plain dicts.
- Every probe needs Linux/macOS/Windows branches with stdlib fallbacks (see the Graceful Degradation table in AGENTS.md).
- Verify changes end-to-end before reporting done (`make lint`, `make test`, restart the GUI server after server/frontend edits).

## Additional commands not in AGENTS.md

```bash
# Browser/e2e tests (Playwright + httpx; skip automatically if not installed)
make install-e2e             # pip install -r requirements-dev.txt + chromium
make e2e                     # all browser e2e tests (tests/test_e2e_browser.py)
make e2e-browser             # live-monitor browser tests only
make e2e-stress              # monitor server stress tests
make e2e-smoke               # fast pass (NETDIAG_MONITOR_DURATION=15)

# Requirements traceability suite — one test per REQ/NFR in docs/requirements.md
make e2e-req
python3 -m pytest tests/test_e2e_requirements.py -k REQ001 -v   # single requirement
```

Tests use pytest markers `REQ001`–`REQ028` and `NFR001`–`NFR007` (registered in `tests/conftest.py`) mapping 1:1 to `docs/requirements.md`. When changing behavior covered by a requirement, run its marker-tagged test. E2E tests start a real GUI server on a random port via `tests/server_helpers.py`.
