# Fund Estimator Web

基金组合实时估值与日结分析系统。当前分支保留在线 Web 应用，入口为 Streamlit 页面 `app/Home.py`。

## 功能概览

- 登录与注册：基于 Supabase 用户表，支持浏览器 Cookie 保持登录状态。
- 自选基金：首页展示自选基金实时估值、涨跌幅、置信度和估值提示。
- 持仓管理：按日期回放持仓流水，展示组合总成本、预估市值、预估盈亏和今日预计收益。
- 图片导入持仓：可通过截图识别持仓，支持同步持仓和加减仓两种导入模式。
- 基金详情：查看单只基金实时估值、官方净值走势、实时估值走势、个人收益走势和误差分析。
- 日结台账：生成收盘估算、尝试覆盖官方净值、扫描近 7 天待结算数据。
- 误差分析：按组合和单基金统计估算口径与官方净值之间的误差、命中率和异常阈值。
- 组合分析：独立菜单展示组合曲线、收益归因、目标仓位偏离和数据健康检查。

## 代码结构

- `app/`：Streamlit 页面入口和业务界面。
- `services/`：业务服务层，包含登录、自选、估值、持仓、日结、误差分析、图片识别等逻辑。
- `services/portfolio_analysis_service.py`：组合扩展分析，包含组合曲线、收益归因、目标仓位和健康检查。
- `datasources/`：行情、净值、基金档案、持仓映射等外部或本地数据源适配。
- `domain/`：基金、估值、持仓、流水等核心数据模型。
- `storage/`：本地 JSON 存储路径和读写封装。
- `config/`：刷新频率、行情参数、常量和开关。
- `scripts/`：迁移、修复和演示脚本。

## 本地运行

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m streamlit run app/Home.py
```

## 部署到 Streamlit Community Cloud

1. 推送代码到 GitHub。
2. 在 Streamlit Community Cloud 创建新应用。
3. Main file path 设置为 `app/Home.py`。
4. 在 Streamlit secrets 配置 `SUPABASE_URL` 和 `SUPABASE_KEY`。
5. 如需启用图片识别，额外配置 `GEMINI_API_KEY`，可选配置 `GEMINI_MODEL` 和 `GEMINI_API_BASE_URL`。

## 运行依赖

- Supabase 用于登录、自选同步、持仓流水和日结数据。
- 行情与净值接口由 `datasources/` 下的适配器封装。
- 浏览器刷新后，登录状态通过签名 Cookie 保持，session id 不再放入 URL。
