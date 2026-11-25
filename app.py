"""Streamlit UI for ForgeAI (stretch goal): run the factory and chat with any produced agent.

    .venv/bin/pip install streamlit && .venv/bin/streamlit run app.py
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parent
RUNS = ROOT / "runs"

st.set_page_config(page_title="ForgeAI", layout="wide")
st.title("ForgeAI - the agent that builds agents")
tab_build, tab_chat, tab_report = st.tabs(["Build an agent", "Chat with an agent", "Reports"])

with tab_build:
    brief = st.text_area("Brief (one paragraph)", height=120,
                         placeholder="I need an assistant that answers HR policy questions for shore staff...")
    c1, c2 = st.columns(2)
    docs_dir = c1.text_input("Document folder (path on this machine)", "")
    uploads = c2.file_uploader("...or upload documents", accept_multiple_files=True,
                               type=["pdf", "txt", "md", "csv", "docx", "json"])
    name = st.text_input("Run name", "my_agent")
    iters = st.slider("Max optimisation iterations", 0, 3, 3)
    if st.button("Forge", type="primary", disabled=not brief.strip()):
        if uploads:
            tmp = Path(tempfile.mkdtemp(prefix="forge_docs_"))
            for f in uploads:
                (tmp / f.name).write_bytes(f.getvalue())
            docs_dir = str(tmp)
        if not docs_dir:
            st.error("Give a document folder or upload documents.")
        else:
            out = RUNS / name
            cmd = [sys.executable, str(ROOT / "forge.py"), "--brief", brief, "--docs", docs_dir,
                   "--out", str(out), "--auto", "--max-iters", str(iters)]
            box = st.empty()
            lines = []
            with subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                  cwd=ROOT) as p:
                for line in p.stdout:
                    lines.append(line.rstrip())
                    box.code("\n".join(lines[-40:]))
            if (out / "report.md").exists():
                st.success(f"Done - artifacts in {out}")
                st.markdown((out / "report.md").read_text())

with tab_chat:
    defs = sorted(RUNS.glob("*/agent.json"))
    if not defs:
        st.info("No agents built yet.")
    else:
        choice = st.selectbox("Agent", defs, format_func=lambda p: p.parent.name)
        debug = st.checkbox("Show retrieval / tools")
        key = f"hist_{choice}"
        if key not in st.session_state:
            st.session_state[key] = []

        @st.cache_resource
        def load(path):
            from forgeai.runtime import Agent
            return Agent.load(path)

        agent = load(str(choice))
        st.caption(f"{agent.d.name} - tools: {agent.d.tools or 'none'}")
        for role, msg in st.session_state[key]:
            st.chat_message(role).markdown(msg)
        if q := st.chat_input("Ask something"):
            st.chat_message("user").markdown(q)
            r = agent.ask(q)
            msg = r.answer + (f"\n\n*sources: {', '.join(r.citations)}*" if r.citations else "")
            if debug:
                msg += "\n\n```json\n" + json.dumps({"retrieved": r.retrieved, "tools": r.tool_calls,
                                                      "refused": r.refused, "gate": r.gate}, indent=1) + "\n```"
            st.chat_message("assistant").markdown(msg)
            st.session_state[key] += [("user", q), ("assistant", msg)]

with tab_report:
    reps = sorted(RUNS.glob("*/report.md"))
    if reps:
        rp = st.selectbox("Run", reps, format_func=lambda p: p.parent.name)
        sc = rp.parent / "scorecard.md"
        if sc.exists():
            st.markdown(sc.read_text())
        st.markdown(rp.read_text())
    else:
        st.info("No reports yet.")
