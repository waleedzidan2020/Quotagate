from __future__ import annotations

import os
import subprocess
import threading
import time
from pathlib import Path
from typing import Callable

CONFIRM_TEXT = 'SHUTDOWN'
SHUTDOWN_PATH = '/sbin/shutdown'
SHUTDOWN_COMMAND = (SHUTDOWN_PATH, '-h', 'now')
DEFAULT_DELAY_SECONDS = 2.0


def validate_shutdown_request(confirm: object) -> tuple[str, str, str]:
    """Validate a shutdown request without executing anything."""
    if str(confirm) != CONFIRM_TEXT:
        raise ValueError('Type SHUTDOWN exactly to confirm')
    if os.geteuid() != 0:
        raise PermissionError('QuotaGate shutdown requires root service privileges')
    binary = Path(SHUTDOWN_PATH)
    if not binary.is_file():
        raise RuntimeError('/sbin/shutdown is not available on this system')
    if not os.access(SHUTDOWN_PATH, os.X_OK):
        raise PermissionError('/sbin/shutdown is not executable')
    return SHUTDOWN_COMMAND


def execute_shutdown(popen: Callable[..., object] = subprocess.Popen) -> object:
    """Execute the one hard-coded SysV shutdown command."""
    return popen(
        list(SHUTDOWN_COMMAND),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
        start_new_session=True,
        shell=False,
    )


def _delayed_shutdown(delay_seconds: float, on_error: Callable[[Exception], None] | None) -> None:
    time.sleep(max(0.0, float(delay_seconds)))
    try:
        execute_shutdown()
    except Exception as exc:
        if on_error is not None:
            try:
                on_error(exc)
            except Exception:
                pass


def schedule_shutdown(
    confirm: object,
    delay_seconds: float = DEFAULT_DELAY_SECONDS,
    on_error: Callable[[Exception], None] | None = None,
    thread_factory: Callable[..., threading.Thread] = threading.Thread,
) -> dict[str, object]:
    """Schedule shutdown after a short delay so the HTTP response can flush first."""
    validate_shutdown_request(confirm)
    worker = thread_factory(
        target=_delayed_shutdown,
        args=(delay_seconds, on_error),
        daemon=True,
        name='quotagate-shutdown',
    )
    worker.start()
    return {
        'ok': True,
        'message': 'Shutdown initiated',
        'delay_seconds': float(delay_seconds),
    }
