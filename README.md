# ZJ Quant · 多因子量化交易系统

A monorepo for a multi-factor quantitative trading system. It is organised as **one shared core
data layer** plus **per-feature apps** — matching the four-module split in the design doc (§1.3:
数据管理 / 因子追踪 / 策略&回测 / 监控告警). This period delivers the data-management app (P0).

## 仓库布局

```
ZJ_Quant_03/
├── packages/
│   └── quant_data/          # 核心数据层(append-only + current-view, PIT, 复权, 字段注册表…),被所有 app 共享
├── apps/
│   ├── web/                 # ✅ Web 监控管理平台(本期交付,P0)
│   │   ├── backend/quant_web/   # FastAPI
│   │   ├── frontend/            # Vite + React SPA (V3 Terminal)
│   │   ├── Dockerfile · docker-compose.yml · .env.example · README.md
│   ├── factor/              # 🚧 因子追踪(P1,预留)
│   ├── strategy/            # 🚧 策略 & 回测(P2,预留)
│   └── monitor/             # 🚧 监控告警(P3,预留)
├── tests/                   # pytest(数据层 + web API)
├── scripts/setup_env.sh     # per-worktree venv 引导
└── pyproject.toml           # 单一根级工程定义(packages.find 指向 packages/ 与 apps/web/backend)
```

Python package names (`quant_data`, `quant_web`) are **unchanged**, so `pip install -e .` and all
`import quant_data` / `from quant_web... import …` keep working regardless of on-disk location.

## 各模块入口

| 模块 | 文档 | 状态 |
|---|---|---|
| Web 平台 | [apps/web/README.md](apps/web/README.md) | ✅ 已交付 |
| 因子追踪 | [apps/factor/README.md](apps/factor/README.md) | 🚧 预留 (P1) |
| 策略 & 回测 | [apps/strategy/README.md](apps/strategy/README.md) | 🚧 预留 (P2) |
| 监控告警 | [apps/monitor/README.md](apps/monitor/README.md) | 🚧 预留 (P3) |

## 开发环境

```bash
# per-worktree 隔离 venv(Python 3.13);scripts/setup_env.sh 在 worktree 创建时自动运行
pip install -e ".[web,test]"     # 从仓库根安装核心层 + web 后端 + 测试依赖
```

## 运行 Web 平台

见 [apps/web/README.md](apps/web/README.md)。最快路径:

```bash
cd apps/web
docker compose run --rm web python -m quant_web.seed_demo
docker compose up        # http://localhost:8000 · admin / admin123
```

## 测试

```bash
pytest                   # 186 通过(quant_data 169 + quant_web API 17),从仓库根运行
```
