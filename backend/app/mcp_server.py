"""
LLMOps 评测平台 MCP Server（streamable-http transport）。

把评测平台的核心能力以 MCP tools 形式暴露，任意 MCP Host（例如 mypaperweb
的多智能体 supervisor）都能在运行时发现并调用，形成 **Agent 自助评测闭环**：

    list_eval_datasets → start_eval_run → wait_eval_run →
    get_run_failures / compare_runs → Agent 汇报评测结论

部署形态：docker compose 中独立的 ``mcp`` 服务（与 backend 同镜像），
uvicorn 暴露 9000 端口，MCP 端点为 ``http://<host>:9000/mcp``。
本进程通过 HTTP 调用 backend REST API（``LLMOPS_API_BASE``），不直连数据库，
与 web/worker 的鉴权、SSRF 校验等逻辑完全复用。

工具返回值均为 JSON 字符串（LangChain StructuredTool 会作为文本喂给 LLM）。
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request

from mcp.server.mcpserver import MCPServer

# ── 配置（compose 注入）────────────────────────────────────────────
API_BASE = os.getenv("LLMOPS_API_BASE", "http://localhost:8000").rstrip("/")
# Agent 不指定 target_url 时，平台默认评测"Agent 自己"（mypaperweb 标准端点）。
DEFAULT_TARGET_URL = os.getenv(
    "LLMOPS_DEFAULT_TARGET_URL",
    "http://host.docker.internal:8080/evaluation/target",
)
DEFAULT_CONCURRENCY = int(os.getenv("LLMOPS_DEFAULT_CONCURRENCY", "4"))
TARGET_TIMEOUT_S = int(os.getenv("LLMOPS_TARGET_TIMEOUT_S", "300"))

_TERMINAL_STATUSES = {"completed", "failed", "error"}

server = MCPServer("llmops-eval-platform")


# ── HTTP helper ────────────────────────────────────────────────────
def _api(method: str, path: str, payload: dict | None = None, timeout: float = 30.0) -> dict:
    data = (
        json.dumps(payload, ensure_ascii=False).encode("utf-8")
        if payload is not None
        else None
    )
    req = urllib.request.Request(API_BASE + path, data=data, method=method)
    req.add_header("Content-Type", "application/json; charset=utf-8")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read().decode("utf-8")
        return json.loads(body) if body else {}


def _dump(data: dict | list) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2)


def _resolve_dataset_id(dataset: str) -> tuple[int, str]:
    """dataset 参数支持纯 id、精确名称或名称模糊匹配。"""
    keyword = (dataset or "").strip()
    items = _api("GET", "/api/datasets").get("items", [])
    if not keyword:
        raise ValueError("dataset 为空：请先用 list_eval_datasets 查看可用评测集")

    # 纯数字 → 按 id 精确查
    if keyword.isdigit():
        ds_id = int(keyword)
        for item in items:
            if item["id"] == ds_id:
                return ds_id, item.get("name", str(ds_id))
        raise ValueError(f"未找到 id={ds_id} 的评测集")

    # 精确名称优先
    for item in items:
        if item.get("name") == keyword:
            return item["id"], item["name"]
    # 子串模糊匹配（唯一命中才接受，避免歧义）
    matches = [item for item in items if keyword.lower() in (item.get("name") or "").lower()]
    if len(matches) == 1:
        return matches[0]["id"], matches[0]["name"]
    if len(matches) > 1:
        names = ", ".join(f"{m['id']}:{m.get('name')}" for m in matches[:8])
        raise ValueError(f"关键词 '{keyword}' 匹配到多个评测集，请用更精确的名称或 id：{names}")
    raise ValueError(f"未找到名称包含 '{keyword}' 的评测集，请用 list_eval_datasets 查看")


def _run_summary(run: dict) -> dict:
    return {
        "run_id": run.get("id"),
        "name": run.get("name"),
        "status": run.get("status"),
        "avg_score": run.get("avg_score"),
        "passed_cases": run.get("passed_cases"),
        "failed_cases": run.get("failed_cases"),
        "total_cases": run.get("total_cases"),
        "avg_latency_ms": run.get("avg_latency_ms"),
    }


# ── MCP tools ──────────────────────────────────────────────────────
@server.tool()
def list_eval_datasets(keyword: str = "") -> str:
    """列出评测平台上的评测集（dataset），返回每个评测集的 id、名称、用例总数与
    用例类型分布。keyword 非空时按名称模糊过滤。发起评测前先用本工具确认目标
    评测集（例如名称含 qa-expanded 的是问答评测集）。"""
    items = _api("GET", "/api/datasets").get("items", [])
    if keyword:
        items = [d for d in items if keyword.lower() in (d.get("name") or "").lower()]

    result = []
    for item in items:
        ds_id = item["id"]
        try:
            # cases 接口默认分页 limit=100，评测集可能超过 100 条，显式放大
            cases_resp = _api("GET", f"/api/datasets/{ds_id}/cases?limit=1000", timeout=30)
            cases = cases_resp.get("items", [])
        except Exception:  # noqa: BLE001
            cases = []
        type_dist: dict[str, int] = {}
        for case in cases:
            type_dist[case.get("case_type", "?")] = type_dist.get(case.get("case_type", "?"), 0) + 1
        result.append(
            {
                "id": ds_id,
                "name": item.get("name"),
                "description": (item.get("description") or "")[:120],
                "case_count": len(cases),
                "case_types": type_dist,
            }
        )
    return _dump({"datasets": result, "count": len(result)})


@server.tool()
def start_eval_run(
    dataset: str,
    target_url: str = "",
    concurrency: int = 0,
    run_name: str = "",
) -> str:
    """对指定评测集发起一次评测 run（异步执行，立即返回 run_id）。

    参数：
    - dataset：评测集 id 或名称（支持模糊匹配，如 "qa-expanded"）。
    - target_url：被评系统的标准评测端点；**留空时默认评测本助手自己**
      （mypaperweb 的 /evaluation/target 端点）。
    - concurrency：并发数，默认 4；研究类任务建议 2-3。
    - run_name：可选，便于在平台上识别本次 run。

    拿到 run_id 后，用 wait_eval_run 等待完成，再用 get_run_failures 看明细。"""
    ds_id, ds_name = _resolve_dataset_id(dataset)
    body = {
        "name": run_name or f"mcp-selfeval-{ds_name}-{int(time.time())}",
        "dataset_id": ds_id,
        "concurrency": concurrency or DEFAULT_CONCURRENCY,
        "target_url": target_url or DEFAULT_TARGET_URL,
        "target_type": "rag",
        "target_timeout": TARGET_TIMEOUT_S,
    }
    run = _api("POST", "/api/runs", body, timeout=30)
    return _dump(
        {
            "started": True,
            "run_id": run.get("id"),
            "dataset_id": ds_id,
            "dataset_name": ds_name,
            "target_url": body["target_url"],
            "concurrency": body["concurrency"],
            "hint": "评测异步执行中（通常数分钟）。请反复调用 wait_eval_run 等待完成，不要重复发起。",
        }
    )


@server.tool()
def get_eval_run(run_id: int) -> str:
    """查询某次评测 run 的实时状态与总体分数：status（pending/running/completed/
    failed/error）、avg_score（0-1 平均分）、passed_cases/failed_cases。"""
    run = _api("GET", f"/api/runs/{run_id}", timeout=15)
    return _dump(_run_summary(run))


@server.tool()
def wait_eval_run(run_id: int, max_wait_seconds: int = 180) -> str:
    """阻塞等待评测 run 结束（单次调用最多等待 max_wait_seconds，默认 180 秒；
    内部每 5 秒轮询一次）。若返回的 status 仍是 running/pending，说明评测尚未
    完成，请原样再次调用本工具继续等待（run_id 不变），不要重新发起 run。
    完成后返回总体分与通过情况。"""
    deadline = time.time() + max(10, max_wait_seconds)
    last: dict = {}
    while time.time() < deadline:
        last = _api("GET", f"/api/runs/{run_id}", timeout=15)
        if last.get("status") in _TERMINAL_STATUSES:
            out = _run_summary(last)
            out["finished"] = True
            return _dump(out)
        time.sleep(5)
    out = _run_summary(last)
    out["finished"] = False
    out["hint"] = "仍在评测中，请再次调用 wait_eval_run 继续等待。"
    return _dump(out)


@server.tool()
def get_run_failures(run_id: int, limit: int = 10) -> str:
    """列出 run 中失败（failed）或出错（error/无分数）的用例，包含输入摘要、
    未达标指标及各自分数、用例耗时，用于定位被评系统的薄弱环节。
    limit 控制返回条数（默认 10）。"""
    data = _api("GET", f"/api/runs/{run_id}/results?limit=1000", timeout=30)
    items = data.get("items", [])
    failures = []
    for item in items:
        scores = item.get("scores") or {}
        status = item.get("status")
        if status == "passed" and scores:
            continue
        failing = {
            name: round(detail.get("score", 0), 3)
            for name, detail in scores.items()
            if isinstance(detail, dict) and detail.get("score", 1) < 0.7
        }
        failures.append(
            {
                "case_id": item.get("case_id"),
                "status": status,
                "case_score": round(item.get("score") or 0.0, 3),
                "input": (item.get("input") or "")[:200],
                "weak_metrics": failing,
                "latency_ms": item.get("latency_ms"),
                "error": (item.get("error_message") or "")[:200] or None,
            }
        )
    failures.sort(key=lambda x: (x["status"] != "error", x["case_score"]))
    return _dump(
        {
            "run_id": run_id,
            "total_cases": len(items),
            "failure_count": len(failures),
            "failures": failures[:limit],
        }
    )


@server.tool()
def compare_runs(baseline_run_id: int, candidate_run_id: int) -> str:
    """对比两次评测 run（baseline 为基线、candidate 为新版本），返回平均分变化、
    各指标变化、回退/改进用例数，以及硬回退（hard flip：基线≥0.6 且暴跌≥0.15）
    数量与结论。用于版本回归门禁判断。"""
    data = _api(
        "GET",
        f"/api/runs/compare?run1={baseline_run_id}&run2={candidate_run_id}",
        timeout=30,
    )
    diffs = data.get("metric_diffs", {})
    regressed = data.get("regressed_cases", []) or []
    improved = data.get("improved_cases", []) or []
    hard_flips = [
        c
        for c in regressed
        if (c.get("run1_score") or 0) >= 0.6 and (c.get("delta") or 0) <= -0.15
    ]
    verdict = "regression" if hard_flips or (diffs.get("avg_score") or 0) <= -0.05 else "ok"
    return _dump(
        {
            "baseline_run_id": baseline_run_id,
            "candidate_run_id": candidate_run_id,
            "verdict": verdict,
            "avg_score_delta": diffs.get("avg_score"),
            "avg_latency_ms_delta": diffs.get("avg_latency_ms"),
            "metric_deltas": {
                k: v
                for k, v in diffs.items()
                if k not in ("avg_score", "avg_latency_ms", "avg_tokens")
            },
            "improved_case_count": len(improved),
            "regressed_case_count": len(regressed),
            "hard_flip_count": len(hard_flips),
            "top_regressed": [
                {
                    "case_id": c.get("case_id"),
                    "input": (c.get("input") or "")[:120],
                    "baseline_score": c.get("run1_score"),
                    "candidate_score": c.get("run2_score"),
                    "delta": c.get("delta"),
                }
                for c in regressed[:5]
            ],
            "summary": data.get("summary"),
        }
    )


# uvicorn app.mcp_server:app
# 说明：MCP server 供跨容器的 MCP Host（mypaperweb）经 host.docker.internal 访问，
# mcp 2.x 默认开启的 DNS-rebinding Host 头校验只放行 localhost，会拒绝容器内网
# 主机名。本服务为本地 Docker 开发部署（无公网暴露、平台本身无鉴权），关闭该校验。
_DNS_REBINDING_PROTECTION = os.getenv("LLMOPS_MCP_DNS_REBINDING_PROTECTION", "0") == "1"
from mcp.server.streamable_http import TransportSecuritySettings  # noqa: E402

app = server.streamable_http_app(
    host="0.0.0.0",
    transport_security=TransportSecuritySettings(
        enable_dns_rebinding_protection=_DNS_REBINDING_PROTECTION,
    ),
)
