#!/usr/bin/env python3
"""Export the web app + all recorded runs as a static site (docs/) for GitHub Pages.

    .venv/bin/python export_static.py

The hosted page shows every dashboard, exam case, repair log, agent definition and report.
Chat replays the agent's real recorded answers; live answers and new builds need the local app.
"""
import json
import shutil
from pathlib import Path

import server
from forgeai.ingest import chunk_documents, load_folder
from forgeai.models import AgentDefinition
from forgeai.runtime import resolve_docs

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "docs"


def main():
    if OUT.exists():
        shutil.rmtree(OUT)
    (OUT / "data" / "runs").mkdir(parents=True)
    shutil.copytree(ROOT / "web", OUT / "assets")
    (OUT / "assets" / "index.html").unlink()
    html = (ROOT / "web" / "index.html").read_text()
    html = html.replace('<script src="assets/app.js">', '<script>window.FORGE_STATIC = true;</script>\n<script src="assets/app.js">')
    (OUT / "index.html").write_text(html)
    (OUT / ".nojekyll").write_text("")

    runs = [r for r in server.list_runs() if r["complete"]]
    for r in runs:
        r["running"] = False
    (OUT / "data" / "runs.json").write_text(json.dumps(runs))
    for r in runs:
        data = server.get_run(r["name"])
        # Text of every cited excerpt, so the hosted chat can show sources without the documents.
        chunks = {}
        if data.get("agent"):
            d = AgentDefinition.model_validate(data["agent"])
            docs = load_folder(resolve_docs(d.docs_dir))
            by_id = {c.id: c.text for c in chunk_documents(docs, d.retrieval.chunk_size, d.retrieval.overlap)}
            for t in data["transcripts"]:
                for c in t.get("citations", []):
                    if c in by_id:
                        chunks[c] = by_id[c][:900]
            # never publish absolute local paths
            if Path(d.docs_dir).is_absolute():
                data["agent"]["docs_dir"] = f"demos/.../{Path(d.docs_dir).name}"
            if data.get("agent_v0"):
                data["agent_v0"]["docs_dir"] = data["agent"]["docs_dir"]
        data["chunks"] = chunks
        (OUT / "data" / "runs" / f"{r['name']}.json").write_text(json.dumps(data))
        print(f"exported {r['name']}: {len(data['evals'])} cases, {len(chunks)} cited passages")
    print(f"static site written to {OUT}")


if __name__ == "__main__":
    main()
