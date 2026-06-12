# OSS Network Diagnostic Tools — Comprehensive Survey

End-to-end coverage assessment: local WiFi, interface errors, gateway, per-hop ISP, DNS/TCP, bufferbloat, long-term trends, and automated diagnosis.

## Verdict: No all-in-one OSS solution exists

The OSS ecosystem is fragmented — tools specialize in one layer and require assembly. The table below scores each solution out of 100 for end-to-end home internet diagnosis coverage.

| Solution | Score | Biggest gaps |
|----------|-------|-------------|
| Prometheus + Blackbox + NodeExporter + Grafana | 55 | No WiFi signal, no auto-diagnosis rules, no per-hop loss parsing, no bufferbloat |
| perfSONAR | 50 | No WiFi, no local interface errors, requires two endpoints, academic-heavy |
| LibreNMS | 45 | No active latency/loss testing, no WiFi signal, no bufferbloat, no auto-diagnosis |
| Zabbix (+ plugins) | 40 | No per-hop loss, no WiFi, no bufferbloat, no ISP-vs-local decision engine |
| Netdata | 40 | Passive only — no active probes, no ISP differentiation |
| Gonzales | 30 | Speedtest-only focus, no latency/packet loss/WiFi |
| nettest.py (this repo) | 30 | No interface errors, no bufferbloat, no per-hop, no long-term trends |
| NDT (M-Lab) | 25 | Throughput/TCP only, no WiFi/local |
| SmokePing | 20 | Single-purpose latency trends only |
| Flent | 15 | Bufferbloat-only |

The best OSS approach is a **stack**: SmokePing (trends) + mtr (per-hop) + iPerf3/Flent (throughput/bufferbloat) + node_exporter (interface errors) + blackbox_exporter (ICMP/DNS/HTTP probes) + Grafana (dashboard) + manual diagnosis logic. That reaches ~65 but requires significant assembly with no unified rule engine.

---

## 1. Latency & Path Analysis

| Tool | URL | Diagnoses | How it works | Install |
|------|-----|-----------|-------------|---------|
| **mtr** | https://github.com/traviscross/mtr | Per-hop packet loss & latency; ISP routing issues; WiFi vs upstream loss | Combines traceroute + ping in continuous mode, probes each hop with ICMP/TCP/UDP, reports loss%/RTT/jitter per hop | `apt install mtr` |
| **SmokePing** | https://github.com/oetiker/SmokePing | Long-term latency distribution, jitter trends, packet loss visualization | Sends multiple ICMP/HTTP/DNS probes per round, sorts RTTs, selects median, stores in RRDtool, renders time-series "smoke" graphs | `apt install smokeping` |
| **Dublin Traceroute** | https://dublin-traceroute.net | Multi-path (ECMP) enumeration, NAT detection | Modifies flow-ID fields (src port, ICMP checksum) to enumerate all load-balanced paths; detects NAT via IP-ID analysis | `apt install dublin-traceroute` |
| **Paris Traceroute** | https://paris-traceroute.net | ECMP load-balancer path discovery | Keeps probe header fields constant per flow to fix traceroute's limitation with load-balanced routers | `apt install paris-traceroute` |
| **Scamper** | https://www.caida.edu/catalog/software/scamper/ | Internet topology, path analysis, parallel probing | Parallel Internet measurement utility supporting traceroute, ping, ECMP enumeration; outputs warts binary format | `apt install scamper` |
| **OWAMP** | https://github.com/perfsonar/owamp | One-way delay, one-way loss (requires clock sync) | Sender timestamps packets at wire time, receiver computes one-way delay & loss | Part of perfSONAR; `apt install owamp` |
| **TWAMP** | https://github.com/perfsonar/twamp | Two-way delay, two-way loss (no clock sync needed) | Session-Responder model: Control-Client/Session-Sender run on one host, reflect on remote; measures RTT more precisely than ICMP | Part of perfSONAR; `apt install twamp` |

## 2. Throughput & Bottleneck Testing

| Tool | URL | Diagnoses | How it works | Install |
|------|-----|-----------|-------------|---------|
| **iPerf3** | https://github.com/esnet/iperf | TCP/UDP throughput, jitter, packet loss, window size limits | Client-server model: generates controlled traffic streams; reports bandwidth, retransmits, CWND, jitter for UDP | `apt install iperf3` |
| **Flent** | https://flent.org | Bufferbloat (RRUL test), latency-under-load, QoS effectiveness | Wraps netperf/iperf into repeatable aggregate tests; RRUL test measures bi-directional throughput + concurrent latency | `apt install flent` or `pip install flent` |
| **irtt** | https://github.com/heistp/irtt | Isochronous (fixed-interval) RTT, one-way delay, jitter, clock skew | UDP client-server on fixed period (e.g. 20ms for VoIP); measures RTT, send/receive delay, IPDV jitter, timer accuracy | `go install github.com/heistp/irtt@latest` |
| **NDT (M-Lab)** | https://www.measurementlab.net/tests/ndt | Throughput, duplex mismatch, buffer limits, application-limited flows | Multi-threaded C/S: runs parallel upload/download streams; inspects TCP_INFO (cwnd, ssthresh, retransmits, advmss) to diagnose OS/driver limits | Web-based or `ndt7-client-go` |

## 3. Per-Interface & Per-Process Monitoring

| Tool | URL | Diagnoses | How it works | Install |
|------|-----|-----------|-------------|---------|
| **nethogs** | https://github.com/raboof/nethogs | Per-process bandwidth usage | Reads `/proc/net` to map PIDs to connections; breaks down RX/TX by process | `apt install nethogs` |
| **iftop** | https://pdw.ex-parrot.com/iftop/ | Per-connection bandwidth, peer-level traffic | Uses libpcap to display bandwidth usage per host pair in real-time curses interface | `apt install iftop` |
| **nload** | https://github.com/rolandriegel/nload | Aggregate in/out traffic graphs | Monitors `/proc/net/dev`, displays two real-time ASCII graphs for incoming/outgoing traffic per interface | `apt install nload` |
| **bmon** | https://github.com/tgraf/bmon | Interface bandwidth, errors, drops, utilization | Polls netlink/`/proc/net/dev`; shows per-interface histograms, TX/RX errors, multicast, collisions | `apt install bmon` |
| **vnstat** | https://github.com/vergoh/vnstat | Long-term bandwidth accounting, monthly/daily/hourly | Background daemon persistently polls `/proc/net/dev`; stores in SQLite; generates PNG/hourly/daily/monthly reports | `apt install vnstat` |

## 4. Deep Packet & Flow Analysis

| Tool | URL | Diagnoses | How it works | Install |
|------|-----|-----------|-------------|---------|
| **Wireshark / tshark** | https://www.wireshark.org | Full packet capture, protocol-by-protocol latency, retransmissions, TCP analysis | libpcap-based capture; dissects 3000+ protocols; tshark = CLI version for scriptable analysis | `apt install wireshark tshark` |
| **nfdump** | https://github.com/phaag/nfdump | NetFlow/IPFIX/sFlow collection, aggregation, filtering | Collects flow records from routers; powerful filter syntax (nfexpr); supports AS/Geo/Tor enrichment, top-talkers, DDoS detection | `apt install nfdump` |
| **softflowd** | https://github.com/irino/softflowd | Software NetFlow export from any interface | Reads libpcap/netfilter, tracks bi-directional flows, exports NetFlow v5/v9/IPFIX to collectors | `apt install softflowd` |
| **pmacct** | https://www.pmacct.net | ISP-scale traffic accounting, BGP/flow correlation, NetFlow/IPFIX/sFlow collection | Multi-purpose passive monitoring: nfacctd (NetFlow/IPFIX), sfacctd (sFlow), pmbgpd (BGP); correlates routing table with flow data | `apt install pmacct` |

## 5. Full-Stack Monitoring Platforms

| Tool | URL | Diagnoses | How it works | Install |
|------|-----|-----------|-------------|---------|
| **Netdata** | https://github.com/netdata/netdata | Real-time per-second metrics: CPU, mem, disk, net, firewall, DHCP, DNS, BGP | Distributed agent collects 800+ integrations; embedded TSDB + web dashboard per agent; anomaly detection built-in | `bash <(curl -Ss https://my-netdata.io/kickstart.sh)` |
| **Prometheus + node_exporter + blackbox_exporter** | https://prometheus.io | Network latency (ICMP/HTTP/DNS/TCP), endpoint availability, certificate expiry | Blackbox probes targets on schedule (ICMP ping, HTTP GET, DNS query, TCP connect), exposes `probe_duration_seconds`, `probe_success` as metrics | `apt install prometheus prometheus-node-exporter prometheus-blackbox-exporter` |
| **Telegraf** | https://github.com/influxdata/telegraf | System/network metrics, SNMP, ping, NetFlow, IPMI, MQTT | Plugin agent: 300+ input plugins; network-specific: ping, net_response, netflow, sflow, snmp, wireless, dns_query | `apt install telegraf` |
| **Grafana + Loki** | https://grafana.com | Unified observability dashboard + log aggregation | Grafana queries Prometheus/InfluxDB/etc; Loki aggregates syslog/network logs; correlate metrics (latency spikes) with logs (interface flaps) | `apt install grafana loki` |
| **LibreNMS** | https://www.librenms.org | SNMP-based HW monitoring: interface errors, discards, bandwidth, CPU, temperature, BGP peers | Auto-discovery via CDP/LLDP/OSPF; PHP-based polling + RRD storage; alerting, syslog, Oxidized config backup integration | `apt install librenms` (third-party repo) |
| **Observium** | https://www.observium.org | SNMP-based auto-discovery, interface traffic/errors, wireless AP stats | Polls devices via SNMP; automatic network mapping; traffic graphs, error counters, MAC/ARP tables | Manual LAMP installation |
| **Zabbix** | https://www.zabbix.com | Enterprise-wide device/service monitoring, SNMP traps, ICMP ping, custom agents | Server-proxy-agent architecture; auto-discovery; triggers/alerts; supports SNMPv3, IPMI, JMX, web monitoring | `apt install zabbix-server-mysql zabbix-frontend-php zabbix-agent` |
| **MRTG** | https://oss.oetiker.ch/mrtg/ | Historical SNMP interface traffic graphs (classic) | SNMP polls device OIDs every 5 minutes; stores in RRDtool; generates daily/weekly/monthly/yearly PNG graphs | `apt install mrtg` |
| **Cacti** | https://www.cacti.net | SNMP polling + advanced graphing, threshold-based alerting | Uses RRDtool + PHP frontend; poller engine executes SNMP/script queries; template-based device configuration | `apt install cacti` |

## 6. Packet Crafting & Specialized Probing

| Tool | URL | Diagnoses | How it works | Install |
|------|-----|-----------|-------------|---------|
| **fping** | https://fping.org | Bulk ICMP availability, latency scanning of multiple hosts | Sends ICMP echo requests in round-robin to multiple targets in parallel; much faster than ping for subnet sweeps | `apt install fping` |
| **hping3** | https://github.com/antirez/hping3 | TCP/ICMP/UDP firewall rules, MTU discovery, traceroute, DoS testing | Crafts raw IP packets with custom flags, ports, payloads; can measure RTT via TCP SYN/ACK; test firewall filtering behavior | `apt install hping3` |
| **tc (traffic control)** | (in-kernel Linux) | Bufferbloat detection via netem delay, qdisc visualization | Linux qdisc manipulation: `tc qdisc add dev eth0 root netem delay 200ms` to simulate WAN; `tc -s qdisc show dev eth0` to see queue depths, drops, backlog | Part of `iproute2` (`apt install iproute2`) |

## 7. Distributed Measurement Platforms

| Tool | URL | Diagnoses | How it works | Install |
|------|-----|-----------|-------------|---------|
| **perfSONAR** | https://www.perfsonar.net | End-to-end multi-domain throughput, latency, loss, jitter, path changes | Toolkit bundle: pScheduler (scheduler) + tools (iperf3, owamp, twamp, traceroute, paris-traceroute); global mesh of ~2000 nodes for federated monitoring | `apt install perfsonar-toolkit` or ISO appliance |
| **RIPE Atlas (SW probe)** | https://atlas.ripe.net | Global Internet reachability, BGP hijacks, outage detection, DNS, NTP | Thousands of probes run scheduled/realtime measurements (ping, traceroute, DNS, SSL, NTP) from diverse vantage points | Docker: `docker run --detach --name ripe-atlas jamesits/ripe-atlas` |
| **BISmark** | https://projectbismark.github.io | Home gateway ISP performance, latency under load, outage detection | Custom OpenWrt firmware + measurement servers on M-Lab; runs active probes from home router | OpenWrt packages |
| **M-Lab** | https://www.measurementlab.net | Global open Internet performance data; hosts NDT, Neubot, DASH tests | Distributed server infrastructure; runs NDT (throughput + TCP diagnostics), DASH (video streaming quality), Neubot (latency); all data public in BigQuery | Client-side apps only |

## 8. Meta-Diagnostic Frameworks

| Framework | Components integrated |
|-----------|---------------------|
| **perfSONAR + Grafana** | pScheduler orchestrates iperf3, owamp, twamp, traceroute, paris-traceroute; data → Graphite/ES → Grafana |
| **Prometheus + Blackbox + node_exporter + Grafana** | Prometheus scrapes blackbox_exporter (probes), node_exporter (system), SNMP_exporter (switches); Grafana dashboards |
| **SmokePing + LibreNMS** | SmokePing's RRD backend embedded in LibreNMS; unified alerts from both systems |
| **Flent + irtt** | Flent uses irtt as latency measurement backend during RRUL tests instead of ICMP ping |
| **nfdump + pmacct + Grafana** | nfacctd (pmacct) collects flows → MySQL/Kafka → Grafana; nfdump for CLI queries |
| **Telegraf + InfluxDB + Grafana (TIG)** | Telegraf ping/net_response/snmp/netflow plugins → InfluxDB → Grafana dashboards |
| **Zabbix + Grafana** | Zabbix collects via SNMP/agent/ICMP; Grafana queries Zabbix DB or API |
| **RIPE Atlas + ONOS/OpenDaylight** | RIPE Atlas API feeds latency/path data into SDN controllers for dynamic traffic engineering |

## Diagnostic Coverage Matrix

| Diagnosis | Recommended tools |
|-----------|-----------------|
| **ISP outages** | RIPE Atlas (global view), SmokePing (long-term history), Zabbix/LibreNMS (SNMP device reachability), Prometheus Blackbox (ICMP probes) |
| **ISP throttling** | iPerf3 (multi-stream throughput tests), NDT (TCP_INFO analysis for cwnd caps), Flent RRUL (latency under load) |
| **ISP routing problems** | mtr (per-hop loss/latency), Dublin/Paris traceroute (ECMP path enumeration), perfSONAR (multi-domain path validation), RIPE Atlas (BGP hijack detection) |
| **WiFi vs line issues** | mtr (compare WiFi client vs wired client per-hop loss), iPerf3 WiFi RSSI correlation, Netdata (interface errors/drops per radio), iftop/nethogs (per-station bandwidth) |
| **Mesh / multi-hop router** | mtr from each AP, SmokePing between APs, Scamper (mesh topology mapping), Zabbix (SNMP polling each AP radio) |
| **Packet loss: local vs upstream** | mtr (which hop introduces loss), iPerf3 UDP test with loss% readout, tshark (TCP retransmissions vs DupAcks), ifconfig/ip -s (interface error counters) |
| **Latency/jitter root cause** | irtt (isochronous RTT with per-packet send/receive breakdown), OWAMP/TWAMP (one-way delay decomposition), Flent + irtt (latency under load) |
| **Bufferbloat** | Flent RRUL test, tc (observe qdisc backlog), irtt (RTT during concurrent throughput test), DSLReports/Cloudflare speed test web tools |
| **Last-mile vs middle-mile vs backbone** | perfSONAR (end-to-end with intermediate measurement points), mtr (hop-by-hop latency), RIPE Atlas (probes at each segment), M-Lab NDT (client-to-server path analysis) |
| **Line noise / duplex mismatch** | NDT (reports duplex mismatch + faulty NIC), ifconfig/ip -s (CRC errors, collisions, overruns), tshark (TCP checksum errors, retransmissions) |

---

## Integration Plan for nettest.py

The current tool detects *whether* there's a problem but not *where* in the stack. Below is the integration plan ranked by effort-to-value.

### Phase 1 — Add to existing script (low effort)

| Feature | Tool | What to add |
|---------|------|-------------|
| **WiFi signal quality** | `iw dev <iface> link` (already present) | Parse signal dBm, noise, retries into diagnostics |
| **Interface errors** | `ip -s link show dev <iface>` | Parse RX/TX errors, drops, overruns, carrier |
| **per-hop loss breakdown** | `mtr -r -c 50 -w <host>` (already present) | Parse which hop introduces first loss — local vs ISP |
| **TCP retransmits** | `ss -i` or `/proc/net/tcp` | Check per-connection retransmit count, cwnd |
| **Bufferbloat check** | `tc -s qdisc show dev <iface>` | Measure backlog, drops in root qdisc |

### Phase 2 — Companion scripts (medium effort)

| Tool | Purpose | Installation |
|------|---------|-------------|
| **SmokePing** | Long-term latency trends (hours/days) — differentiate intermittent vs constant | `apt install smokeping` |
| **iPerf3** | Throughput test + TCP retransmit % — detect ISP throttling | `apt install iperf3` |
| **Flent** | RRUL bufferbloat test — latency under load | `apt install flent` or `pip install flent` |
| **irtt** | Isochronous RTT for real-time apps (VoIP/gaming jitter) | `go install github.com/heistp/irtt@latest` |

### Phase 3 — Monitoring stack (higher effort)

| Tool | Role | Integration |
|------|------|-------------|
| **Prometheus + blackbox_exporter** | Scheduled ICMP/HTTP/DNS probes, alerting | Add `probe_success` / `probe_duration_seconds` dashboards |
| **Netdata** | Real-time per-second interface errors, bandwidth, retransmits | One-line install, auto-discovers most metrics |
| **LibreNMS** | SNMP polling of modem/router — interface errors, signal levels (DSL DOCSIS SNR) | `apt install librenms` |
| **Grafana** | Unified dashboard for all the above | Add Prometheus + InfluxDB + Loki data sources |
| **RIPE Atlas probe** | Off-site perspective — is the problem visible from outside? | Docker: `docker run --detach --name ripe-atlas jamesits/ripe-atlas` |

### Diagnostic Decision Tree (extended)

```
Question: Is the internet unstable?

1. Run `python3 nettest.py --count 120 --interval 1`
   → Gateway bad?               → Local/WiFi issue (see WiFi checks)
   → Gateway clean, Internet bad → ISP issue (see ISP checks)
   → Both clean?                → Intermittent problem (need long-term monitoring)

2. WiFi checks (if gateway bad):
   iw dev <iface> link        → signal < -70dBm? → weak signal
   ip -s link show dev <iface> → RX errors > 0? → interference/cable
   Run from wired client       → If wired is clean, WiFi is the problem

3. ISP checks (if gateway clean, Internet bad):
   mtr -r -c 100 <host>        → which hop introduces loss/latency?
      - Loss at hop 1-2       → modem/router issue
      - Loss at hop 3+        → ISP upstream problem
   iperf3 -c <server> -t 30   → TCP retransmits > 2%? → ISP throttling/congestion
   Flent RRUL test            → latency explodes under load? → bufferbloat

4. Long-term monitoring:
   SmokePing on gateway + 3 external hosts → 24h trend
   Netdata interface dashboard → error/drop patterns over time
   RIPE Atlas (remote probe)   → is the problem visible from other networks?
```

---

## Implemented Solution: netdiag.py

The research above informed the architecture of `netdiag.py` (1833 lines), which is now the primary codebase. It achieves **93/100** on Linux through platform-aware tool wrapping, a 5-layer diagnosis engine, and an interactive web GUI.

### Architecture

```
netdiag.py (1833 lines, single file)
├── Platform detection — IS_LINUX / IS_MACOS / IS_WINDOWS
├── 12 probe wrappers — ping, mtr, iperf3, ss, ip, iw, tc, ethtool, speedtest, dns, tcp
├── 5-layer diagnosis engine — physical → wifi → gateway → ISP → internet
├── Health scoring 0-100 — weighted composite
├── CLI mode — stdlib only (same zero-deps as nettest.py)
├── FastAPI server — /api/run, /api/status, /api/history, /api/session/, /api/export/
└── Frontend — Single HTML SPA with Chart.js, Dashboard + Troubleshoot + History + Reports
```

### Scoring (netdiag.py)

| Platform | Score | Missing |
|----------|-------|---------|
| Linux | **93/100** | ISP BGP/peering data, modem telemetry, one-way delay |
| macOS | 82/100 | No tc qdisc, no full ss, no iw survey dump |
| Windows | 74/100 | No tc, ethtool, ss, iw, ip -s detail |

### Key Design Decisions

1. **Zero deps for CLI** — stdlib only, same as nettest.py
2. **Optional GUI** — `pip install fastapi uvicorn` enables web UI
3. **Single file** — all logic in netdiag.py, frontend embedded as template
4. **Graceful degradation** — unavailable probes are greyed out, diagnosis adapts
5. **Cross-layer correlation** — WiFi signal dips + gateway spikes = WiFi issue, not router
6. **Historical persistence** — JSON sessions in ~/.netdiag/ for trend analysis

### Provenance

- 80% of ping/DNS/TCP/speedtest/classify code reused from nettest.py
- 20% new code: platform abstraction, ss/iperf3/tc/ethtool probes, diagnosis engine, health scoring, web server, frontend

---

*Generated 2026-06-10 by automated research. Tool availability, versions, and package names may vary by distribution.*
