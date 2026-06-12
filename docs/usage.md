# NetDiag — CLI Reference

## Usage

```
python3 netdiag.py [OPTIONS]
```

## Options

### Ping Configuration

`--hosts HOST [HOST ...]`
: Ping targets (default: `1.1.1.1`, `8.8.8.8`, `9.9.9.9`, `google.com`)

`--count N`
: Number of ping probes per target (default: `20`)

`--interval SEC`
: Seconds between consecutive pings (default: `0.5`)

`--timeout SEC`
: Per-ping timeout in seconds (default: `2`)

`--ipv4`
: Force IPv4 for all pings

`--ipv6`
: Force IPv6 for all pings

### Probe Selection

`--dns-count N`
: Number of DNS resolution queries per host (default: `10`)

`--tcp-count N`
: TCP connection attempts per target (default: `10`)

`--no-speedtest`
: Skip Ookla Speedtest

`--no-trace`
: Skip MTR/traceroute route inspection

`--no-iperf`
: Skip iPerf3 throughput test

`--no-bufferbloat`
: Skip bufferbloat detection

`--quiet`
: Suppress per-ping progress output. Only prints summary and diagnosis.

### Output

`--outdir DIR`
: Output directory for files (default: `internet_diagnostics/`)
: Created files: `diagnostics.json`, `ping_samples.csv`, `ping_summary.csv`, `report.txt`

`--history-dir DIR`
: Directory for session history (default: `~/.netdiag/`)

### Server Mode

`--gui`
: Start web UI server at `http://localhost:8080`. Requires `pip install fastapi uvicorn`.

`--daemon`
: Continuous monitoring. Runs full diagnostic every 10 minutes + serves web UI.

`--port PORT`
: Web server port (default: `8080`)

### Connection Tests

`--download-test`
: Download 100 small images concurrently to measure throughput (Mbps)

`--connection-test`
: HTTP latency test + path MTU probe

### Other

`--help`
: Show help message and exit

## Exit Codes

| Code | Meaning |
|------|---------|
| 0 | Success |
| 1 | Runtime error (e.g. interrupted, server failure) |
| 2 | Invalid arguments |

## Environment

- `PYTHONUNBUFFERED=1` — recommended for daemon mode

## Examples

```bash
# Minimal smoke test (5 seconds)
python3 netdiag.py --count 3 --interval 0.2 --no-speedtest --no-trace --no-iperf --no-bufferbloat

# Standard diagnostic
python3 netdiag.py

# Long-duration stability test (2 minutes)
python3 netdiag.py --count 120 --interval 1 --no-speedtest

# Headless mode (quiet, skip slow probes)
python3 netdiag.py --quiet --no-speedtest --no-iperf --no-trace --no-bufferbloat

# Custom hosts, high ping count
python3 netdiag.py --hosts 1.1.1.1 8.8.8.8 --count 50 --interval 0.2 --dns-count 5

# Web UI on a specific port
python3 netdiag.py --gui --port 3000
```
