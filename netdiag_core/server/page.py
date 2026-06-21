"""Frontend page assembly.

The UI lives as static files under netdiag_core/frontend/ (index.html shell,
styles.css, js/*.js, partials/*.html). assemble_index() injects the per-tab
partials into the shell at the <!--PARTIALS--> marker; the static assets are
served from FRONTEND_DIR. Paths are anchored to this file so they resolve no
matter the current working directory.
"""

from pathlib import Path

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"

# DOM order of the tab sections (matches the original single-file template).
PARTIAL_ORDER = ["dashboard", "troubleshoot", "monitor", "history", "settings", "about", "tools"]


def assemble_index():
    base = (FRONTEND_DIR / "index.html").read_text(encoding="utf-8")
    parts = "\n".join(
        (FRONTEND_DIR / "partials" / f"{name}.html").read_text(encoding="utf-8")
        for name in PARTIAL_ORDER
    )
    return base.replace("<!--PARTIALS-->", parts)
