# ZJ Quant — 压力测试

对真实服务栈（web-backend `:8000` + factor-api `:8100`，与 e2e 共用 `e2e/.stack/` 演示仓库）
做并发负载，测量每端点延迟分位数与错误率。

## 一键运行

```bash
bash stress/run_stress.sh --users 30 --duration 30 --scenario mixed
```

脚本会自动拉起（或复用已在运行的）后端栈，压测完成后清理自己启动的进程。

## 手动运行

```bash
# 栈已在运行时 (e2e 套件或 docker compose 起的):
.venv/bin/python stress/load_web_api.py --base http://localhost:8000 \
    --users 50 --duration 60 --scenario read
```

## 场景

| 场景 | 内容 |
| --- | --- |
| `read` | 只读混合：dashboard 汇总、批次分页、表行浏览（DuckDB 真实查询）、K 线（随机标的/长度）、质量问题、因子代理（→factor-api）、日志中枢、健康聚合 |
| `login` | 认证风暴：bcrypt 验证 CPU 路径 + 30% 错误密码（验证失败路径稳定返回 401 而非 500） |
| `mixed` | 90% read + 10% login（默认） |

## 门禁

整体错误率（非预期状态码 + 网络异常）超过 `--max-error-rate`（默认 1%）时
退出码非零，可直接接入 CI / 发布前检查。

> 注意：压测会向 `e2e/.stack/` 的 app_log 写入请求日志，属正常现象；
> 删除 `e2e/.stack/` 可重置。
