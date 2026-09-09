"""Phase 6 A/B 成本基准：模型路由 + 语义缓存 off vs on。

用法（宿主机 PowerShell，两栈已启动）：
    # 组 1：路由/缓存关闭（默认态）
    python scripts/ab_cost_benchmark.py run --group off
    # 组 2：路由/缓存开启（脚本自动重建 mypaperweb backend 容器注入开关）
    python scripts/ab_cost_benchmark.py run --group on
    # 汇总对比（读两组 run 的 results，含 target_meta 分流统计与成本估算）
    python scripts/ab_cost_benchmark.py compare --run-off <id1> --run-on <id2>

量化口径：
- 通过率 / 平均分：judge 评分（TaskSuccess 等），两组同 dataset 同 judge。
- tokens：target（mypaperweb）上报的真实用量；语义缓存命中记 0。
- 成本：按 QWEN_PRICE 公示单价（元/1K tokens）× 分流后各模型用量估算。
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time

import requests

sys.stdout.reconfigure(encoding="utf-8")

LLMOPS_BASE = os.getenv("LLMOPS_BASE", "http://localhost:8000/api")
MYPAPER_BASE = "http://localhost:8080"
MYPAPER_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "mypaperweb"))
DATASET_ID = 18  # mypaperweb-qa-expanded（50 条 qa）
TARGET_URL = "http://host.docker.internal:8080/evaluation/target"

# 阿里云百炼公示价（元 / 1K tokens）：(输入, 输出)
# qwen-turbo / qwen-plus / qwen-max 来自百炼选型与定价页；
# qwen3.5-flash 为廉价快速档，调用价按 turbo 同档近似（已在报告中注明）。
QWEN_PRICE: dict[str, tuple[float, float]] = {
    "qwen-turbo": (0.0003, 0.0006),
    "qwen3.5-flash": (0.0003, 0.0006),  # 近似：flash 与 turbo 同为廉价档
    "qwen-plus": (0.0008, 0.0020),
    "qwen-max": (0.0200, 0.0600),
    "cache": (0.0, 0.0),                # 语义缓存命中：不调模型，零成本
}
DEFAULT_PRICE = QWEN_PRICE["qwen3.5-flash"]


def _get(url: str, **kwargs) -> requests.Response:
    resp = requests.get(url, timeout=30, **kwargs)
    resp.raise_for_status()
    return resp


def restart_backend(routing: bool, cache: bool) -> None:
    """通过环境变量 + 容器重建切换 mypaperweb 的 A/B 开关。"""
    env = os.environ.copy()
    env["MODEL_ROUTING_ENABLED"] = "true" if routing else "false"
    env["SEMANTIC_CACHE_ENABLED"] = "true" if cache else "false"
    print(f"[ab] 重启 mypaperweb backend（routing={routing}, cache={cache}）...")
    subprocess.run(
        ["docker", "compose", "up", "-d", "backend"],
        cwd=MYPAPER_DIR, env=env, check=True, capture_output=True,
    )
    for _ in range(60):
        try:
            if requests.get(f"{MYPAPER_BASE}/openapi.json", timeout=2).status_code == 200:
                print("[ab] mypaperweb backend 已就绪")
                return
        except requests.RequestException:
            pass
        time.sleep(2)
    raise RuntimeError("mypaperweb backend 60s 内未就绪")


def wait_run(run_id: int, poll_sec: int = 10, max_sec: int = 3600) -> dict:
    deadline = time.time() + max_sec
    while time.time() < deadline:
        run = _get(f"{LLMOPS_BASE}/runs/{run_id}").json()
        status = run.get("status")
        done = run.get("passed_cases", 0) + run.get("failed_cases", 0)
        print(f"[ab] run {run_id}: status={status} ({done}/{run.get('total_cases')})")
        if status in ("completed", "failed"):
            return run
        time.sleep(poll_sec)
    raise RuntimeError(f"run {run_id} 超时未完成")


def start_group(group: str) -> dict:
    # off：双关基线；on：开路由+缓存（冷启动，填充语义缓存）；
    # warm：不重启，沿用 on 配置再跑一遍（缓存命中，量化缓存收益）
    if group == "off":
        restart_backend(routing=False, cache=False)
    elif group == "on":
        restart_backend(routing=True, cache=True)
    # warm：不重启，容器当前已是 on 配置

    name = f"p6-ab-{group}-qa50"
    payload = {
        "name": name,
        "dataset_id": DATASET_ID,
        "target_url": TARGET_URL,
        "target_type": "qa",
        "target_timeout": 120,
        "concurrency": 5,
    }
    run = requests.post(f"{LLMOPS_BASE}/runs", json=payload, timeout=30)
    run.raise_for_status()
    run = run.json()
    print(f"[ab] 创建 run {run['id']}（{name}）")
    run = wait_run(run["id"])
    print(f"[ab] run {run['id']} 完成: passed={run['passed_cases']}/{run['total_cases']}, "
          f"avg_score={run.get('avg_score')}, avg_tokens={run.get('avg_tokens')}, "
          f"avg_latency={run.get('avg_latency_ms')}ms")
    return run


def collect_results(run_id: int) -> list[dict]:
    resp = _get(f"{LLMOPS_BASE}/runs/{run_id}/results", params={"limit": 500})
    return resp.json().get("items", [])


def cost_of(model: str, input_tokens: int, output_tokens: int) -> float:
    pin, pout = QWEN_PRICE.get(model or "", DEFAULT_PRICE)
    return input_tokens / 1000 * pin + output_tokens / 1000 * pout


def summarize(run: dict, results: list[dict]) -> dict:
    total_in = sum(r.get("input_tokens") or 0 for r in results)
    total_out = sum(r.get("output_tokens") or 0 for r in results)
    total_cost = sum(
        cost_of((r.get("target_meta") or {}).get("model", ""),
                r.get("input_tokens") or 0, r.get("output_tokens") or 0)
        for r in results
    )
    meta_hits = [r for r in results if r.get("target_meta")]
    cache_hits = sum(1 for r in meta_hits if (r.get("target_meta") or {}).get("cache_hit"))
    routed = sum(1 for r in meta_hits if (r.get("target_meta") or {}).get("routed"))
    turbo = sum(1 for r in meta_hits
                if ((r.get("target_meta") or {}).get("model") or "").endswith("turbo"))
    return {
        "run_id": run["id"],
        "name": run.get("name", ""),
        "status": run.get("status"),
        "total": run.get("total_cases"),
        "passed": run.get("passed_cases"),
        "pass_rate": round(run.get("passed_cases", 0) / max(1, run.get("total_cases", 1)) * 100, 1),
        "avg_score": run.get("avg_score"),
        "avg_tokens": run.get("avg_tokens"),
        "avg_latency_ms": run.get("avg_latency_ms"),
        "total_tokens": total_in + total_out,
        "est_cost_yuan": round(total_cost, 4),
        "cache_hits": cache_hits,
        "routed": routed,
        "turbo_served": turbo,
    }


def _load_summary(run_id: int) -> dict:
    return summarize(_get(f"{LLMOPS_BASE}/runs/{run_id}").json(), collect_results(run_id))


def compare(run_off_id: int, run_on_id: int, run_warm_id: int | None = None) -> None:
    groups = [("off 基线", _load_summary(run_off_id))]
    if run_warm_id:
        groups.append(("on 冷启动", _load_summary(run_on_id)))
        groups.append(("on 热缓存", _load_summary(run_warm_id)))
    else:
        groups.append(("on", _load_summary(run_on_id)))

    base = groups[0][1]

    def pct(cur: float, b: float) -> str:
        if not b:
            return "-"
        return f"{(cur - b) / b * 100:+.1f}%"

    print("\n===== Phase 6 A/B：模型路由 + 语义缓存（dataset 18 qa 50 条）=====\n")
    header = f"{'指标':<16}" + "".join(f"{name:>16}" for name, _ in groups)
    print(header)

    # 通过率
    line = f"{'通过率 %':<16}"
    for _, s in groups:
        line += f"{s['pass_rate']:>16.1f}"
    print(line)
    # 平均分
    line = f"{'平均分':<16}"
    for _, s in groups:
        line += f"{(s['avg_score'] or 0):>16.3f}"
    print(line)
    # 平均 tokens
    line = f"{'平均 tokens/条':<16}"
    for _, s in groups:
        line += f"{(s['avg_tokens'] or 0):>16.1f}"
    line += "  | " + " / ".join(
        f"{name}:{pct(s['avg_tokens'] or 0, base['avg_tokens'] or 0)}" for name, s in groups[1:]
    )
    print(line)
    # 总 tokens
    line = f"{'总 tokens':<16}"
    for _, s in groups:
        line += f"{s['total_tokens']:>16}"
    line += "  | " + " / ".join(
        f"{name}:{pct(s['total_tokens'], base['total_tokens'])}" for name, s in groups[1:]
    )
    print(line)
    # 成本
    line = f"{'估算成本(元)':<16}"
    for _, s in groups:
        line += f"{s['est_cost_yuan']:>16.4f}"
    line += "  | " + " / ".join(
        f"{name}:{pct(s['est_cost_yuan'], base['est_cost_yuan'])}" for name, s in groups[1:]
    )
    print(line)
    # 延迟
    line = f"{'平均延迟 ms':<16}"
    for _, s in groups:
        line += f"{(s['avg_latency_ms'] or 0):>16.0f}"
    line += "  | " + " / ".join(
        f"{name}:{pct(s['avg_latency_ms'] or 0, base['avg_latency_ms'] or 0)}" for name, s in groups[1:]
    )
    print(line)
    # 缓存/路由
    line = f"{'缓存命中条数':<16}"
    for _, s in groups:
        line += f"{s['cache_hits']:>16}"
    print(line)
    line = f"{'路由到 turbo':<16}"
    for _, s in groups:
        line += f"{s['turbo_served']:>16}"
    print(line)

    print("\n口径：tokens=目标系统真实上报（char_estimate 同口径，缓存命中记 0）；")
    print("成本=分流后各模型百炼公示单价估算（qwen3.5-flash 按 turbo 同档近似）；")
    print("热缓存组 = 同一数据集第二次跑，语义缓存命中后跳过模型调用。")


def main() -> None:
    parser = argparse.ArgumentParser(description="Phase 6 A/B cost benchmark")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_run = sub.add_parser("run", help="跑一组（off/on/warm）")
    p_run.add_argument("--group", choices=["off", "on", "warm"], required=True)

    p_cmp = sub.add_parser("compare", help="对比各组 run")
    p_cmp.add_argument("--run-off", type=int, required=True)
    p_cmp.add_argument("--run-on", type=int, required=True)
    p_cmp.add_argument("--run-warm", type=int, default=None)

    args = parser.parse_args()
    if args.cmd == "run":
        start_group(args.group)
    else:
        compare(args.run_off, args.run_on, args.run_warm)


if __name__ == "__main__":
    main()
