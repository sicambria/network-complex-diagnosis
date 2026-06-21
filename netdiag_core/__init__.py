"""NetDiag core package.

Platform-agnostic internet diagnostics. The CLI core is stdlib-only; the web
GUI (netdiag_core.server) lazily imports fastapi/uvicorn. See docs/architecture.md.

Import the package as a library via the top-level ``netdiag`` shim
(``from netdiag import diagnose``) which re-exports this package's public surface.
"""

SPDX_LICENSE = "AGPL-3.0-only"
