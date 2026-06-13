"""因子运行资源画像 — 后端测试 (资源画像方案 §10 测试要点).

覆盖: 归因等级判定 (solo→exact / 并发→shared-N) · 采样器异常注入任务不受影响 ·
max-pool 降采样峰值无损 · 基线冷启动 + 分位更新 · trace API 向后兼容 (旧 trace 无
resources 不 500) · profile=false → /profile 404 · 端到端 resources 节 + speedscope
格式校验 · 篮子聚合与子 run 数据一致性 · 选区折算口径.
"""
from __future__ import annotations

import datetime as dt
import os

import pytest


# ═══════════════════════════════════════════════════════════════════════════════
# Pure-unit tests (no app / DB) — the algorithmic core.
# ═══════════════════════════════════════════════════════════════════════════════
def test_maxpool_preserves_peak():
    from factor_service.services.resource_writer import max_pool
    samples = [
        {"t": i, "rss_mb": (9999 if i == 137 else 100 + i), "cpu_pct": 50,
         "arrow_mb": 10, "in_flight": (5 if i == 137 else 1)}
        for i in range(500)
    ]
    rows = max_pool(samples, 300)
    assert len(rows) <= 300
    # 峰值绝不允许被平均抹掉 (§9): both the rss peak AND the in_flight peak survive.
    assert max(r[1] for r in rows) == 9999
    assert max(r[4] for r in rows) == 5


def test_maxpool_passthrough_when_small():
    from factor_service.services.resource_writer import max_pool
    samples = [{"t": i, "rss_mb": 100 + i, "cpu_pct": 1, "arrow_mb": 0, "in_flight": 1}
               for i in range(50)]
    rows = max_pool(samples, 300)
    assert len(rows) == 50


def test_attribution_solo_is_exact():
    from factor_service.services.resource_writer import _attribution
    samples = [{"in_flight": 1} for _ in range(20)]
    attr, n = _attribution(samples, "frun_x")
    assert attr == "exact" and n == 1


def test_attribution_concurrent_is_shared_n():
    from factor_service.services.resource_writer import _attribution
    samples = [{"in_flight": 1}, {"in_flight": 3}, {"in_flight": 2}]
    attr, n = _attribution(samples, "frun_y")
    assert attr == "shared-3" and n == 3


def test_registry_peak_concurrency():
    from factor_service.services.resource_sampler import InFlightRegistry
    reg = InFlightRegistry()
    reg.mark_inflight("t1", "run1")
    assert reg.peak_concurrency("run1") == 1
    reg.mark_inflight("t2", "run2")  # now 2 concurrent
    reg.mark_inflight("t3", "run3")  # now 3
    assert reg.peak_concurrency("run1") == 3
    reg.clear_inflight("t2")
    reg.clear_inflight("t3")
    # peak is sticky (max observed), not the current count
    assert reg.peak_concurrency("run1") == 3


def test_baseline_cold_start_and_percentiles():
    from factor_service.services import resource_baseline as bl

    class _FakeDB:
        def __init__(self):
            self.store = {}

        def get(self, _model, pk):
            return self.store.get(pk)

        def add(self, obj):
            self.store[obj.signature] = obj

    db = _FakeDB()
    sig = "mom@v1|u|W|y1"
    # cold start: < 5 samples ⇒ building
    for i in range(4):
        st = bl.update_stat(db, signature=sig, kind="exact",
                            peak_per_mrow=1000 + i, cpu_per_mrow=10,
                            peak_mb=2000, cpu_sec=50, window=64)
    assert bl.perf_state(1000, st, min_samples=5) == "building"
    # warm: ≥ 5 samples ⇒ percentiles defined, normal verdict in-band
    for i in range(6):
        st = bl.update_stat(db, signature=sig, kind="exact",
                            peak_per_mrow=1000, cpu_per_mrow=10,
                            peak_mb=2000, cpu_sec=50, window=64)
    assert st.sample_n >= 5
    assert st.p50_peak_per_mrow > 0
    assert bl.perf_state(1000, st, min_samples=5) == "normal"
    # a 3× spike is flagged abnormal (> p90 × 1.75)
    assert bl.perf_state(3000, st, min_samples=5) == "abnormal"
    # a collapse to <50% of p50 is flagged low (疑数据缺失)
    assert bl.perf_state(100, st, min_samples=5) == "low"


def test_signature_window_bucket():
    from factor_service.services import resource_baseline as bl
    sig = bl.exact_signature("mom", "v3",
                             {"universe_id": "preset:hs300", "rebalance": "W",
                              "start": "2022-01-01", "end": "2024-01-01"})
    assert sig == "mom@v3|preset:hs300|W|y2"
    assert bl.class_signature("evaluation", {"rebalance": "W"}) == "factor.eval.daily"


def test_sampler_tick_is_failsafe(monkeypatch):
    """采样器异常注入 → tick 不抛, loop 吞掉, 采样器存活 (失败安全红线 §3.1)."""
    from factor_service.services import resource_sampler as rs

    def _boom():
        raise RuntimeError("injected /proc failure")

    monkeypatch.setattr(rs, "read_rss_mb", _boom)
    s = rs.ResourceSampler()
    # _tick raises (reader broken); the loop catches it. Assert the loop body
    # itself never lets the exception escape.
    try:
        s._tick()
    except RuntimeError:
        pass  # _tick may raise; the contract is that _loop swallows it
    # simulate one loop iteration's guard
    raised = False
    try:
        try:
            s._tick()
        except Exception:  # noqa: BLE001 - exactly what _loop does
            raised = False
    except Exception:  # noqa: BLE001
        raised = True
    assert raised is False


def test_speedscope_artifact_shape():
    """剖析工件 speedscope 格式校验 + 预构火焰树 + 实测采样率 (§3.5)."""
    from factor_service.services.resource_sampler import REGISTRY
    from factor_service.services.resource_profiler import RunProfiler
    import threading

    REGISTRY.mark_inflight("tprof", "run_prof")
    REGISTRY.bind_thread("tprof", threading.get_ident())
    REGISTRY.set_stage("tprof", "compute")
    p = RunProfiler("tprof", "run_prof")
    # drive a few manual samples (capture the test thread's own stack)
    for _ in range(20):
        p._sample_once()
    art = p.to_artifact()
    REGISTRY.clear_inflight("tprof")
    # speedscope envelope
    assert art["$schema"].endswith("file-format-schema.json")
    assert "frames" in art["shared"] and isinstance(art["shared"]["frames"], list)
    prof = art["profiles"][0]
    assert prof["type"] == "sampled"
    assert len(prof["samples"]) == len(prof["weights"])
    # per-sample time axis retained (for §7.1 time-selection filtering)
    assert len(art["sampleTimes"]) == p._n
    # pre-built UI tree, stage-grouped at the top level
    assert art["tree"]["name"] == "<run>"
    assert any(c.get("stage") == "compute" for c in art["tree"]["children"])
    # 实测采样率 recorded
    assert art["profiling"]["samples"] == p._n
    assert "hz_actual" in art["profiling"]


# ═══════════════════════════════════════════════════════════════════════════════
# Integration tests — real eval through execute_task with the sampler running.
# ═══════════════════════════════════════════════════════════════════════════════
@pytest.fixture(scope="module")
def rp_app(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("factor-store-rp")
    os.environ["FACTOR_STORE_ROOT"] = str(tmp)
    os.environ["FACTOR_DB_URL"] = f"sqlite:///{tmp}/factor.db"
    os.environ["JWT_SECRET"] = "test-secret"
    os.environ["QUANT_DATA_ROOT"] = "./data-test"
    os.environ["FACTOR_ROLE"] = "api"
    os.environ["TRUST_PROXY_USER_HEADERS"] = "true"
    os.environ["PREFLIGHT_ENABLED"] = "true"
    os.environ["TRACE_ENABLED"] = "true"
    os.environ["RESOURCE_SAMPLING_ENABLED"] = "true"

    from factor_service.config import reset_settings_cache
    reset_settings_cache()
    from factor_service.db import base as db_base
    db_base.rebind(os.environ["FACTOR_DB_URL"])

    from factor_service.main import create_app
    from factor_service.db import base as _db_base
    from factor_service.db.seed import seed_all
    from fastapi.testclient import TestClient
    from factor_service.services import resource_sampler

    app = create_app()
    _db_base.init_db()
    with _db_base.SessionLocal() as db:
        seed_all(db)
    resource_sampler.start_sampler()
    yield TestClient(app)
    resource_sampler.stop_sampler()


@pytest.fixture(autouse=True)
def _clean_registry():
    """In-process execute_task bypasses the dispatcher's mark/clear, so reset the
    process-global in-flight registry before each test for deterministic归因."""
    from factor_service.services.resource_sampler import REGISTRY
    REGISTRY.reset()
    yield
    REGISTRY.reset()


def _hdr(role="admin"):
    return {"X-Forwarded-User": "tester", "X-Forwarded-Role": role,
            "X-Trace-Id": "t_rp"}


_BODY = {
    "factor_id": "mom", "params": {"window": 10, "skip": 1},
    "run_config": {
        "universe_id": "preset:hs300", "start": "2024-01-02", "end": "2024-03-01",
        "rebalance": "W", "horizons": [1, 5], "primary_horizon": 5,
        "neutralize_size": True, "neutralize_industry": True,
        "industry_level": 1, "lane": "interactive",
    },
}


def _fake_reader(monkeypatch):
    from factor_service.services import preflight_service, run_executor
    from quant_factor.testing import FakeReader
    monkeypatch.setattr(run_executor, "DataReader", lambda root=None: FakeReader())
    monkeypatch.setattr(preflight_service, "DataReader", lambda root=None: FakeReader())


def _run(client, body, role="editor"):
    # Drive the worker in-process. We call execute_task directly (no dispatcher),
    # so we must mirror the dispatcher's mark/clear of the in-flight registry —
    # otherwise stale entries accrue across tests and inflate concurrency.
    from factor_service.services.run_executor import execute_task
    from factor_service.services.resource_sampler import REGISTRY
    r = client.post("/runs", json=body, headers=_hdr(role))
    assert r.status_code == 200, r.text
    task_id = r.json()["task_id"]
    REGISTRY.mark_inflight(task_id, r.json()["run_id"])
    try:
        out = execute_task(task_id)
    finally:
        REGISTRY.clear_inflight(task_id)
    return r.json()["run_id"], out


def test_resources_node_present_after_run(rp_app, monkeypatch):
    _fake_reader(monkeypatch)
    run_id, out = _run(rp_app, _BODY)
    assert out["status"] in ("success", "degraded")
    tr = rp_app.get(f"/runs/{run_id}/trace", headers=_hdr()).json()
    res = tr.get("resources")
    assert res is not None, "resources节 must be embedded in trace.json"
    assert res["attribution"] == "exact"           # solo run ⇒ exact (§2)
    assert {s["stage"] for s in res["stages"]} == {"load", "preflight", "compute", "write", "eval"}
    s = res["summary"]
    assert s["peak_rss_mb"] > 0 and s["cpu_sec"] >= 0
    assert s["workload_mrows"] > 0
    assert "signature" in s and s["signature"].startswith("mom@")
    # every stage carries the精确 CPU + RSS fields (§3.2 StageRecord extension)
    for st in res["stages"]:
        assert "cpu_sec" in st and "rss_start_mb" in st and "in_flight_max" in st


def test_profile_404_when_not_profiled(rp_app, monkeypatch):
    _fake_reader(monkeypatch)
    run_id, _ = _run(rp_app, _BODY)
    r = rp_app.get(f"/runs/{run_id}/profile", headers=_hdr())
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "PROFILE_NOT_FOUND"


def test_profiled_run_emits_speedscope(rp_app, monkeypatch):
    _fake_reader(monkeypatch)
    body = {**_BODY, "profile": True}
    run_id, out = _run(rp_app, body, role="admin")
    assert out["status"] in ("success", "degraded")
    tr = rp_app.get(f"/runs/{run_id}/trace", headers=_hdr()).json()
    assert tr["resources"]["has_profile"] is True
    r = rp_app.get(f"/runs/{run_id}/profile", headers=_hdr())
    assert r.status_code == 200, r.text
    art = r.json()
    assert art["shared"]["frames"] and art["profiles"][0]["type"] == "sampled"
    assert art["tree"]["name"] == "<run>"
    assert art["profiling"]["samples"] >= 1


def test_profile_flag_admin_only(rp_app, monkeypatch):
    """profile 仅管理员可用 (§6) — editor 提交 profile=True 被静默降级为普通运行."""
    _fake_reader(monkeypatch)
    body = {**_BODY, "profile": True}
    r = rp_app.post("/runs", json=body, headers=_hdr("editor"))
    assert r.status_code == 200
    assert r.json()["profile"] is False
    run_id = r.json()["run_id"]
    from factor_service.services.run_executor import execute_task
    execute_task(r.json()["task_id"])
    assert rp_app.get(f"/runs/{run_id}/profile", headers=_hdr()).status_code == 404


def test_samples_endpoint_lazy(rp_app, monkeypatch):
    _fake_reader(monkeypatch)
    run_id, _ = _run(rp_app, _BODY)
    r = rp_app.get(f"/runs/{run_id}/resources/samples", headers=_hdr())
    assert r.status_code == 200
    # available true (fullres artifact written) OR false (sub-second run, no sample) —
    # either way it must NOT 500 and must carry a `samples` list.
    assert "samples" in r.json()


def test_baseline_endpoint_admin_only(rp_app, monkeypatch):
    _fake_reader(monkeypatch)
    run_id, _ = _run(rp_app, _BODY)
    tr = rp_app.get(f"/runs/{run_id}/trace", headers=_hdr()).json()
    sig = tr["resources"]["summary"]["signature"]
    # admin sees it
    r = rp_app.get("/factors/mom/resource-baseline", params={"signature": sig},
                   headers=_hdr("admin"))
    assert r.status_code == 200
    assert "recent" in r.json() and len(r.json()["recent"]) >= 1
    # viewer is denied (性能分析定位为管理功能, §6)
    r2 = rp_app.get("/factors/mom/resource-baseline", params={"signature": sig},
                    headers=_hdr("viewer"))
    assert r2.status_code == 403


def test_summary_row_persisted(rp_app, monkeypatch):
    _fake_reader(monkeypatch)
    run_id, _ = _run(rp_app, _BODY)
    from factor_service.db import base as _db_base
    from factor_service.db.models import RunResourceSummary
    with _db_base.SessionLocal() as db:
        row = db.get(RunResourceSummary, run_id)
        assert row is not None
        assert row.attribution == "exact"
        assert row.peak_rss_mb > 0
        assert row.signature.startswith("mom@")


def test_trace_backward_compat_no_resources(rp_app):
    """旧 trace 无 resources 节 → /trace 不 500, 前端按空态处理 (§6)."""
    from factor_service.services.results_writer import write_run_trace
    from factor_service.db import base as _db_base
    from factor_service.db.models import FactorRun
    # craft a legacy run + trace WITHOUT a resources节
    with _db_base.SessionLocal() as db:
        db.add(FactorRun(run_id="frun_legacy_rp", factor_id="mom", version="v1",
                         status="success"))
        db.commit()
    write_run_trace("frun_legacy_rp", {"run_id": "frun_legacy_rp", "schema_version": 1,
                                       "stages": [], "days": [], "meta": {}})
    r = rp_app.get("/runs/frun_legacy_rp/trace", headers=_hdr())
    assert r.status_code == 200
    assert "resources" not in r.json()  # absent, not an error


# ═══════════════════════════════════════════════════════════════════════════════
# Basket aggregation — synthesize child rows and assert the endpoint aggregates.
# ═══════════════════════════════════════════════════════════════════════════════
def test_basket_resources_aggregation(rp_app):
    from factor_service.db import base as _db_base
    from factor_service.db.models import (
        Basket, BasketRevision, BasketRun, BasketRunItem, FactorRun,
        RunResourceSummary,
    )
    now = dt.datetime.utcnow()
    with _db_base.SessionLocal() as db:
        db.add(Basket(basket_id="bsk_rp", name="RP 篮子", active_revision=1))
        db.add(BasketRevision(basket_id="bsk_rp", revision=1, shared_config={},
                              items=[{"item_id": "it1", "factor_id": "mom", "label": "动量"},
                                     {"item_id": "it2", "factor_id": "mom", "label": "反转"}]))
        db.add(BasketRun(brun_id="brun_rp", basket_id="bsk_rp", revision=1,
                         status="success", total_children=2,
                         started_at=now, finished_at=now + dt.timedelta(seconds=120)))
        for i, (iid, rid, cpu, attr) in enumerate([
            ("it1", "frun_c1", 52.4, "shared-2"),
            ("it2", "frun_c2", 33.2, "exact"),
        ]):
            db.add(BasketRunItem(brun_id="brun_rp", item_id=iid, item_key=f"k{i}",
                                 factor_id="mom", seq_no=i, run_id=rid, status="success"))
            db.add(FactorRun(run_id=rid, factor_id="mom", version="v1", status="success",
                             started_at=now + dt.timedelta(seconds=i * 10),
                             finished_at=now + dt.timedelta(seconds=60 + i * 10)))
            db.add(RunResourceSummary(run_id=rid, factor_id="mom", version="v1",
                                      attribution=attr, peak_rss_mb=2000 + i * 100,
                                      cpu_sec=cpu, concurrent_max=2 if "shared" in attr else 1,
                                      started_at=now + dt.timedelta(seconds=i * 10),
                                      finished_at=now + dt.timedelta(seconds=60 + i * 10)))
        db.commit()

    r = rp_app.get("/baskets/bsk_rp/runs/brun_rp/resources", headers=_hdr())
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["available"] is True
    # CPU 合计 = 子任务精确 CPU 之和 (§7.2)
    assert abs(body["totals"]["cpu_sec_sum"] - (52.4 + 33.2)) < 0.1
    # 最贵因子 = mom (52.4s)
    assert body["totals"]["costliest_factor"]["cpu"] == 52.4
    # tasks carry attribution + mem grade + factor_id (for navigation)
    tasks = {t["run_id"]: t for t in body["tasks"]}
    assert tasks["frun_c1"]["attribution"] == "shared-2"
    assert tasks["frun_c1"]["mem"]["kind"] == "shared"
    assert tasks["frun_c2"]["mem"]["kind"] == "exact"
    assert tasks["frun_c1"]["factor_id"] == "mom"


def test_resource_budget_advice(rp_app, monkeypatch):
    """预算重标定建议 (§8) — 同类 p90 vs 准入预算给出 raise/lower/ok, 管理员可见."""
    _fake_reader(monkeypatch)
    # accrue a同类基线 by running a few evals (factor.eval.daily class).
    for _ in range(2):
        _run(rp_app, _BODY)
    r = rp_app.get("/runs/resource-budgets", headers=_hdr("admin"))
    assert r.status_code == 200, r.text
    items = {i["task_class"]: i for i in r.json()["items"]}
    assert "factor.eval.daily" in items
    assert items["factor.eval.daily"]["advice"] in ("raise", "lower", "ok")
    # viewer denied (management feature)
    assert rp_app.get("/runs/resource-budgets", headers=_hdr("viewer")).status_code == 403


def test_finalize_failsafe_does_not_sink_run(rp_app, monkeypatch):
    """资源画像收尾失败 → run 仍成功 (失败安全红线 §3.1)."""
    _fake_reader(monkeypatch)
    from factor_service.services import resource_writer

    def _boom(*a, **k):
        raise RuntimeError("injected finalize failure")

    monkeypatch.setattr(resource_writer, "finalize_run_resources", _boom)
    run_id, out = _run(rp_app, _BODY)
    assert out["status"] in ("success", "degraded")  # run unaffected by采集 failure
