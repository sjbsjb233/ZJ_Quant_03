# ZJ Quant — Playwright E2E 测试

针对真实三服务开发栈的端到端测试：

- **web-backend** — uvicorn `:8000`（自动 seed 演示仓库 + 演示用户到 `e2e/.stack/`）
- **factor-api** — uvicorn `:8100`（同一演示仓库，自带内置因子 seed）
- **SPA** — vite dev server `:5173`（`/api` 代理到 `:8000`）

三个服务由 `playwright.config.js` 的 `webServer` 自动拉起/复用，无需手工启动。

## 运行

```bash
# 前置：仓库根目录已建好 .venv（install_dev.sh），前端已 npm install
cd e2e
npm install            # 安装 @playwright/test
npx playwright install chromium
npm test               # 全量跑
npx playwright test tests/auth.spec.js   # 单个文件
npm run report         # 查看 HTML 报告
```

## 覆盖范围

| 文件 | 内容 |
| --- | --- |
| `auth.spec.js` | 登录/登出、错误凭据、匿名重定向、viewer/editor/admin 角色门禁 |
| `data-pages.spec.js` | 数据总览、批次列表+详情、数据源、表浏览（真实行数据）、字段、质量、审计 |
| `charts.spec.js` | K 线/市场全景/行业/基本面图表渲染（canvas 真实出图） |
| `factors.spec.js` | 因子库（走 backend→factor-api 代理全链路）、详情、篮子、股票池、任务 |
| `observability.spec.js` | 可观测总览、日志中枢（断言真实 app.startup 日志）、告警、旧路由重定向 |
| `api-contract.spec.js` | HTTP 契约：401 错误包、X-Trace-Id 回显、分页、refresh 流程、token 类型混用拒绝、代理链路、K 线 OHLC |

## 状态目录

`e2e/.stack/`（gitignored）存放演示仓库 / audit.db / factor-store / 日志。
删除该目录可强制下次运行时重新 seed。
