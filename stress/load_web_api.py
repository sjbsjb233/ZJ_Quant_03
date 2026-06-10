"""HTTP 压力测试 — 对运行中的 web-backend(:8000)/factor 代理做并发负载.

用法 (先把栈跑起来, 见 stress/README.md 或 stress/run_stress.sh):

    .venv/bin/python stress/load_web_api.py \
        --base http://localhost:8000 --users 30 --duration 30 --scenario mixed

场景:
    read   — 只读混合负载: dashboard / batches / table rows / kline / factors 代理 / logs
    login  — 认证风暴 (bcrypt CPU 路径 + 失败路径)
    mixed  — 90% read + 10% login (默认)

输出每端点的 QPS / 错误数 / p50 / p90 / p95 / p99 / max 延迟;
整体错误率超过 --max-error-rate (默认 1%) 时退出码非零, 可直接做门禁。
"""
from __future__ import annotations

import argparse
import asyncio
import random
import sys
import time
from collections import defaultdict

import httpx

USERS = {
    "admin": "admin123",
    "editor": "editor123",
    "viewer": "viewer123",
}

# (名称, 方法, 路径, 参数) — 全部只读; symbol/limit 随机化避免缓存同质请求
READ_CALLS = [
    ("dashboard", "GET", "/api/v1/data/dashboard/summary", {}),
    ("batches", "GET", "/api/v1/data/batches", {"page": 1, "page_size": 20}),
    ("tables", "GET", "/api/v1/data/tables", {}),
    ("table_rows", "GET", "/api/v1/data/tables/raw.bars_daily/rows", {"symbols": "600519.SH", "limit": 50}),
    ("kline", "GET", "/api/v1/data/charts/kline", {"symbol": "600519.SH", "freq": "1d", "adjust": "none", "limit": 120}),
    ("quality", "GET", "/api/v1/data/quality/issues", {"page": 1, "page_size": 20}),
    ("factors_proxy", "GET", "/api/v1/factors", {}),
    ("logs", "GET", "/api/v1/logs", {"page": 1, "page_size": 20}),
    ("health_deps", "GET", "/api/v1/health/deps", {}),
]
SYMBOLS = ["600519.SH", "000858.SZ", "000568.SZ", "000001.SZ", "600000.SH", "600036.SH", "000002.SZ", "300750.SZ"]


class Stats:
    def __init__(self) -> None:
        self.lat: dict[str, list[float]] = defaultdict(list)
        self.err: dict[str, int] = defaultdict(int)
        self.codes: dict[int, int] = defaultdict(int)

    def add(self, name: str, ms: float, status: int, ok_statuses: tuple[int, ...] = (200,)) -> None:
        self.lat[name].append(ms)
        self.codes[status] += 1
        if status not in ok_statuses:
            self.err[name] += 1

    def report(self, wall_s: float) -> tuple[int, int]:
        total = sum(len(v) for v in self.lat.values())
        errors = sum(self.err.values())
        print(f"\n{'endpoint':<16}{'reqs':>7}{'err':>6}{'p50':>9}{'p90':>9}{'p95':>9}{'p99':>9}{'max':>9}")
        print("-" * 74)
        for name in sorted(self.lat):
            xs = sorted(self.lat[name])
            n = len(xs)
            q = lambda p: xs[min(n - 1, int(p * n))]  # noqa: E731
            print(f"{name:<16}{n:>7}{self.err[name]:>6}"
                  f"{q(0.50):>8.0f}ms{q(0.90):>7.0f}ms{q(0.95):>7.0f}ms{q(0.99):>7.0f}ms{xs[-1]:>7.0f}ms")
        print("-" * 74)
        print(f"total {total} reqs in {wall_s:.1f}s → {total / wall_s:.1f} req/s · "
              f"errors {errors} ({errors / max(total, 1):.2%})")
        print("status codes:", dict(sorted(self.codes.items())))
        return total, errors


async def login(client: httpx.AsyncClient, user: str = "admin") -> str:
    r = await client.post("/api/v1/auth/login", json={"username": user, "password": USERS[user]})
    r.raise_for_status()
    return r.json()["access_token"]


async def worker_read(client: httpx.AsyncClient, token: str, stats: Stats, deadline: float) -> None:
    headers = {"Authorization": f"Bearer {token}"}
    rng = random.Random()
    while time.monotonic() < deadline:
        name, method, path, params = rng.choice(READ_CALLS)
        if name == "kline":
            params = {**params, "symbol": rng.choice(SYMBOLS), "limit": rng.choice([60, 120, 250])}
        t0 = time.perf_counter()
        try:
            r = await client.request(method, path, params=params, headers=headers)
            stats.add(name, (time.perf_counter() - t0) * 1000, r.status_code)
        except httpx.HTTPError:
            stats.add(name, (time.perf_counter() - t0) * 1000, 599)


async def worker_login(client: httpx.AsyncClient, stats: Stats, deadline: float) -> None:
    rng = random.Random()
    users = list(USERS)
    while time.monotonic() < deadline:
        user = rng.choice(users)
        bad = rng.random() < 0.3  # 30% 错误密码, 覆盖失败分支 (确保统一 401 而非 500)
        t0 = time.perf_counter()
        try:
            r = await client.post("/api/v1/auth/login",
                                  json={"username": user, "password": "wrong!" if bad else USERS[user]})
            stats.add("login_bad" if bad else "login_ok", (time.perf_counter() - t0) * 1000,
                      r.status_code, ok_statuses=(401,) if bad else (200,))
        except httpx.HTTPError:
            stats.add("login_bad" if bad else "login_ok", (time.perf_counter() - t0) * 1000, 599)


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base", default="http://localhost:8000")
    ap.add_argument("--users", type=int, default=20, help="并发虚拟用户数")
    ap.add_argument("--duration", type=int, default=30, help="持续秒数")
    ap.add_argument("--scenario", choices=["read", "login", "mixed"], default="mixed")
    ap.add_argument("--max-error-rate", type=float, default=0.01)
    args = ap.parse_args()

    limits = httpx.Limits(max_connections=args.users + 10, max_keepalive_connections=args.users)
    async with httpx.AsyncClient(base_url=args.base, timeout=30.0, limits=limits) as client:
        try:
            r = await client.get("/api/v1/health")
            r.raise_for_status()
        except httpx.HTTPError as e:
            print(f"[stress] backend 不可达: {args.base} ({e}) — 先启动栈, 见 stress/README.md", file=sys.stderr)
            return 2

        token = await login(client)
        stats = Stats()
        deadline = time.monotonic() + args.duration
        n_login = {"read": 0, "login": args.users, "mixed": max(1, args.users // 10)}[args.scenario]
        n_read = args.users - n_login
        print(f"[stress] base={args.base} scenario={args.scenario} "
              f"workers={args.users} (read={n_read}, login={n_login}) duration={args.duration}s")

        t0 = time.monotonic()
        tasks = [asyncio.create_task(worker_read(client, token, stats, deadline)) for _ in range(n_read)]
        tasks += [asyncio.create_task(worker_login(client, stats, deadline)) for _ in range(n_login)]
        await asyncio.gather(*tasks)
        total, errors = stats.report(time.monotonic() - t0)

    rate = errors / max(total, 1)
    if rate > args.max_error_rate:
        print(f"[stress] FAIL: 错误率 {rate:.2%} 超过阈值 {args.max_error_rate:.2%}", file=sys.stderr)
        return 1
    print("[stress] PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
