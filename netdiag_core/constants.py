"""Static data constants — host lists, ICMP rate-limiters, well-known sites.

Pure data, no imports, no platform logic (platform flags live in runtime.py so
they have a single mockable home). Safe to import from anywhere.
"""

DEFAULT_HOSTS = ["1.1.1.1", "8.8.8.8", "9.9.9.9", "google.com"]
DNS_HOSTS = ["google.com", "cloudflare.com", "quad9.net"]
TCP_TARGETS = [("1.1.1.1", 443), ("8.8.8.8", 443), ("google.com", 443)]
# Public anycast resolvers that deliberately deprioritize / rate-limit ICMP echo.
# A high ICMP "loss" figure to these while TCP/HTTPS to the same address succeeds
# is the resolver shedding ping load, NOT packets being dropped on the line. We
# never report packet loss the working TCP layer contradicts (see reconcile_icmp).
ICMP_RATE_LIMITERS = {
    "1.1.1.1", "1.0.0.1", "8.8.8.8", "8.8.4.4",
    "9.9.9.9", "149.112.112.112", "208.67.222.222", "208.67.220.220",
}
IPERF_SERVER = "iperf3.moji.fr"
# Reliability probe targets: a deliberate mix of hostname HTTPS endpoints (small
# bodies/images) and bare-IP endpoints. Bare-IP targets skip DNS entirely, which
# lets the probe isolate resolver intermittency from connection intermittency.
RELIABILITY_TARGETS = [
    "https://www.google.com/generate_204",
    "https://cloudflare.com/cdn-cgi/trace",
    "https://www.wikipedia.org/static/images/project-logos/enwiki.png",
    "https://1.1.1.1/",
    "https://8.8.8.8/",
]

# 100 well-known, safe, globally-reachable HTTPS sites. The intermittent-connection
# reproducer fetches each site's favicon (a tiny static asset present on all of them)
# many times with cache-busting, recreating the "page with lots of small images"
# load pattern that triggers intermittent first-attempt failures. We only measure the
# CONNECTION (DNS/TCP/TLS/first byte), so a redirect or 404 still exercises the full
# path and counts as a reachable connection.
WELLKNOWN_SITES = [
    "google.com", "youtube.com", "facebook.com", "wikipedia.org", "amazon.com",
    "reddit.com", "microsoft.com", "apple.com", "netflix.com", "instagram.com",
    "linkedin.com", "github.com", "stackoverflow.com", "cloudflare.com", "mozilla.org",
    "wordpress.org", "bbc.com", "cnn.com", "nytimes.com", "theguardian.com",
    "yahoo.com", "bing.com", "duckduckgo.com", "ebay.com", "twitch.tv",
    "spotify.com", "paypal.com", "dropbox.com", "adobe.com", "salesforce.com",
    "oracle.com", "ibm.com", "intel.com", "nvidia.com", "amd.com",
    "samsung.com", "sony.com", "dell.com", "hp.com", "cisco.com",
    "vmware.com", "redhat.com", "ubuntu.com", "debian.org", "python.org",
    "nodejs.org", "npmjs.com", "docker.com", "kubernetes.io", "gitlab.com",
    "bitbucket.org", "atlassian.com", "slack.com", "zoom.us", "notion.so",
    "figma.com", "canva.com", "medium.com", "quora.com", "pinterest.com",
    "tumblr.com", "vimeo.com", "soundcloud.com", "imdb.com", "booking.com",
    "airbnb.com", "uber.com", "etsy.com", "shopify.com", "squarespace.com",
    "godaddy.com", "namecheap.com", "digitalocean.com", "fastly.com", "akamai.com",
    "mit.edu", "stanford.edu", "harvard.edu", "nasa.gov", "who.int",
    "un.org", "europa.eu", "archive.org", "ietf.org", "w3.org",
    "gnu.org", "kernel.org", "apache.org", "nginx.org", "postgresql.org",
    "mysql.com", "mongodb.com", "redis.io", "elastic.co", "hashicorp.com",
    "wordpress.com", "wikimedia.org", "creativecommons.org", "letsencrypt.org", "openstreetmap.org",
]

APT_PACKAGES = {
    "ping": "iputils-ping",
    "ip": "iproute2",
    "traceroute": "traceroute",
    "mtr": "mtr-tiny",
    "speedtest-cli": "speedtest-cli",
}
