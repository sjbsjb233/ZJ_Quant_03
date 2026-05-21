# ZJ Quant · 多因子量化交易系统

ZJ Quant 是一个 monorepo，采用 **packages（库）+ services（服务）** 的双层结构：

- **packages/** — 业务能力库，各自是独立的 Python distribution（有自己的 `pyproject.toml`、可单独发版/打 wheel），可被任何调用方（web、CLI、Notebook）直接 `import`。
- **services/** — 调度 / 服务层，组合 packages 对外提供 UI 或接口，本身不拥有业务逻辑。依赖关系单向：services → packages。

```
services/                 把库的能力包成可运行的服务
  web/        ──┐         FastAPI + React（quant-web）
                │ pip install quant-data / from quant_data import ...
packages/     ←─┘         业务能力库，也能被 Notebook / CLI 直接 import
  quant_data/             数据层（P0，已交付）
  quant_factor/           因子库（P1，预留）
  quant_strategy/         策略 & 回测库（P2，预留）
```

## 仓库布局

| 路径 | 包名 | 状态 | 说明 |
|---|---|---|---|
| `packages/quant_data/` | `quant-data` | ✅ P0，已交付 | 数据层：raw/reference/metadata 治理，append-only + current-view，PIT，字段可扩展 |
| `packages/quant_factor/` | `quant-factor` | 🚧 P1，预留 | 因子库 |
| `packages/quant_strategy/` | `quant-strategy` | 🚧 P2，预留 | 策略 & 回测库 |
| `services/web/` | `quant-web` | ✅ P0，已交付 | Web 监控管理平台（FastAPI + React，含监控告警视图） |

每个 package 用 `src/<pkg>/` layout：开发时必须先 `pip install -e .`，杜绝"漏装却误用工作树"的问题。未来还可加入 `services/scheduler/`、`services/cli/` 等。

## 快速上手

```bash
git clone <repo>
cd ZJ_Quant_03
bash scripts/setup_env.sh        # 建 .venv（Python 3.13）并按依赖顺序装齐所有包
source .venv/bin/activate
```

`setup_env.sh` 会调用 `scripts/install_dev.sh`，**先装 `packages/*` 再装 `services/*`**（editable），
这样 `quant-web` 声明的 `quant-data>=0.2.0` 依赖会从本地工作树解析，而不是去 PyPI 找。

各模块详细启动指南：

- Web 平台：[services/web/README.md](services/web/README.md)
- 数据层：[packages/quant_data/README.md](packages/quant_data/README.md)

## 跑测试

每个包/服务自带测试目录，可单独跑：

```bash
cd packages/quant_data && pytest          # 数据层测试（169 个，不含 perf）
cd services/web        && pytest          # web API 测试（17 个）
```

或一次跑全部：

```bash
pytest packages services
```
