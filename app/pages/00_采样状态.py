import json
import sys
from pathlib import Path
from datetime import datetime

import streamlit as st

BASE_DIR = Path(__file__).resolve().parents[2]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from storage import paths
from services.auth_guard import require_login


def _first_existing(cands: list[Path]) -> Path:
    for p in cands:
        if p.exists():
            return p
    return cands[0]


status_candidates: list[Path] = []
log_candidates: list[Path] = []

if hasattr(paths, "file_collector_status"):
    status_candidates.append(Path(paths.file_collector_status()))
if hasattr(paths, "file_collector_log"):
    log_candidates.append(Path(paths.file_collector_log()))
if hasattr(paths, "status_dir"):
    status_candidates.append(Path(paths.status_dir()) / "collector_status.json")
if hasattr(paths, "runtime_root"):
    rt = Path(paths.runtime_root())
    status_candidates.append(rt / "status" / "collector_status.json")
    log_candidates.append(rt / "logs" / "collector.log")

# 旧位置兜底（项目内 storage 目录）
status_candidates.append(BASE_DIR / "storage" / "status" / "collector_status.json")
log_candidates.append(BASE_DIR / "storage" / "logs" / "collector.log")

STATUS_PATH = _first_existing(status_candidates)
LOG_PATH = _first_existing(log_candidates)

st.set_page_config(page_title="采集器状态", layout="wide")
require_login()
st.title("📡 采集器状态 / 心跳监控")


def read_status(path: Path):
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def read_last_lines(path: Path, n: int = 80):
    if not path.exists():
        return []
    try:
        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
        return lines[-n:]
    except Exception:
        return []


def parse_last_heartbeat(lines):
    # 从日志里解析最后一条时间戳
    for line in reversed(lines):
        if "[collector]" not in line:
            continue
        try:
            ts = line.split("[collector]")[1].strip()[:19]
            return datetime.strptime(ts, "%Y-%m-%d %H:%M:%S")
        except Exception:
            continue
    return None


status = read_status(STATUS_PATH)
lines = read_last_lines(LOG_PATH, n=120)

last_ts = None
phase = None
last_error = None

# 优先用 status.json
if status:
    try:
        last_ts = datetime.fromisoformat(str(status.get("updated_at", "")))
    except Exception:
        last_ts = None
    phase = status.get("phase")
    last_error = status.get("last_error")

# fallback：没有 status 再从日志推断
if not last_ts:
    last_ts = parse_last_heartbeat(lines)

# ====== 顶部概览 ======
col1, col2, col3, col4 = st.columns(4)

col1.metric("status.json 是否存在", "✅" if STATUS_PATH.exists() else "❌")
col2.metric("collector.log 是否存在", "✅" if LOG_PATH.exists() else "❌")
col3.metric("当前 phase", str(phase) if phase else "-")

if last_ts:
    delta = (datetime.now() - last_ts).total_seconds()
    col4.metric("距最近心跳", f"{int(delta)} 秒")
else:
    col4.metric("距最近心跳", "-")

# ====== 状态提示 ======
if last_error:
    st.error(f"最近错误：{last_error}")
elif phase == "outside_trading":
    st.info("当前不在交易时段，采集器处于等待状态（正常）。")

if last_ts:
    delta = (datetime.now() - last_ts).total_seconds()
    if delta <= 60:
        st.success("采集器运行正常 ✅（最近 60 秒内有心跳）")
    elif delta <= 300:
        st.warning("采集器疑似暂停 ⚠️（超过 60 秒未更新）")
    else:
        st.error("采集器可能已停止 ❌（超过 5 分钟未更新）")
else:
    st.warning("未解析到心跳时间：请先运行一次采集器，或检查 status/log 路径。")

st.divider()

# ====== 详细信息 ======
cA, cB = st.columns(2)

with cA:
    st.subheader("status.json（原始内容）")
    if status:
        st.json(status)
    else:
        st.info(f"未读取到：{STATUS_PATH}")

with cB:
    st.subheader("最近日志（尾部）")
    if lines:
        st.code("\n".join(lines), language="text")
    else:
        st.info(f"未读取到：{LOG_PATH}")
