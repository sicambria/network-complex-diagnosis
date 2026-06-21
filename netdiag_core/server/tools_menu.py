"""Tools-tab catalog: per-OSI-layer individual probes with presets.

Each entry's "run" closure invokes a probe via its module so tests can patch a
single canonical target. _diag_args_from_kw builds a full_diagnostic args object
from the loose keyword dict the Tools UI submits.
"""

from netdiag_core import runtime as rt
from netdiag_core import config
from netdiag_core import analysis
from netdiag_core import orchestrate
from netdiag_core.constants import DEFAULT_HOSTS, RELIABILITY_TARGETS
from netdiag_core.probes import netinfo, wifi, ping, route, dns_tcp, sockets, throughput, webprobes, reliability


def _diag_args_from_kw(kw):
    cfg = config.load_config()
    class _NA: pass
    a = _NA()
    a.hosts = kw.get("hosts", cfg.get("hosts", list(DEFAULT_HOSTS)))
    if isinstance(a.hosts, str):
        a.hosts = [h.strip() for h in a.hosts.split(",") if h.strip()]
    a.count = int(kw.get("count", cfg.get("ping_count", 20)))
    a.interval = float(kw.get("interval", cfg.get("ping_interval", 0.5)))
    a.timeout = int(kw.get("timeout", cfg.get("ping_timeout", 2)))
    a.dns_count = int(kw.get("dns_count", cfg.get("dns_count", 10)))
    a.tcp_count = int(kw.get("tcp_count", cfg.get("tcp_count", 10)))
    a.quiet = True
    a.no_bufferbloat = not rt.IS_LINUX or not kw.get("bufferbloat", True)
    a.no_trace = not kw.get("trace", True)
    a.no_speedtest = not kw.get("speedtest", False)
    a.no_iperf = not kw.get("iperf3", False)
    a.download_test = kw.get("download_test", False)
    a.connection_test = kw.get("connection_test", False)
    a.reliability_test = kw.get("reliability_test", False)
    a.reliability_targets = kw.get("reliability_targets") or cfg.get("reliability_targets")
    a.reliability_samples = int(kw.get("reliability_samples", cfg.get("reliability_samples", 20)))
    a.reliability_concurrency = int(kw.get("reliability_concurrency", cfg.get("reliability_concurrency", 8)))
    a.reliability_duration = int(kw.get("reliability_duration", cfg.get("reliability_duration", 0)))
    a.wellknown_test = kw.get("wellknown_test", False)
    a.wellknown_duration = int(kw.get("wellknown_duration", cfg.get("wellknown_duration", 150)))
    a.wellknown_concurrency = int(kw.get("wellknown_concurrency", cfg.get("wellknown_concurrency", 12)))
    a.isp_report = kw.get("isp_report", False)
    a.outdir = "internet_diagnostics"
    a.history_dir = cfg.get("history_dir", "~/.netdiag")
    return a


TOOLS_MENU = [
    # Layer 1 - Physical
    {"id": "interface_stats", "name": "Interface Statistics", "layer": 1, "layer_name": "Physical (L1)",
     "desc": "Read RX/TX errors, drops, overruns, carrier changes from the default network interface.",
     "docs": "Command: ip -s link / ifconfig / netstat -e / sysfs (stdlib fallback)",
     "params": [],
     "presets": [{"name": "Default", "values": {}}],
     "run": lambda kw: netinfo.interface_stats(netinfo.get_default_interface())},
    {"id": "ethtool_info", "name": "Ethtool (Link / Duplex / Speed)", "layer": 1, "layer_name": "Physical (L1)",
     "desc": "Check Ethernet link status, negotiated speed, and duplex mode (Linux only, requires ethtool).",
     "docs": "Command: ethtool <iface>  |  Plan B: parsed from interface_stats",
     "params": [],
     "presets": [{"name": "Default", "values": {}}],
     "run": lambda kw: netinfo.ethtool_info(netinfo.get_default_interface())},
    # Layer 2 - Data Link
    {"id": "wifi_info", "name": "WiFi Info & Survey", "layer": 2, "layer_name": "Data Link (L2)",
     "desc": "Detect wireless interface, signal strength (dBm), noise, channel utilization, and link quality.",
     "docs": "Command: iw dev / iw survey dump / airport / netsh wlan / procfs (stdlib fallback)",
     "params": [],
     "presets": [{"name": "Default", "values": {}}],
     "run": lambda kw: wifi.wifi_info(netinfo.get_default_interface())},
    # Layer 3 - Network
    {"id": "ping_test", "name": "Ping (ICMP Echo)", "layer": 3, "layer_name": "Network (L3)",
     "desc": "Send ICMP echo requests and measure RTT, packet loss, jitter, and latency distribution (p95).",
     "docs": "Command: ping -c <count> -W <timeout> <host>  |  Plan B: TCP connect RTT via socket",
     "params": [
         {"key": "host", "label": "Target host", "type": "text", "default": "1.1.1.1"},
         {"key": "count", "label": "Ping count", "type": "number", "default": 10, "min": 1, "max": 200},
         {"key": "interval", "label": "Interval (s)", "type": "number", "default": 0.5, "min": 0.1, "max": 10, "step": 0.1},
         {"key": "timeout", "label": "Timeout (s)", "type": "number", "default": 2, "min": 1, "max": 10},
     ],
     "presets": [
         {"name": "Quick (3 pings)", "values": {"host": "1.1.1.1", "count": 3, "interval": 0.2, "timeout": 2}},
         {"name": "Standard (20 pings)", "values": {"host": "1.1.1.1", "count": 20, "interval": 0.5, "timeout": 2}},
         {"name": "Stress (100 pings)", "values": {"host": "1.1.1.1", "count": 100, "interval": 0.1, "timeout": 3}},
     ],
     "run": lambda kw: ping.ping_burst(kw.get("host", "1.1.1.1"), int(kw.get("count", 10)), float(kw.get("interval", 0.5)), timeout_s=int(kw.get("timeout", 2)), label="tool_ping")},
    {"id": "mtr_test", "name": "MTR / Traceroute", "layer": 3, "layer_name": "Network (L3)",
     "desc": "Trace the route to a target with per-hop loss and latency. Falls back to traceroute or native ping TTL sweep.",
     "docs": "Command: mtr -r -c <count> <host>  |  Plan B: traceroute -n  |  Plan C: ping -t TTL sweep",
     "params": [
         {"key": "host", "label": "Target host", "type": "text", "default": "1.1.1.1"},
         {"key": "count", "label": "Probes per hop", "type": "number", "default": 10, "min": 1, "max": 100},
     ],
     "presets": [
         {"name": "Quick (5 probes)", "values": {"host": "1.1.1.1", "count": 5}},
         {"name": "Standard (10 probes)", "values": {"host": "1.1.1.1", "count": 10}},
         {"name": "Deep (30 probes)", "values": {"host": "1.1.1.1", "count": 30}},
     ],
     "run": lambda kw: route.mtr_test(kw.get("host", "1.1.1.1"), count=int(kw.get("count", 10)))},
    {"id": "mtu_probe", "name": "Path MTU Discovery", "layer": 3, "layer_name": "Network (L3)",
     "desc": "Probe the maximum transmission unit along the path using ping with incrementing packet sizes.",
     "docs": "Command: ping -c 1 -M do -s <size> <host> (Linux) / ping -c 1 -D -s <size> <host> (macOS)",
     "params": [
         {"key": "host", "label": "Target host", "type": "text", "default": "1.1.1.1"},
         {"key": "max_size", "label": "Max packet size (bytes)", "type": "number", "default": 1500, "min": 68, "max": 9000},
     ],
     "presets": [
         {"name": "Standard (1500)", "values": {"host": "1.1.1.1", "max_size": 1500}},
         {"name": "Jumbo frames (9000)", "values": {"host": "1.1.1.1", "max_size": 9000}},
     ],
     "run": lambda kw: route.mtu_probe(kw.get("host", "1.1.1.1"), max_size=int(kw.get("max_size", 1500)))},
    {"id": "detect_gateway", "name": "Gateway Detection", "layer": 3, "layer_name": "Network (L3)",
     "desc": "Detect the default gateway IP address using ip route / route -n get / netstat -rn / procfs.",
     "docs": "Command: ip route show default / route -n get default / netstat -rn  |  Plan B: /proc/net/route",
     "params": [],
     "presets": [{"name": "Default", "values": {}}],
     "run": lambda kw: {"gateway": netinfo.detect_gateway(), "interface": netinfo.get_default_interface()}},
    # Layer 4 - Transport
    {"id": "tcp_test", "name": "TCP Connect Test", "layer": 4, "layer_name": "Transport (L4)",
     "desc": "Measure TCP handshake latency to a target host:port. Detects firewall drops, timeouts, and reachability issues.",
     "docs": "Method: socket.create_connection() — stdlib only, no external tool required.",
     "params": [
         {"key": "host", "label": "Target host", "type": "text", "default": "1.1.1.1"},
         {"key": "port", "label": "Port", "type": "number", "default": 443, "min": 1, "max": 65535},
         {"key": "count", "label": "Attempts", "type": "number", "default": 5, "min": 1, "max": 100},
         {"key": "timeout", "label": "Timeout (s)", "type": "number", "default": 3, "min": 1, "max": 10},
     ],
     "presets": [
         {"name": "Quick (3 attempts)", "values": {"host": "1.1.1.1", "port": 443, "count": 3, "timeout": 3}},
         {"name": "Standard (10 attempts)", "values": {"host": "1.1.1.1", "port": 443, "count": 10, "timeout": 3}},
         {"name": "Common ports", "values": {"host": "google.com", "port": 80, "count": 5, "timeout": 3}},
     ],
     "run": lambda kw: dns_tcp.tcp_test(kw.get("host", "1.1.1.1"), int(kw.get("port", 443)), count=int(kw.get("count", 5)), timeout_s=int(kw.get("timeout", 3)))},
    {"id": "tcp_socket_stats", "name": "TCP Socket Stats (Retransmits)", "layer": 4, "layer_name": "Transport (L4)",
     "desc": "Read TCP retransmit percentage from the system. High retransmits indicate congestion or link issues.",
     "docs": "Command: ss -itp / nettop -J tcp / netstat -s  |  Plan B: /proc/net/tcp (connection count only)",
     "params": [],
     "presets": [{"name": "Default", "values": {}}],
     "run": lambda kw: sockets.tcp_socket_stats(netinfo.get_default_interface())},
    {"id": "iperf3_test", "name": "iPerf3 Throughput", "layer": 4, "layer_name": "Transport (L4)",
     "desc": "Measure TCP/UDP throughput to an iPerf3 server. Tests raw bandwidth capacity and detects retransmits.",
     "docs": "Command: iperf3 -c <server> -t <duration> -J  |  Requires iperf3 server on the remote end.",
     "params": [
         {"key": "server", "label": "iPerf3 server (optional)", "type": "text", "default": ""},
         {"key": "duration", "label": "Test duration (s)", "type": "number", "default": 10, "min": 5, "max": 60},
     ],
     "presets": [
         {"name": "Quick (5s)", "values": {"server": "", "duration": 5}},
         {"name": "Standard (10s)", "values": {"server": "", "duration": 10}},
         {"name": "Long (30s)", "values": {"server": "", "duration": 30}},
     ],
     "run": lambda kw: throughput.iperf3_test(server=kw.get("server") or None, duration=int(kw.get("duration", 10)))},
    # Layer 5-7 - Application
    {"id": "dns_test", "name": "DNS Resolution Test", "layer": 5, "layer_name": "Application (L5-7)",
     "desc": "Measure DNS resolution latency and failure rate using socket.getaddrinfo().",
     "docs": "Method: socket.getaddrinfo() — stdlib only, no external tool required.",
     "params": [
         {"key": "host", "label": "Hostname to resolve", "type": "text", "default": "google.com"},
         {"key": "count", "label": "Queries", "type": "number", "default": 10, "min": 1, "max": 100},
     ],
     "presets": [
         {"name": "Quick (3 queries)", "values": {"host": "google.com", "count": 3}},
         {"name": "Standard (10 queries)", "values": {"host": "google.com", "count": 10}},
         {"name": "All hosts", "values": {"host": "google.com", "count": 10}},
     ],
     "run": lambda kw: dns_tcp.dns_test(kw.get("host", "google.com"), count=int(kw.get("count", 10)))},
    {"id": "http_latency", "name": "HTTP Latency Test", "layer": 7, "layer_name": "Application (L5-7)",
     "desc": "Measure HTTP request latency to multiple endpoints. Detects slow web servers or CDN issues.",
     "docs": "Method: urllib.request — stdlib only.",
     "params": [
         {"key": "hosts", "label": "URLs (comma-separated)", "type": "text", "default": "https://1.1.1.1,https://8.8.8.8,https://google.com"},
         {"key": "count", "label": "Requests per host", "type": "number", "default": 3, "min": 1, "max": 20},
         {"key": "timeout", "label": "Timeout (s)", "type": "number", "default": 5, "min": 1, "max": 15},
     ],
     "presets": [
         {"name": "Quick (1 request)", "values": {"hosts": "https://1.1.1.1,https://google.com", "count": 1, "timeout": 5}},
         {"name": "Standard (3 requests)", "values": {"hosts": "https://1.1.1.1,https://8.8.8.8,https://google.com", "count": 3, "timeout": 5}},
     ],
     "run": lambda kw: webprobes.http_latency_test(hosts=[h.strip() for h in kw.get("hosts", "https://1.1.1.1").split(",") if h.strip()], count=int(kw.get("count", 3)), timeout_s=int(kw.get("timeout", 5)))},
    {"id": "speedtest", "name": "Speedtest (Ookla)", "layer": 7, "layer_name": "Application (L5-7)",
     "desc": "Measure download/upload speed and latency using Ookla's speedtest.net infrastructure.",
     "docs": "Command: speedtest --format=json  |  Plan B: speedtest-cli --json",
     "params": [],
     "presets": [{"name": "Default", "values": {}}],
     "run": lambda kw: throughput.speedtest_result()},
    {"id": "download_test", "name": "Download Test (Images)", "layer": 7, "layer_name": "Application (L5-7)",
     "desc": "Download images from multiple CDNs to measure real-world HTTP download throughput.",
     "docs": "Method: urllib.request on a set of known image URLs — stdlib only.",
     "params": [
         {"key": "count", "label": "Images to download", "type": "number", "default": 50, "min": 1, "max": 200},
         {"key": "timeout", "label": "Timeout per image (s)", "type": "number", "default": 10, "min": 5, "max": 30},
     ],
     "presets": [
         {"name": "Quick (10 images)", "values": {"count": 10, "timeout": 10}},
         {"name": "Standard (50 images)", "values": {"count": 50, "timeout": 10}},
         {"name": "Heavy (100 images)", "values": {"count": 100, "timeout": 15}},
     ],
     "run": lambda kw: webprobes.download_images_test(count=int(kw.get("count", 50)), timeout_s=int(kw.get("timeout", 10)))},
    {"id": "reliability_test", "name": "Reliability / Intermittent Test", "layer": 7, "layer_name": "Application (L5-7)",
     "desc": "Detect intermittent connection failures (first-connect-fails-then-retry-works). Makes many fresh, "
             "cache-defeating HTTPS connections; times DNS/TCP/TLS/first-byte phases; compares IPv4 vs IPv6, "
             "low vs high concurrency, and hostname vs bare-IP targets to localize the cause.",
     "docs": "Method: socket + ssl + urllib (stdlib). Defeats caching via unique URLs, no-cache headers, "
             "Connection: close, fresh sockets, and TLS session-ticket disable. Plan B: urllib total-time.",
     "params": [
         {"key": "targets", "label": "Target URLs (comma-separated, blank = defaults)", "type": "text", "default": ",".join(RELIABILITY_TARGETS)},
         {"key": "samples", "label": "Samples per target", "type": "number", "default": 20, "min": 1, "max": 500},
         {"key": "duration", "label": "Duration (s, 0 = use sample count)", "type": "number", "default": 0, "min": 0, "max": 600},
         {"key": "concurrency", "label": "Concurrency (parallel connections)", "type": "number", "default": 8, "min": 1, "max": 64},
         {"key": "retries", "label": "Retries after a failed first attempt", "type": "number", "default": 2, "min": 0, "max": 5},
         {"key": "timeout", "label": "Timeout per attempt (s)", "type": "number", "default": 5, "min": 1, "max": 30},
         {"key": "ipv", "label": "IP mode (0 = both, 4, or 6)", "type": "text", "default": "0"},
         {"key": "compare_concurrency", "label": "Also run sequential pass (low vs high A/B)", "type": "checkbox", "default": True},
     ],
     "presets": [
         {"name": "Quick", "values": {"samples": 5, "concurrency": 4, "duration": 0, "compare_concurrency": True}},
         {"name": "Standard", "values": {"samples": 20, "concurrency": 8, "duration": 0, "compare_concurrency": True}},
         {"name": "Stress (high concurrency)", "values": {"samples": 20, "concurrency": 32, "duration": 0, "compare_concurrency": True}},
         {"name": "Long-watch (duration)", "values": {"samples": 5, "concurrency": 8, "duration": 60, "compare_concurrency": False}},
     ],
     "run": lambda kw: reliability.reliability_test(
         targets=kw.get("targets") or None,
         samples=int(kw.get("samples", 20) or 20),
         duration_s=int(kw.get("duration", 0) or 0),
         concurrency=int(kw.get("concurrency", 8) or 8),
         retries=int(kw.get("retries", 2) or 0),
         timeout_s=int(kw.get("timeout", 5) or 5),
         ipv=int(kw.get("ipv", 0) or 0),
         compare_concurrency=bool(kw.get("compare_concurrency", True)))},
     {"id": "bufferbloat", "name": "Bufferbloat Test", "layer": 7, "layer_name": "Application (L5-7)",
     "desc": "Run concurrent ping+iPerf3 to measure latency under load. High ratios (>3x) indicate bufferbloat.",
     "docs": "Command: tc -s qdisc + iperf3 (Linux enhanced)  |  Plan B: iperf3 concurrent ping (non-Linux)",
     "params": [],
     "presets": [{"name": "Default", "values": {}}],
     "run": lambda kw: throughput.bufferbloat_test(netinfo.get_default_interface())},
    # Additional standalone tools
    {"id": "quick_ping", "name": "Quick Ping (Single)", "layer": 3, "layer_name": "Network (L3)",
     "desc": "Send a single ICMP echo request for an instant reachability and RTT check. Faster than the burst ping.",
     "docs": "Command: ping -c 1 -W <timeout> <host>  |  Plan B: TCP connect RTT via socket",
     "params": [
         {"key": "host", "label": "Target host", "type": "text", "default": "1.1.1.1"},
         {"key": "timeout", "label": "Timeout (s)", "type": "number", "default": 2, "min": 1, "max": 10},
     ],
     "presets": [
         {"name": "Cloudflare", "values": {"host": "1.1.1.1", "timeout": 2}},
         {"name": "Google", "values": {"host": "8.8.8.8", "timeout": 2}},
         {"name": "Gateway", "values": {"host": "", "timeout": 2}},
     ],
     "run": lambda kw: {"tool": "quick_ping", "gateway_hint": netinfo.detect_gateway(), **ping.ping_once(kw.get("host", "1.1.1.1"), timeout_s=int(kw.get("timeout", 2)))}},
    {"id": "dns_resolve", "name": "DNS Resolve (Single)", "layer": 5, "layer_name": "Application (L5-7)",
     "desc": "Resolve a hostname to IP addresses using a single DNS query. Quick check if DNS is working.",
     "docs": "Method: socket.getaddrinfo() — stdlib only, no external tool required.",
     "params": [
         {"key": "host", "label": "Hostname to resolve", "type": "text", "default": "google.com"},
     ],
     "presets": [
         {"name": "google.com", "values": {"host": "google.com"}},
         {"name": "cloudflare.com", "values": {"host": "cloudflare.com"}},
         {"name": "quad9.net", "values": {"host": "quad9.net"}},
     ],
     "run": lambda kw: ping.resolve_all(kw.get("host", "google.com"))},
    {"id": "classify_ping", "name": "Ping Classification", "layer": 3, "layer_name": "Network (L3)",
     "desc": "Run a ping burst then classify the result into categories: clean, bad_loss, some_loss, latency_spikes, high_jitter.",
     "docs": "Classification thresholds: loss>=5%→bad_loss, loss>=1%→some_loss, p95>=300ms→bad_latency_spikes, p95>=150ms→latency_spikes, jitter>=80ms→high_jitter.",
     "params": [
         {"key": "host", "label": "Target host", "type": "text", "default": "1.1.1.1"},
         {"key": "count", "label": "Ping count", "type": "number", "default": 10, "min": 1, "max": 100},
         {"key": "timeout", "label": "Timeout (s)", "type": "number", "default": 2, "min": 1, "max": 10},
     ],
     "presets": [
         {"name": "Quick (5 pings)", "values": {"host": "1.1.1.1", "count": 5, "timeout": 2}},
         {"name": "Standard (20 pings)", "values": {"host": "1.1.1.1", "count": 20, "timeout": 2}},
     ],
     "run": lambda kw: {"classification": ping.classify_ping(ping.ping_burst(kw.get("host", "1.1.1.1"), int(kw.get("count", 10)), 0.5, timeout_s=int(kw.get("timeout", 2)), label="classify")), "host": kw.get("host", "1.1.1.1")}},
    {"id": "check_tools", "name": "Tool Availability Check", "layer": 0, "layer_name": "System",
     "desc": "Check which external command-line tools (ping, ip, mtr, iperf3, speedtest, etc.) are installed and available.",
     "docs": "Scans PATH for required and optional diagnostic tools. Missing optional tools reduce diagnostic detail but stdlib fallbacks are always available.",
     "params": [],
     "presets": [{"name": "Default", "values": {}}],
     "run": lambda kw: rt.check_tools()},
    {"id": "full_diagnostic", "name": "Full Diagnostic (All Layers)", "layer": 0, "layer_name": "All Layers",
     "desc": "Orchestrate every probe in sequence: interface, wifi, ethtool, gateway ping, internet ping, DNS, TCP, MTR, speedtest, iPerf3, bufferbloat, download test, HTTP latency, MTU probe.",
     "docs": "This is the same engine used by Troubleshoot/Dashboard, exposed here with every toggle for fine-grained control. Caution: enabling all probes can take 60-120s.",
     "params": [
         {"key": "hosts", "label": "Ping hosts (comma-separated)", "type": "text", "default": "1.1.1.1,8.8.8.8"},
         {"key": "count", "label": "Ping count per host", "type": "number", "default": 5, "min": 1, "max": 200},
         {"key": "speedtest", "label": "Run speedtest", "type": "checkbox", "default": False},
         {"key": "trace", "label": "Run MTR trace", "type": "checkbox", "default": False},
         {"key": "bufferbloat", "label": "Run bufferbloat test", "type": "checkbox", "default": False},
         {"key": "iperf3", "label": "Run iPerf3", "type": "checkbox", "default": False},
         {"key": "download_test", "label": "Download test", "type": "checkbox", "default": False},
         {"key": "connection_test", "label": "HTTP latency + MTU", "type": "checkbox", "default": False},
     ],
     "presets": [
         {"name": "Minimal (ping only)", "values": {"hosts": "1.1.1.1", "count": 5, "speedtest": False, "trace": False, "bufferbloat": False, "iperf3": False, "download_test": False, "connection_test": False}},
         {"name": "Standard diagnostic", "values": {"hosts": "1.1.1.1,8.8.8.8", "count": 10, "speedtest": False, "trace": False, "bufferbloat": False, "iperf3": False, "download_test": False, "connection_test": False}},
         {"name": "Full (everything)", "values": {"hosts": "1.1.1.1,8.8.8.8,google.com", "count": 20, "speedtest": True, "trace": True, "bufferbloat": True, "iperf3": True, "download_test": True, "connection_test": True}},
     ],
     "run": lambda kw: orchestrate.full_diagnostic(_diag_args_from_kw(kw))},
    {"id": "diagnose_engine", "name": "Diagnose Results (Analysis)", "layer": 0, "layer_name": "All Layers",
     "desc": "Run the 5-layer diagnostic rule engine on fresh results. Analyzes interface errors, WiFi signal, gateway stability, ISP routing, and internet health.",
     "docs": "Five layers: Physical (L1) → WiFi (L2) → Gateway (L3) → ISP (L3-L4) → Internet (L5-7). Each diagnosis includes severity, title, detail, and fix recommendation.",
     "params": [
         {"key": "host", "label": "Target host", "type": "text", "default": "1.1.1.1"},
         {"key": "count", "label": "Ping count", "type": "number", "default": 5, "min": 1, "max": 20},
     ],
     "presets": [
         {"name": "Quick (3 pings)", "values": {"host": "1.1.1.1", "count": 3}},
         {"name": "Standard (5 pings)", "values": {"host": "1.1.1.1", "count": 5}},
     ],
     "run": lambda kw: {"diagnosis": analysis.diagnose(orchestrate.full_diagnostic(_diag_args_from_kw({"hosts": kw.get("host", "1.1.1.1"),"count": int(kw.get("count", 5)),"trace": False,"bufferbloat": False,"speedtest": False,"iperf3": False,"download_test": False,"connection_test": False})))}},
    {"id": "health_score_tool", "name": "Health Score Calculator", "layer": 0, "layer_name": "All Layers",
     "desc": "Compute the composite 0-100 health score from a fresh diagnostic run. Weighted: interface 10%, wifi 15%, gateway 25%, internet 25%, dns 10%, tcp 5%, bufferbloat 10%.",
     "docs": "Formula: weighted average of per-layer scores. Score >=70 = clean, 40-69 = warning, <40 = bad.",
     "params": [
         {"key": "host", "label": "Target host", "type": "text", "default": "1.1.1.1"},
         {"key": "count", "label": "Ping count", "type": "number", "default": 5, "min": 1, "max": 20},
     ],
     "presets": [
         {"name": "Quick (3 pings)", "values": {"host": "1.1.1.1", "count": 3}},
         {"name": "Standard (5 pings)", "values": {"host": "1.1.1.1", "count": 5}},
     ],
     "run": lambda kw: {"health_score": analysis.health_score(orchestrate.full_diagnostic(_diag_args_from_kw({"hosts": kw.get("host", "1.1.1.1"),"count": int(kw.get("count", 5)),"trace": False,"bufferbloat": False,"speedtest": False,"iperf3": False,"download_test": False,"connection_test": False})))}},
]
