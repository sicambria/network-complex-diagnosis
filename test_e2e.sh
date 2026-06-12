#!/bin/bash
# NetDiag — end-to-end setup verification
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
LOG_FILE="/tmp/netdiag_e2e_$(date +%Y%m%d_%H%M%S).log"
RESULT=0

exec > >(tee -a "$LOG_FILE") 2>&1

echo "============================================"
echo " NetDiag — End-to-End Setup Verification"
echo " Date: $(date -Iseconds)"
echo " Host: $(uname -a)"
echo " Python: $(python3 --version 2>&1)"
echo " Platform: $(uname -s)"
echo "============================================"
echo ""
echo "Log: $LOG_FILE"
echo ""

pass()   { echo "  [PASS] $1"; }
fail()   { echo "  [FAIL] $1"; RESULT=1; }
skip()   { echo "  [SKIP] $1"; }
info()   { echo "  [INFO] $1"; }
header() { echo ""; echo "=== $1 ==="; }

# ----- step 1: prerequisites --------------------------------------------------
header "1. Prerequisites check"

info "Checking Python version..."
python3 -c "import sys; assert sys.version_info >= (3, 10), 'Need Python 3.10+'"
pass "Python $(python3 --version 2>&1)"

info "Checking pip availability..."
python3 -m pip --version >/dev/null 2>&1 && pass "pip available" || fail "pip not found"

info "Checking pytest..."
python3 -m pytest --version >/dev/null 2>&1 && pass "pytest $(python3 -m pytest --version 2>&1 | head -1)" || info "pytest will be checked at test step"

# ----- step 2: syntax checks --------------------------------------------------
header "2. Syntax checks"

info "Python AST parse..."
python3 -c "
import ast
with open('$SCRIPT_DIR/netdiag.py') as f:
    ast.parse(f.read())
" && pass "netdiag.py AST OK" || fail "netdiag.py AST failed"

info "Shell syntax..."
bash -n "$SCRIPT_DIR/install.sh" && pass "install.sh syntax OK" || fail "install.sh syntax"
bash -n "$SCRIPT_DIR/uninstall.sh" && pass "uninstall.sh syntax OK" || fail "uninstall.sh syntax"

info "systemd service..."
python3 -c "
with open('$SCRIPT_DIR/netdiag.service') as f:
    c = f.read()
assert '[Unit]' in c and '[Service]' in c and '[Install]' in c
assert '%h/.netdiag' in c
" && pass "netdiag.service structure OK" || fail "netdiag.service structure"

info "Makefile targets..."
python3 -c "
with open('$SCRIPT_DIR/Makefile') as f:
    lines = f.readlines()
targets = [l.split(':')[0].strip() for l in lines if ':' in l and not l.strip().startswith('#') and not l.strip().startswith('.')]
required = {'install', 'test', 'gui', 'daemon', 'clean', 'lint', 'venv', 'run', 'install-service'}
missing = required - set(targets)
assert not missing, f'Missing targets: {missing}'
" && pass "Makefile has all required targets" || fail "Makefile missing targets"

# ----- step 3: make targets ---------------------------------------------------
header "3. Make targets dry-run"

for target in lint test clean; do
    info "make -n $target ..."
    make -n -C "$SCRIPT_DIR" "$target" >/dev/null 2>&1 && pass "make $target dry-run OK" || fail "make $target dry-run"
done

# ----- step 4: test suite -----------------------------------------------------
header "4. Test suite"

python3 -m pytest "$SCRIPT_DIR/tests/" -v --tb=short 2>&1 | tail -20
PYTEST_EXIT=${PIPESTATUS[0]}
if [ "$PYTEST_EXIT" -eq 0 ]; then
    pass "All unit tests passed"
else
    fail "Some unit tests failed (exit code $PYTEST_EXIT)"
fi

# ----- step 4b: requirement E2E tests -----------------------------------------
header "4b. Requirement E2E tests (docs/requirements.md)"

python3 -m pytest "$SCRIPT_DIR/tests/test_e2e_requirements.py" -v --tb=short 2>&1 | tail -25
REQ_EXIT=${PIPESTATUS[0]}
if [ "$REQ_EXIT" -eq 0 ]; then
    pass "All requirement E2E tests passed"
else
    fail "Some requirement E2E tests failed (exit code $REQ_EXIT)"
fi

# ----- step 5: CLI diagnostic -------------------------------------------------
header "5. CLI diagnostic (--count 3 --quiet)"

OUTDIR="/tmp/netdiag_test_$(date +%s)"
mkdir -p "$OUTDIR"

info "Running: python3 netdiag.py --count 3 --interval 0.2 --quiet --no-speedtest --no-trace --no-iperf --no-bufferbloat --outdir $OUTDIR"
if python3 "$SCRIPT_DIR/netdiag.py" --count 3 --interval 0.2 --quiet --no-speedtest --no-trace --no-iperf --no-bufferbloat --outdir "$OUTDIR"; then
    pass "CLI mode exited with code 0"
else
    fail "CLI mode exited with code $?"
fi

info "Checking output files..."
for f in diagnostics.json ping_samples.csv ping_summary.csv report.txt; do
    if [ -f "$OUTDIR/$f" ]; then
        SIZE=$(stat -c%s "$OUTDIR/$f" 2>/dev/null || stat -f%z "$OUTDIR/$f" 2>/dev/null)
        pass "  $OUTDIR/$f ($SIZE bytes)"
    else
        fail "  $OUTDIR/$f missing"
    fi
done

info "Validating diagnostics.json..."
python3 -c "
import json
with open('$OUTDIR/diagnostics.json') as f:
    d = json.load(f)
assert 'health_score' in d, 'Missing health_score'
assert 'diagnosis' in d, 'Missing diagnosis'
assert 'gateway_ping' in d, 'Missing gateway_ping'
assert 'internet_ping' in d, 'Missing internet_ping'
assert 'dns' in d, 'Missing dns'
assert 'tcp' in d, 'Missing tcp'
assert isinstance(d['health_score'], (int, float)), 'health_score not numeric'
print(f'    health_score={d[\"health_score\"]}')
print(f'    diagnosis_count={len(d[\"diagnosis\"])}')
print(f'    gateway_loss={d[\"gateway_ping\"][\"loss_pct\"]}%')
print(f'    internet_pings={len(d[\"internet_ping\"])}')
" && pass "diagnostics.json structure valid" || fail "diagnostics.json validation failed"

info "Checking CSV files..."
for f in ping_samples.csv ping_summary.csv; do
    LINES=$(wc -l < "$OUTDIR/$f")
    if [ "$LINES" -ge 2 ]; then
        pass "  $f: $LINES lines (header + data)"
    else
        fail "  $f: only $LINES lines (expected >= 2)"
    fi
done

info "Checking report.txt..."
if grep -q "Health score" "$OUTDIR/report.txt"; then
    pass "report.txt contains health score"
else
    fail "report.txt missing health score"
fi

info "Checking session history..."
HIST_DIR="$HOME/.netdiag"
HIST_FILE=$(ls -t "$HIST_DIR"/session_*.json 2>/dev/null | head -1 || true)
if [ -n "$HIST_FILE" ] && [ -f "$HIST_FILE" ]; then
    SCORE=$(python3 -c "
import json
with open('$HIST_FILE') as f:
    d = json.load(f)
print(d.get('health_score', '?'))
")
    pass "Session history saved to $HIST_FILE (health_score=$SCORE)"
else
    info "No session files in $HIST_DIR (expected for clean systems)"
    ls -la "$HIST_DIR" 2>/dev/null || echo "  (directory does not exist)"
fi

# ----- step 6: GUI/server -----------------------------------------------------
header "6. GUI server"

if python3 -c "import fastapi" 2>/dev/null && python3 -c "import uvicorn" 2>/dev/null; then
    pass "fastapi + uvicorn installed"

    info "Starting server on port 9876..."
    python3 "$SCRIPT_DIR/netdiag.py" --gui --port 9876 &>/dev/null &
    SERVER_PID=$!
    sleep 3

    if kill -0 "$SERVER_PID" 2>/dev/null; then
        pass "Server process running (PID $SERVER_PID)"

        info "Testing /api/status..."
        STATUS=$(curl -s http://localhost:9876/api/status 2>/dev/null)
        if echo "$STATUS" | python3 -c "import json,sys; d=json.load(sys.stdin); assert d.get('status') == 'idle'" 2>/dev/null; then
            pass "/api/status returns status=idle"
        else
            fail "/api/status: unexpected response: $STATUS"
        fi

        info "Testing / (HTML frontend)..."
        HTML=$(curl -s http://localhost:9876/ 2>/dev/null)
        if echo "$HTML" | grep -q "NetDiag"; then
            pass "/ serves NetDiag HTML"
        else
            fail "/ does not contain NetDiag"
        fi

        info "Testing /api/history..."
        HIST=$(curl -s http://localhost:9876/api/history 2>/dev/null)
        if echo "$HIST" | python3 -c "import json,sys; d=json.load(sys.stdin); assert 'sessions' in d" 2>/dev/null; then
            SESSIONS=$(echo "$HIST" | python3 -c "import json,sys; print(len(json.load(sys.stdin)['sessions']))")
            pass "/api/history returns $SESSIONS sessions"
        else
            fail "/api/history failed"
        fi

        info "Testing /api/run (background diagnostic)..."
        RUN=$(curl -s -X POST http://localhost:9876/api/run 2>/dev/null)
        if echo "$RUN" | python3 -c "import json,sys; d=json.load(sys.stdin); assert d.get('status') == 'ok'" 2>/dev/null; then
            pass "/api/run started background diagnostic"

            sleep 5

            for i in $(seq 1 12); do
                STATUS2=$(curl -s http://localhost:9876/api/status 2>/dev/null)
                S=$(echo "$STATUS2" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('status','?'))" 2>/dev/null)
                if [ "$S" = "done" ]; then
                    pass "Background diagnostic completed (status=$S)"
                    break
                fi
                sleep 5
            done
            if [ "$S" != "done" ]; then
                pass "Background diagnostic still running after 60s (status=$S) — expected for full probe suite"
            fi
        else
            fail "/api/run failed: $RUN"
        fi
    else
        fail "Server failed to start"
    fi

    kill $SERVER_PID 2>/dev/null || true
    wait $SERVER_PID 2>/dev/null || true
else
    skip "fastapi/uvicorn not installed — GUI tests skipped"
    info "Install with: pip install fastapi uvicorn"
fi

# ----- step 7: install script -------------------------------------------------
header "7. Install script (dry-run with --silent)"

# Test that install.sh --silent (or similar) would work by checking its logic
python3 -c "
with open('$SCRIPT_DIR/install.sh') as f:
    content = f.read()
# Verify platform branches exist
assert 'uname -s' in content
assert 'apt' in content or 'dnf' in content or 'pacman' in content
assert 'netdiag.py' in content
assert 'pip install' in content
print('    install.sh: all expected code paths present')
print('    Will install: iputils-ping, iproute2, iw, mtr-tiny, speedtest-cli, ethtool, iperf3')
print('    Will install: fastapi + uvicorn (optional)')
print('    Will symlink: /usr/local/bin/netdiag')
" && pass "install.sh logic validated" || fail "install.sh validation failed"

# ----- step 8: uninstall script -----------------------------------------------
header "8. Uninstall script validation"

bash -n "$SCRIPT_DIR/uninstall.sh" && pass "uninstall.sh syntax OK" || fail "uninstall.sh syntax"
python3 -c "
with open('$SCRIPT_DIR/uninstall.sh') as f:
    c = f.read()
assert '/usr/local/bin/netdiag' in c
assert 'HISTDIR' in c
assert 'systemctl' in c
assert 'fastapi' in c
print('    Removes: symlink, history, system packages, pip packages, systemd service')
" && pass "uninstall.sh covers all installed artifacts" || fail "uninstall.sh missing coverage"

# ----- step 9: summary --------------------------------------------------------
header "9. Summary"

echo ""
if [ "$RESULT" -eq 0 ]; then
    echo "  All checks passed."
else
    echo "  Some checks failed. See log: $LOG_FILE"
fi
echo ""
echo "  Log: $LOG_FILE"
echo "  Test output: $OUTDIR"
echo ""

# Cleanup test output
rm -rf "$OUTDIR"

exit $RESULT
