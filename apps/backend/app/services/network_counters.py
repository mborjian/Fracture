from __future__ import annotations

import contextlib

try:
    import psutil
except Exception:  # noqa: BLE001
    psutil = None


def total_rxtx_bytes() -> tuple[int, int] | None:
    if psutil is None:
        return None
    with contextlib.suppress(Exception):
        counters = psutil.net_io_counters()
        if counters is None:
            return None
        return int(counters.bytes_recv), int(counters.bytes_sent)
    return None
