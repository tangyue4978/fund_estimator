import json
from pathlib import Path
from datetime import datetime

import streamlit as st

# =========
# 路径兜底：从当前文件定位到项目根目录
# app/pages/00_Collector_Status.py -> parents[2] = 项目根目录
# =========
BASE_DIR = Path(__file__).resolve().parents[2]

LOG_PATH = BASE_DIR / "storage" / "logs" / "collector.log"
STATUS_PATH = BASE_DIR / "storage" / "status" / "collector_status.json"

st.set_page_config(page_title="采集器状态", layout="wide")
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
