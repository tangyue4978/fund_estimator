# storage/json_store.py
from __future__ import annotations

import json
import os
import tempfile
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Optional


@dataclass
class EnsureResult:
    path: str
    data: Any
    created: bool


def _ensure_parent(p: str) -> None:
    Path(p).parent.mkdir(parents=True, exist_ok=True)


def _lock_file_path(path: str) -> Path:
    p = Path(path)
    return p.with_name(p.name + ".lock")


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if pid == os.getpid():
        return True
    if os.name == "nt":
        try:
            import ctypes

            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            handle = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
            if handle:
                ctypes.windll.kernel32.CloseHandle(handle)
                return True
            return False
        except Exception:
            return False
    try:
        os.kill(pid, 0)
        return True
    except PermissionError:
        return True
    except OSError:
        return False


def _clear_stale_lock(lock_path: Path, stale_sec: float = 60.0) -> bool:
    try:
        txt = lock_path.read_text(encoding="ascii", errors="ignore").strip()
        pid = int(txt) if txt else 0
    except Exception:
        pid = 0
    try:
        age = time.time() - lock_path.stat().st_mtime
    except Exception:
        age = stale_sec + 1

    # Remove stale lock when owner pid is gone, or lock is too old.
    owner_dead = bool(pid and (not _pid_alive(pid)))
    if owner_dead or (age >= stale_sec):
        try:
            lock_path.unlink(missing_ok=True)
            return True
        except Exception:
            return False
    return False


@contextmanager
def _file_lock(path: str, timeout_sec: float = 120.0, poll_sec: float = 0.05):
    lock_path = _lock_file_path(path)
    _ensure_parent(str(lock_path))
    if os.name == "nt":
        import msvcrt

        fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR)
        start = time.time()
        while True:
            try:
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                break
            except OSError:
                if (time.time() - start) >= timeout_sec:
                    os.close(fd)
                    raise TimeoutError(f"file lock timeout: {lock_path}")
                time.sleep(poll_sec)
        try:
            yield
        finally:
            try:
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
            finally:
                os.close(fd)
        return

    start = time.time()
    fd: int | None = None
    while True:
        try:
            fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_RDWR)
            os.write(fd, str(os.getpid()).encode("ascii", errors="ignore"))
            break
        except FileExistsError:
            _clear_stale_lock(lock_path)
            if (time.time() - start) >= timeout_sec:
                raise TimeoutError(f"file lock timeout: {lock_path}")
            time.sleep(poll_sec)
    try:
        yield
    finally:
        try:
            if fd is not None:
                os.close(fd)
        finally:
            try:
                lock_path.unlink(missing_ok=True)
            except Exception:
                pass


def _read_text_with_fallback(path: Path) -> str:
    data = path.read_bytes()
    for enc in ("utf-8", "utf-8-sig", "gbk", "gb18030"):
        try:
            return data.decode(enc)
        except Exception:
            continue
    return ""


def load_json(path: str, default: Optional[Any] = None, fallback: Optional[Any] = None) -> Any:
    """
    读取 JSON 文件。
    - default: 文件不存在/读取失败时返回
    - fallback: default 的别名（兼容项目里现有调用）
    """
    if default is None and fallback is not None:
        default = fallback

    try:
        p = Path(path)
        if not p.exists():
            return default
        text = _read_text_with_fallback(p).strip()
        if not text:
            return default
        return json.loads(text)
    except Exception:
        return default


def save_json(path: str, data: Any) -> None:
    _ensure_parent(path)
    target = Path(path)
    text = json.dumps(data, ensure_ascii=False, indent=2)
    fd, tmp_name = tempfile.mkstemp(prefix=f"{target.name}.", suffix=".tmp", dir=str(target.parent))
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        try:
            os.replace(str(tmp_path), str(target))
        except PermissionError:
            # Some Windows environments may deny atomic replace; fallback to direct write.
            target.write_text(text, encoding="utf-8")
    finally:
        if tmp_path.exists():
            try:
                tmp_path.unlink(missing_ok=True)
            except Exception:
                pass


def ensure_json_file(path: str, default_data: Optional[Any] = None) -> EnsureResult:
    """
    确保 JSON 文件存在。若不存在则创建并写入 default_data（默认 {}）。
    返回 EnsureResult，其中 data 为读到/写入的数据。
    """
    default_data = {} if default_data is None else default_data
    _ensure_parent(path)

    p = Path(path)
    if not p.exists():
        save_json(path, default_data)
        return EnsureResult(path=path, data=default_data, created=True)

    data = load_json(path, default=default_data)
    if data is None:
        data = default_data
        save_json(path, data)
        return EnsureResult(path=path, data=data, created=False)

    return EnsureResult(path=path, data=data, created=False)


def update_json(path: str, updater: Callable[[Dict[str, Any]], Dict[str, Any]]) -> Dict[str, Any]:
    """
    读 -> updater -> 写，返回写入后的 dict
    """
    with _file_lock(path):
        res = ensure_json_file(path, default_data={})
        data = res.data if isinstance(res.data, dict) else {}
        new_data = updater(data) or data
        if not isinstance(new_data, dict):
            raise ValueError("update_json: updater must return dict")
        save_json(path, new_data)
        return new_data


def ensure_json_file_with_schema(path: str, schema_default: Dict[str, Any]) -> Dict[str, Any]:
    """
    用于需要固定 schema 的 json：缺字段就补齐
    """
    res = ensure_json_file(path, default_data=schema_default)
    data = res.data if isinstance(res.data, dict) else {}

    changed = False
    for k, v in schema_default.items():
        if k not in data:
            data[k] = v
            changed = True

    if changed:
        save_json(path, data)
    return data
