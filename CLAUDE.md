# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

See @AGENTS.md for the full project guide: architecture of `netdiag.py` (single ~3340-line file), code conventions, commands, workflow rules (server restart / frontend regeneration procedures), and no-go rules. Follow it strictly — especially:

- CLI core is stdlib-only (Python 3.12+); fastapi/uvicorn are optional and only for GUI mode. Never add other pip dependencies.
- No type hints, no docstrings; functions return plain dicts.
- **`diagnose()` is the single source of truth for severity.** The web UI renders its output (`facts`/`assumption`/`confidence`/`fix`) verbatim — never recompute severity in JavaScript (that caused the "red ✗ + No specific fix needed" contradictions). See AGENTS.md "Single source of truth".
- **Never report ICMP ping loss as packet loss when TCP/HTTP to the same host works** — `reconcile_icmp()` handles this (1.1.1.1/8.8.8.8/9.9.9.9 rate-limit ICMP). Same rule for MTR: mid-hop loss that clears by the destination is not real loss.
- Every probe needs Linux/macOS/Windows branches with stdlib fallbacks (see the Graceful Degradation table in AGENTS.md).
- Verify changes end-to-end before reporting done (`make lint`, `make test`, restart the GUI server after server/frontend edits).
- Setup must work first time from a clean checkout. System Python is PEP-668 externally-managed, so GUI/test deps (`fastapi`, `uvicorn`, `httpx`, `pytest`) live in `.venv` — `make gui`/`daemon`/`install-gui` use it. Prove it with `rm -rf .venv` before claiming done.
- Never blind-kill a port (`kill $(lsof -ti:8080)` can kill an unrelated dev server). Scope kills to netdiag: `pkill -f "netdiag.py.*--gui"`. netdiag refuses a busy port with a clear `--port` hint instead of clobbering it.
- Capture non-obvious operational lessons (foot-guns, environment gotchas, setup fixes) into AGENTS.md and CLAUDE.md as part of finishing the task — so they do not recur.

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

# Intermittent-issue reproduction + ISP evidence report (CLI)
python3 netdiag.py --wellknown-test --isp-report   # ~2.5 min: 100 well-known sites + print ISP report
# isp_report.txt is ALWAYS written to the outdir; download via GUI Reports tab ("ISP report") or
# /api/export/<session.json>?format=isp
```

Tests use pytest markers `REQ001`–`REQ028` and `NFR001`–`NFR007` (registered in `tests/conftest.py`) mapping 1:1 to `docs/requirements.md`. When changing behavior covered by a requirement, run its marker-tagged test. E2E tests start a real GUI server on a random port via `tests/server_helpers.py`.
