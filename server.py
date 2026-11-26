#!/usr/bin/env python3
"""ForgeAI web app: JSON API over the factory + the static frontend in web/.

    .venv/bin/python server.py            # http://localhost:8765
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

ROOT = Path(__file__).resolve().parent
RUNS = ROOT / "runs"
WEB = ROOT / "web"
UPLOADS = ROOT / "uploads"
PORT = int(os.environ.get("PORT", "8765"))
HOST = os.environ.get("HOST", "127.0.0.1")          # render.yaml sets 0.0.0.0
MAX_FILES, MAX_FILE_MB = 12, 8                        # public-hosting limits

app = FastAPI(title="ForgeAI")
_agents: dict = {}
_jobs: dict[str, dict] = {}
_lock = threading.Lock()

STAGES = ["Loading documents", "Intake interview", "Groundability guard", "Generating agent definition",
          "Generating eval set", "Judge calibration + critic-optimizer loop", "Writing artifacts"]


def _read_json(p: Path, default=None):
    try:
        return json.loads(p.read_text())
    except Exception:
        return default


def _run_dir(name: str) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9_\-]{1,60}", name):
        raise HTTPException(400, "bad run name")
    d = RUNS / name
    if not d.is_dir():
        raise HTTPException(404, "run not found")
    return d


@app.get("/api/runs")
def list_runs():
    out = []
    for d in sorted(RUNS.glob("*/")):
        its = _read_json(d / "iterations.json", [])
        agent = _read_json(d / "agent.json", {}) or _read_json(d / "agent_v0.json", {}) or {}
        final = None
        if its:
            kept = [i for i in its if i.get("kept", True)]
            final = kept[-1]["summary"] if kept else its[0]["summary"]
        out.append({
            "name": d.name,
            "agent": agent.get("name"),
            "brief": (d / "brief.txt").read_text().strip() if (d / "brief.txt").exists() else "",
            "tools": agent.get("tools", []),
            "score": final and final["overall"], "holdout": final and final["holdout"],
            "passed": final and final["passed"], "total": final and final["total"],
            "baseline": its[0]["summary"]["overall"] if its else None,
            "complete": (d / "report.md").exists(),
            "running": d.name in _jobs and _jobs[d.name]["proc"].poll() is None,
            "mtime": (d / "run.log").stat().st_mtime if (d / "run.log").exists() else 0,
        })
    out.sort(key=lambda r: -r["mtime"])
    return out


@app.get("/api/runs/{name}")
def get_run(name: str):
    d = _run_dir(name)
    evals = _read_json(d / "evals.json", {}) or {}
    return {
        "name": name,
        "brief": (d / "brief.txt").read_text().strip() if (d / "brief.txt").exists() else "",
        "requirements": _read_json(d / "requirements.json"),
        "agent_v0": _read_json(d / "agent_v0.json"),
        "agent": _read_json(d / "agent.json"),
        "evals": evals.get("cases", []),
        "eval_stats": evals.get("stats", {}),
        "iterations": _read_json(d / "iterations.json", []),
        "transcripts": _read_json(d / "final_transcripts.json", []),
        "calibration": _read_json(d / "judge_calibration.json"),
        "usage": _read_json(d / "usage.json"),
        "report_md": (d / "report.md").read_text() if (d / "report.md").exists() else "",
    }


class Ask(BaseModel):
    question: str


@app.post("/api/runs/{name}/chat")
def chat(name: str, body: Ask):
    d = _run_dir(name)
    if not (d / "agent.json").exists():
        raise HTTPException(409, "this run has not produced an agent yet")
    from forgeai.runtime import Agent
    key = (name, (d / "agent.json").stat().st_mtime)
    with _lock:
        if key not in _agents:
            _agents[key] = Agent.load(d / "agent.json")
        agent = _agents[key]
    t0 = time.time()
    r = agent.ask(body.question.strip()[:1000])
    snippets = {h.chunk.id: h.chunk.text for h in agent.index.search(body.question, k=12)}
    out = r.to_dict()
    out["latency_ms"] = int((time.time() - t0) * 1000)
    out["sources"] = [{"id": c, "text": snippets.get(c, next((x["result"] for x in r.tool_calls
                                                              if f"tool:{x['tool']}" == c), ""))[:600]}
                      for c in r.citations]
    return out


@app.post("/api/forge")
async def forge(brief: str = Form(...), name: str = Form(...), max_iters: int = Form(3),
                files: list[UploadFile] = File(...)):
    if any(j["proc"].poll() is None for j in _jobs.values()):
        raise HTTPException(429, "Another agent is being built right now - please try again in a few minutes.")
    if len(files) > MAX_FILES:
        raise HTTPException(400, f"Upload at most {MAX_FILES} documents.")
    if len(brief) > 4000:
        raise HTTPException(400, "Keep the brief under 4000 characters.")
    name = re.sub(r"[^A-Za-z0-9_\-]+", "_", name.strip())[:50].strip("_") or "agent"
    if (RUNS / name).exists() or name in _jobs:
        name = f"{name}_{int(time.time()) % 100000}"
    docs = UPLOADS / name
    docs.mkdir(parents=True, exist_ok=True)
    n = 0
    for f in files:
        fname = Path(f.filename or "").name
        if not fname or fname.startswith("."):
            continue
        data = await f.read()
        if len(data) > MAX_FILE_MB * 1024 * 1024:
            raise HTTPException(400, f"{fname} is larger than {MAX_FILE_MB} MB.")
        (docs / fname).write_bytes(data)
        n += 1
    if not n:
        raise HTTPException(400, "upload at least one document")
    cmd = [sys.executable, str(ROOT / "forge.py"), "--brief", brief, "--docs", str(docs),
           "--out", str(RUNS / name), "--auto", "--max-iters", str(max(0, min(3, max_iters)))]
    proc = subprocess.Popen(cmd, cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    _jobs[name] = {"proc": proc, "started": time.time()}
    return {"name": name}


@app.get("/api/jobs/{name}")
def job(name: str):
    log_path = RUNS / name / "run.log"
    lines = log_path.read_text().splitlines() if log_path.exists() else []
    j = _jobs.get(name)
    running = bool(j and j["proc"].poll() is None)
    stage = 0
    for line in lines:
        m = re.match(r"\[(\d)/7\]", line)
        if m:
            stage = int(m.group(1))
    error = ""
    if j and not running and j["proc"].returncode:
        error = (j["proc"].stderr.read() if j["proc"].stderr else "")[-1500:]
    return {"name": name, "running": running, "stage": stage, "stages": STAGES,
            "done": (RUNS / name / "report.md").exists(), "error": error,
            "elapsed": int(time.time() - j["started"]) if j else None, "log": lines[-400:]}


@app.get("/api/health")
def health():
    from forgeai import llm
    models = llm.available_models()
    return {"provider": llm.PROVIDER, "reachable": bool(models), "ollama": bool(models),
            "gen_model": llm.GEN_MODEL, "judge_model": llm.JUDGE_MODEL,
            "models_ready": all(m in models for m in (llm.GEN_MODEL, llm.JUDGE_MODEL)),
            "missing": [m for m in (llm.GEN_MODEL, llm.JUDGE_MODEL) if m not in models],
            "available": sorted(models) if llm.PROVIDER == "openai" else []}


app.mount("/assets", StaticFiles(directory=WEB), name="assets")


@app.get("/{path:path}")
def spa(path: str):
    return FileResponse(WEB / "index.html")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=HOST, port=PORT, log_level="warning")
