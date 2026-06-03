# OOM 审计复现 + 修复验证（2026-06）

针对内存物化/OOM 审计报告,用**生产级数据量**复现各路径是否真的会爆内存,并验证修复。

## 脚本
- `gen_data.py ROOT [N_SYMBOLS] [N_DAYS]` — 生成生产级数据(默认 5000×3700 ≈ **18.5M 行** `raw.bars_daily` + 同量 `universe` + `corporate_actions`,外加 `tushare` 第二源切片用于跨源对比)。向量化写入,生成端内存有界。
- `harness.py [--cap MB] [--only ...]` — 每条可疑读取路径在独立子进程运行,父进程采样子进程 `VmRSS`;**越过 `--cap`(默认 8000MB,建模容器 `mem_limit=8GB`)即 SIGKILL**,记为 `CONTAINER_OOM_SIMULATED`。子进程统一设 `QUANT_DUCKDB_MEMORY_LIMIT=2GB` + `QUANT_DUCKDB_TEMP_DIR`(与生产一致,允许 spill),以证明 OOM 发生在 **to-Python/Arrow 物化**而非引擎执行期。
- `correctness.py` — 小数据上逐字段比对修复前后的输出(explorer 聚合、get_bar_revisions、batch 日期区间),证明修复不改变结果。

## 复现结论(18.5M 行)
| 路径 | 修复前 | 修复后 |
|---|---|---|
| `table_stats`(已部署) / `count_current_view` | 有界 ~4.9GB ✅ | 不变 ✅ |
| `current_view().num_rows`(P0 历史写法) | OOM-kill @8GB 💥 | —(已改 COUNT) |
| explorer 整表物化聚合 | OOM-kill @8GB 💥 | 聚合下推,~5.0GB ✅ |
| `diff_sources` 无日期双 `to_pylist` | OOM-kill @8GB 💥 | 端点强制日期窗+≤90天 ✅ |
| `invariants._Ctx` 同时注册多表 | OOM-kill @8GB 💥 | 预检超阈值 fail-fast ✅ |

> `data/`、`duck_tmp/`、`*.log` 已 gitignore(多 GB,不入库)。复现:
> `python repro/gen_data.py repro/data && python repro/harness.py --cap 8000`
