"""Cancellable subprocess execution with bounded polling and durable output logs.

No shell execution. Cancellation terminates process descendants when supported and
never writes a success marker. Logs are flushed line-by-line for debugging.
"""
from __future__ import annotations

import os
import queue
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Callable


def enable_dpi_awareness() -> bool:
    """Let Tk use the real display DPI on Windows (125%/150% scaling) instead of a blurred bitmap stretch."""
    if sys.platform != 'win32':
        return False
    try:
        import ctypes
    except ImportError:
        return False
    try:
        return ctypes.windll.shcore.SetProcessDpiAwareness(1) == 0
    except Exception:
        try:
            return bool(ctypes.windll.user32.SetProcessDPIAware())
        except Exception:
            return False


class CancelledError(RuntimeError):
    """User requested cancellation. Work files are retained for resumption."""


class CancelToken:
    def __init__(self):
        self._event = threading.Event()

    def cancel(self):
        self._event.set()

    def check(self):
        if self._event.is_set():
            raise CancelledError('操作により処理を中断しました。取得済みキャッシュは保持されています')

    @property
    def cancelled(self):
        return self._event.is_set()


def hidden_process_kwargs() -> dict:
    """Start console children without a black window; the GUI shows progress instead."""
    if os.name != 'nt':
        return {'start_new_session': True}
    return {'creationflags': subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP}


def _stop(proc: subprocess.Popen):
    if proc.poll() is not None:
        return
    if os.name == 'nt':
        # taskkill /T terminates child processes; scope strictly to Popen's PID.
        subprocess.run(['taskkill', '/PID', str(proc.pid), '/T', '/F'],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       timeout=8, check=False, **hidden_process_kwargs())
    else:
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            proc.terminate()
    try:
        proc.wait(timeout=3)
    except subprocess.TimeoutExpired:
        if os.name != 'nt':
            try: os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError: pass
        proc.kill()
        proc.wait(timeout=3)


def run_logged(argv: list[str], log_path: Path, log: Callable[[str], None] = print,
               *, cancel: CancelToken | None = None, timeout: float = 7200) -> int:
    """Run non-shell command; stream output and poll for cancellation/timeout.

    The reader thread avoids getting stuck on a quiet child process's readline().
    A non-zero exit raises RuntimeError; an explicit cancellation raises
    CancelledError and terminates the process tree (best effort on Windows).
    """
    if cancel: cancel.check()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    # Back-pressure protects RAM if a child emits logs faster than the GUI can consume them.
    lines: queue.Queue[str | None] = queue.Queue(maxsize=512)
    stop_reader = threading.Event()
    extra = hidden_process_kwargs()
    proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, encoding='utf-8', errors='replace', bufsize=1, **extra)
    assert proc.stdout is not None

    def reader():
        try:
            for line in proc.stdout:
                if stop_reader.is_set(): break
                while not stop_reader.is_set():
                    try:
                        lines.put(line.rstrip('\r\n'), timeout=0.15)
                        break
                    except queue.Full:
                        continue
        except (OSError, ValueError):
            # Closing the pipe during cancellation can interrupt the reader.
            if not stop_reader.is_set(): raise
        finally:
            while not stop_reader.is_set():
                try:
                    lines.put(None, timeout=0.15)
                    break
                except queue.Full:
                    continue

    thread = threading.Thread(target=reader, daemon=True)
    thread.start()
    began = time.monotonic()
    finished_reader = False
    try:
        with log_path.open('w', encoding='utf-8') as file:
            while True:
                if cancel: cancel.check()
                if time.monotonic() - began > timeout:
                    raise TimeoutError(f'処理時間が上限 {timeout} 秒を超えました: {argv[0]}')
                try:
                    line = lines.get(timeout=.15)
                    if line is None:
                        finished_reader = True
                    else:
                        file.write(line + '\n')
                        file.flush()
                        log(line)
                except queue.Empty:
                    pass
                if finished_reader and proc.poll() is not None and lines.empty():
                    break
        code = proc.wait()
        if cancel: cancel.check()
        if code != 0:
            raise RuntimeError(f'外部プロセスが異常終了 (exit={code}): {argv[0]} / {log_path}')
        return code
    except BaseException:
        _stop(proc)
        raise
    finally:
        stop_reader.set()
        proc.stdout.close()
        thread.join(timeout=1)
