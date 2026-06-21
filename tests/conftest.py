import sys
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SRC_DIR))


def pytest_configure(config):
    REQ_MARKERS = [
        f"REQ{i:03d}: Functional requirement {i}" for i in range(1, 30)
    ] + [
        f"NFR{i:03d}: Non-functional requirement {i}" for i in range(1, 8)
    ] + [
        "REQ_E2E: E2E-specific requirement",
    ]
    for marker in REQ_MARKERS:
        config.addinivalue_line("markers", marker)
