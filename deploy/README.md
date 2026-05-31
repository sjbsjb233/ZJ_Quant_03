# ZJ Quant 部署目录 (`deploy/`)

> 容器化重构 V3 · 五容器拓扑 · **统一编排 + 存储统一**。
> 远程服务器只需维护本目录的 `docker-compose.yml` + `.env` 两个文件;所有持久化
> 都落在本目录下的 bind 子目录里 —— **备份/迁移 = 拷一个 `deploy/` 目录**。

## 拓扑

```
浏览器 → web-frontend(nginx :80) ──反代 /api──▶ web-backend(:8000)
web-backend ──写转发──▶ data-keeper(:8300)          [KEEPER_BASE]   ← 唯一写者 /data:rw
web-backend ──因子查询──▶ factor-api(:8100)          [FACTOR_API_BASE]
data-keeper / factor-* ──日志上报──▶ web-backend /api/v1/logs/ingest [LOG_INGEST_TOKEN]
factor-worker ──轮询──▶ factor.db (与 factor-api 共享 ./factor-store)
```

- **读 = 库**：web-backend / factor-* 挂 `/data:ro` 直读 parquet。
- **写 = 服务**：只有 data-keeper 挂 `/data:rw` (全系统唯一写者)。
- **日志中枢**：app_log / audit 只被 web-backend 写 (`./state`);各服务本地 JSONL 兜底写各自 `./logs/<svc>`。

## 部署目录最终长相 (完全自包含)

```
deploy/
├── docker-compose.yml          # 唯一权威编排 (纯镜像拉取)
├── docker-compose.dev.yml      # dev 叠加层 (本地 build: + 端口直连)
├── .env                        # JWT_SECRET / LOG_INGEST_TOKEN / *_IMAGE ... (不入库)
├── data/                       # 数据湖 (raw/reference/metadata/derived)
├── state/                      # quant_web_audit.db(含 app_log) · duck_tmp/ · logs/
├── keeper-store/               # keeper.db (+ -wal/-shm)
├── factor-store/               # factor.db · evaluations/ runs/ library/ inbox/ correlations/
└── logs/
    ├── keeper/                 # data 层本地 JSONL: data-YYYY-MM-DD.log
    └── factor/                 # 因子层本地 JSONL: factor-YYYY-MM-DD.log
```

> `data/ state/ logs/ keeper-store/ factor-store/` 这些 bind 目录在首次 `up` 时由
> Docker 自动创建 (宿主无则建空目录,root 属主)。

## 首次启动

```bash
cd deploy
cp .env.example .env && vi .env          # 配 JWT_SECRET / LOG_INGEST_TOKEN / *_IMAGE 地址

# 重建演示世界 + 演示用户 (可选,仅 demo/本地)
docker compose run --rm data-keeper python -m keeper_service.seed_demo
docker compose run --rm web-backend python -m quant_web.seed_users

docker compose up -d
open http://localhost:${WEB_PORT:-80}     # admin / admin123
```

## 日常更新 (GHCR 镜像)

```bash
docker compose pull && docker compose up -d
# 或生产自动滚更 (watchtower profile)
docker compose --profile production up -d
```

## 本地构建 (无 GHCR / 改了源码)

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml up --build
# 仅起因子栈调试 (取代旧 apps/factor/docker-compose.factor.yml)
docker compose -f docker-compose.yml -f docker-compose.dev.yml up --build factor-api factor-worker
```

## 备份 (先停栈让 SQLite WAL checkpoint 合并)

```bash
docker compose down                       # 别加 -v
tar czf zj-quant-$(date +%F).tgz .        # 整个 deploy/ 自包含
```

## ⚠️ 命名卷 → bind 一次性迁移 (老部署升级,勿丢数据)

老版本把 `keeper.db` / `factor.db` 放在 Docker **命名卷** (`keeper_store` / `factor_store`)。
本版本改为部署目录下的 `./keeper-store` / `./factor-store` **bind**。升级时按下述步骤把
旧卷内容倒进新 bind 目录 (卷名前缀以 `docker volume ls` 实际为准,固定 `name: zj-quant`
后恒为 `zj-quant_*`)：

```bash
cd deploy
docker compose down                       # 停栈,别加 -v (保留旧卷)
mkdir -p keeper-store factor-store
# 把旧命名卷内容拷进新 bind 目录
docker run --rm -v zj-quant_keeper_store:/from -v "$PWD/keeper-store":/to \
  alpine sh -c 'cp -a /from/. /to/'
docker run --rm -v zj-quant_factor_store:/from -v "$PWD/factor-store":/to \
  alpine sh -c 'cp -a /from/. /to/'
# 换上本目录"全 bind"的新 compose 后:
docker compose up -d
# 核对 keeper-store/keeper.db、factor-store/factor.db 都在且数据连续后,再删旧卷:
#   docker volume rm zj-quant_keeper_store zj-quant_factor_store
```

### SQLite + bind 注意事项 (V3 §2.5 / §6.1)

- **保留 WAL**：`keeper.db` / `factor.db` 是 WAL 模式,有 `-wal` / `-shm` 兄弟文件;
  备份三件套一起拷 (最稳:先 `docker compose down` 让 WAL checkpoint 合并)。
- **别放网络盘** (NFS/CIFS)：SQLite 的 WAL 锁在网络文件系统上不可靠;本地盘 (ext4/xfs) 无问题。
- **属主是 root**：bind 文件在宿主上 root 属主,普通用户 `cat` 需 `sudo`。可接受。

## 红线 (勿违反,否则破坏全链路 trace / 单写者)

- **单写者**：只有 data-keeper 挂 `/data:rw`,其余一律 `:ro`。
- **日志不拆库**：全部汇入 web-backend 的一张 `app_log` 表;分层只做在视图层 + 权限层
  (`LOG_VISIBLE_SOURCES` 按角色收窄 source)。
- **trace 不断**：任何异步入口 (`enqueue`/`submit`) 必须带当前 `trace_id`。
