from __future__ import annotations

# GSZ 活跃度判定阈值：超过多少秒未更新，认为停更（交易时段）
GSZ_STALE_SECONDS = 5 * 60  # 5分钟

# 建议刷新间隔（秒）
REFRESH_SEC_HIGH_CONF = 10
REFRESH_SEC_MED_CONF = 20
REFRESH_SEC_LOW_CONF = 30
REFRESH_SEC_FROZEN = 60

# 交易时段（简化版，后续会在 trade_calendar 里完善）
# 这里先按 A 股常见时间段：09:30-11:30，13:00-15:00
TRADING_SESSIONS = [
    ("09:30", "11:30"),
    ("13:00", "15:00"),
]

# 数据源开关：True=真实接口，False=mock（断网/被限流时建议关）
USE_REAL_DATASOURCE = True

# 网络超时（秒）
HTTP_TIMEOUT_SEC = 6

# HTTP 缓存 TTL（秒）
HTTP_CACHE_TTL_SEC = 60

# 是否保存原始响应到 data/raw
SAVE_RAW_HTTP = True

# 重试次数（requests adapter）
HTTP_RETRIES = 3

# Multi-source cross-check (holdings estimate vs GSZ estimate)
ENABLE_MULTI_SOURCE_CROSSCHECK = True
CROSSCHECK_WARN_DIFF_PCT = 1.2
CROSSCHECK_SEVERE_DIFF_PCT = 2.5
