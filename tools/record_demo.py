"""Record a captioned, silent demo video of the live ForgeAI app (for a voice-over).

    python tools/record_demo.py [base_url] [out_dir]

Drives Chrome with Playwright, injects caption boxes, highlight outlines and a visible cursor,
and writes ForgeAI_demo.mp4. Needs: pip install playwright imageio-ffmpeg (uses installed Chrome).
"""
import subprocess
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "https://forgeai-frk3.onrender.com"
OUT = Path(sys.argv[2] if len(sys.argv) > 2 else "media").resolve()
ROOT = Path(__file__).resolve().parent.parent
W, H = 1440, 900
TOTAL_STEPS = 8

OVERLAY_JS = r"""
(() => {
  if (window.__demo) return;
  const css = document.createElement('style');
  css.textContent = `
  #demo-cap{position:fixed;left:300px;right:40px;bottom:28px;z-index:9999;display:flex;gap:16px;align-items:flex-start;
    background:rgba(7,11,22,.94);color:#eef2ff;border:1px solid rgba(91,133,255,.55);border-left:5px solid #2558f0;
    border-radius:16px;padding:16px 22px;font:500 17px/1.45 Barlow,system-ui,sans-serif;box-shadow:0 20px 50px -20px rgba(0,0,0,.6);
    opacity:0;transform:translateY(12px);transition:opacity .35s,transform .35s}
  #demo-cap.on{opacity:1;transform:none}
  #demo-cap .st{flex:none;font:800 13px/1 Barlow;letter-spacing:.12em;text-transform:uppercase;color:#4cc9ff;
    background:rgba(76,201,255,.12);border-radius:999px;padding:7px 12px;margin-top:2px}
  #demo-cap b{font-weight:800;font-size:20px;display:block;margin-bottom:2px;color:#fff}
  .demo-hl{position:fixed;z-index:9998;border:3px solid #38bdf8;border-radius:14px;pointer-events:none;
    box-shadow:0 0 0 6px rgba(56,189,248,.18),0 0 30px rgba(37,88,240,.45);transition:all .35s;animation:dpulse 1.6s infinite}
  .demo-hl .lbl{position:absolute;top:-34px;left:-3px;background:#2558f0;color:#fff;font:700 13px Barlow;
    padding:5px 10px;border-radius:8px;white-space:nowrap}
  @keyframes dpulse{50%{box-shadow:0 0 0 10px rgba(56,189,248,.08),0 0 40px rgba(37,88,240,.55)}}
  #demo-cur{position:fixed;z-index:10000;width:22px;height:22px;border-radius:50%;background:rgba(37,88,240,.35);
    border:2px solid #fff;box-shadow:0 2px 8px rgba(0,0,0,.4);pointer-events:none;transition:left .55s ease,top .55s ease,transform .15s;
    left:720px;top:450px;transform:translate(-50%,-50%)}
  #demo-cur.click{transform:translate(-50%,-50%) scale(.7);background:rgba(56,189,248,.8)}
  #demo-title{position:fixed;inset:0;z-index:10001;display:grid;place-items:center;text-align:center;
    background:radial-gradient(900px 500px at 50% 30%,#14204a,#05070d);color:#fff;font-family:Barlow,system-ui;transition:opacity .6s}
  #demo-title h1{font:800 76px/1 Barlow;letter-spacing:-.02em;margin:0 0 14px}
  #demo-title p{font:400 24px/1.5 Barlow;color:#a8b3cc;margin:0}
  #demo-title .acc{background:linear-gradient(135deg,#5b85ff,#38bdf8);-webkit-background-clip:text;color:transparent}`;
  document.head.appendChild(css);
  const cap = document.createElement('div'); cap.id = 'demo-cap'; document.body.appendChild(cap);
  const cur = document.createElement('div'); cur.id = 'demo-cur'; document.body.appendChild(cur);
  window.__demo = {
    cap(step, title, body) {
      cap.classList.remove('on');
      setTimeout(() => { cap.innerHTML = (step ? `<span class="st">${step}</span>` : '') + `<div><b>${title}</b>${body || ''}</div>`; cap.classList.add('on'); }, 250);
    },
    hide() { cap.classList.remove('on'); },
    hl(rects) {
      document.querySelectorAll('.demo-hl').forEach(e => e.remove());
      for (const r of rects) {
        const d = document.createElement('div'); d.className = 'demo-hl';
        Object.assign(d.style, {left: (r.x - 8) + 'px', top: (r.y - 8) + 'px', width: (r.w + 16) + 'px', height: (r.h + 16) + 'px'});
        if (r.label) d.innerHTML = `<span class="lbl">${r.label}</span>`;
        document.body.appendChild(d);
      }
    },
    clear() { document.querySelectorAll('.demo-hl').forEach(e => e.remove()); },
    cur(x, y, click) { cur.style.left = x + 'px'; cur.style.top = y + 'px'; if (click) { cur.classList.add('click'); setTimeout(() => cur.classList.remove('click'), 220); } },
    title(h, p) { let t = document.getElementById('demo-title'); if (!t) { t = document.createElement('div'); t.id = 'demo-title'; document.body.appendChild(t); }
      t.style.opacity = 1; t.innerHTML = `<div><h1>${h}</h1><p>${p}</p></div>`; },
    untitle() { const t = document.getElementById('demo-title'); if (t) { t.style.opacity = 0; setTimeout(() => t.remove(), 700); } },
  };
})();
"""


class Demo:
    def __init__(self, page):
        self.p = page

    def ensure(self):
        self.p.evaluate(OVERLAY_JS)

    def wait(self, s):
        self.p.wait_for_timeout(int(s * 1000))

    def cap(self, step, title, body=""):
        self.ensure()
        self.p.evaluate("([s,t,b]) => __demo.cap(s,t,b)", [f"Step {step} / {TOTAL_STEPS}" if step else "", title, body])

    def hl(self, *items):
        """items: (locator, label) pairs; outlines each element on screen."""
        self.ensure()
        rects = []
        for loc, label in items:
            try:
                b = loc.first.bounding_box(timeout=3000)
                if b:
                    rects.append({"x": b["x"], "y": b["y"], "w": b["width"], "h": b["height"], "label": label})
            except Exception:
                pass
        self.p.evaluate("r => __demo.hl(r)", rects)

    def clear(self):
        self.ensure()
        self.p.evaluate("() => __demo.clear()")

    def click(self, loc, pause=0.7):
        self.ensure()
        loc = loc.first
        loc.scroll_into_view_if_needed(timeout=5000)
        b = loc.bounding_box()
        if b:
            x, y = b["x"] + b["width"] / 2, b["y"] + b["height"] / 2
            self.p.evaluate("([x,y]) => __demo.cur(x,y,false)", [x, y])
            self.wait(pause)
            self.p.evaluate("([x,y]) => __demo.cur(x,y,true)", [x, y])
        loc.click()
        self.wait(0.4)

    def scroll_to(self, loc, block="start"):
        loc.first.evaluate(f"e => e.scrollIntoView({{behavior:'smooth', block:'{block}'}})")
        self.wait(1.2)

    def scroll_by(self, dy):
        self.p.evaluate(f"window.scrollBy({{top:{dy}, behavior:'smooth'}})")
        self.wait(1.2)

    def type(self, loc, text, delay=45):
        self.click(loc)
        loc.first.type(text, delay=delay)

    def go(self, hash_):
        self.p.evaluate(f"location.hash = '{hash_}'")
        self.wait(1.6)
        self.p.evaluate("window.scrollTo(0,0)")
        self.ensure()


def record():
    OUT.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel="chrome", headless=True)
        # warm the free Render instance so the recording doesn't show a cold start
        warm = browser.new_page()
        for _ in range(12):
            try:
                if warm.goto(BASE + "/api/runs", timeout=90000).ok:
                    break
            except Exception:
                time.sleep(5)
        warm.close()

        ctx = browser.new_context(viewport={"width": W, "height": H}, record_video_dir=str(OUT),
                                  record_video_size={"width": W, "height": H}, color_scheme="light",
                                  device_scale_factor=1)
        page = ctx.new_page()
        d = Demo(page)
        page.goto(BASE + "/#/", wait_until="networkidle")
        page.wait_for_timeout(1500)

        # ---------- title card
        d.ensure()
        page.evaluate("() => __demo.title(\"Forge<span class='acc'>AI</span>\", 'The agent that builds agents — brief in, tested agent out, with an honest report.')")
        d.wait(5)
        page.evaluate("() => __demo.untitle()")
        d.wait(1)

        # ---------- 1. home
        d.cap(1, "One brief + a folder of documents → a tested agent",
              "ForgeAI interviews, checks the docs can support the brief, writes the agent, writes its own exam, grades and repairs it.")
        d.hl((page.locator(".hero h1"), "What it does"), (page.locator(".hero .cta"), "Start here"))
        d.wait(6)
        d.scroll_to(page.locator(".band"), "center")
        d.hl((page.locator(".band"), "Across all runs"))
        d.cap(1, "Every agent ships with a report card", "Self-generated exam questions, average score — and zero paid APIs needed locally.")
        d.wait(5)
        d.scroll_to(page.locator(".run-card").first, "center")
        d.hl((page.locator(".grid.g3").first, "Agents built so far"))
        d.wait(4)
        d.clear()

        # ---------- 3. overview
        d.go("#/run/ship_spares")
        d.cap(2, "Run overview: the procurement assistant",
              "Final score, a held-out score the repair loop never saw, judge reliability and cost — up front.")
        d.hl((page.locator(".grid.g4"), "Report card"))
        d.wait(7)
        d.scroll_to(page.locator(".pipe"), "start")
        d.hl((page.locator(".pipe"), "What each stage concluded"))
        d.cap(2, "The pipeline at a glance", "Note stage 03: one requested topic — supplier on-time ratings — isn't in any document.")
        d.wait(6)
        d.scroll_by(330)
        d.hl((page.locator(".grid.g2.section").first.locator(".card").first, "Gap → will refuse"),
             (page.locator(".grid.g2.section").first.locator(".card").nth(1), "Only the tools the brief needs"))
        d.cap(2, "Grounded scope and evidence-gated tools",
              "CSV lookup, calculator and date are attached because the brief asks for prices, totals and dispatch dates. Weather is not.")
        d.wait(9)
        d.scroll_by(520)
        d.hl((page.locator(".grid.g2.section").nth(1), "Honest results"))
        d.cap(2, "What still fails — and why", "Each remaining failure is classified. No 100% claimed where it isn't earned.")
        d.wait(7)
        d.clear()

        # ---------- 4. exam
        d.go("#/run/ship_spares/evals")
        d.cap(3, "The exam it wrote for itself",
              "20+ questions from the documents: answerable, multi-document, ambiguous and out-of-scope. Each verified by a second model.")
        d.hl((page.locator(".filters"), "Filter by result, type, split"))
        d.wait(6)
        d.click(page.locator(".seg[data-k='result'] button[data-v='fail']"))
        d.wait(1)
        d.hl((page.locator("table"), "Failed cases"))
        d.wait(3)
        d.click(page.locator("tr[data-id]"))
        d.wait(1)
        d.cap(3, "Open any case to see the evidence",
              "The agent's answer, the judge's verdict on every expected fact (and how it decided), and what retrieval surfaced.")
        d.hl((page.locator(".drawer-panel"), "Case detail"))
        d.wait(7)
        page.locator(".drawer-panel").evaluate("e => e.scrollBy({top: 420, behavior: 'smooth'})")
        d.wait(4)
        d.clear()
        page.keyboard.press("Escape")
        d.wait(0.8)

        # ---------- 5. repairs
        d.go("#/run/selftest_it_helpdesk/optimizer")
        d.cap(4, "The repair loop: classify → targeted fix → re-run",
              "Dev (orange) drives decisions; held-out (blue) is only reported. Hollow points are reverted patches.")
        d.hl((page.locator(".chart"), "Score per iteration"))
        d.wait(8)
        d.scroll_to(page.locator(".iter").nth(1), "start")
        d.hl((page.locator(".iter").nth(1), "Kept: fixed dev cases, broke nothing"))
        d.cap(4, "Every change is logged", "What changed, why, which cases it fixed or broke, score before → after — and whether it was kept.")
        d.wait(8)
        d.scroll_to(page.locator(".diff").first, "center")
        d.hl((page.locator(".diff").first, "Exact diff vs. first draft"))
        d.wait(5)
        d.clear()

        # ---------- 6. agent
        d.go("#/run/ship_spares/agent")
        d.cap(5, "The agent is a declarative definition",
              "System prompt, retrieval settings, output contract and tools — downloadable as agent.json and runnable by any runner.")
        d.hl((page.locator(".prompt"), "Generated system prompt"), (page.locator(".grid[style*='align-content'] .card").first, "Retrieval config"))
        d.wait(8)
        d.clear()

        # ---------- 7. chat
        d.go("#/run/ship_spares/chat")
        d.cap(6, "Try the produced agent — live", "It answers only from the documents, cites its sources and uses tools when needed.")
        d.wait(3)
        for q, note in [
            ("What would 3 units of MS-2001 cost, and who has to approve that order?",
             "Catalogue lookup → calculator → approval threshold from the procurement policy."),
            ("If I order MS-2004 today, when will it be dispatched?",
             "Today's date + the 56-day lead time, computed by the date tool — not guessed."),
            ("Can you recommend a good recipe for chocolate cake?", "Out of scope → it declines instead of improvising."),
        ]:
            d.type(page.locator("#q"), q, delay=28)
            d.click(page.locator("#send"))
            try:
                page.wait_for_function("() => !document.querySelector('.typing')", timeout=40000)
            except Exception:
                pass
            d.wait(1)
            last = page.locator(".msg.bot").last
            d.hl((last, "Grounded answer"))
            d.cap(6, q, note)
            d.wait(7)
            d.clear()
        src = page.locator(".msg.bot .src").first
        if src.count():
            d.click(src)
            d.hl((page.locator(".msg.bot .src-body").first, "Cited passage"))
            d.cap(6, "Click a source to see the exact passage", "Every claim traces back to the documents.")
            d.wait(5)
            d.clear()

        # ---------- 8. report
        d.go("#/run/hr_policy/report")
        d.cap(7, "And an honest report for every run",
              "Groundability gaps, tool decisions, remaining failures with root causes, eval provenance, judge defences and cost.")
        d.wait(5)
        for _ in range(4):
            d.scroll_by(520)
            d.wait(1.5)
        d.wait(2)

        # ---------- 8. build live (last: a running build shares the API quota with chat)
        d.go("#/new")
        d.cap(8, "Build a new agent live", "Write the brief like you'd brief a colleague, then drop in the documents.")
        brief = (ROOT / "demos/_holdout_it/brief.txt").read_text().strip()
        d.type(page.locator("#brief"), brief, delay=12)
        d.wait(0.6)
        page.locator("#fi").set_input_files([str(ROOT / "demos/_holdout_it/docs/it_service_desk_guide.docx"),
                                             str(ROOT / "demos/_holdout_it/docs/vessel_connectivity.txt")])
        d.wait(0.8)
        d.hl((page.locator("#drop"), "DOCX + TXT uploaded"))
        page.locator("#nm").fill("it_helpdesk_demo")
        page.locator("#it").evaluate("e => { e.value = 1; e.dispatchEvent(new Event('input', {bubbles: true})); }")
        d.wait(2.5)
        d.clear()
        d.click(page.locator("#go"))
        try:
            page.wait_for_function("() => location.hash.startsWith('#/build/')", timeout=60000)
        except Exception:  # e.g. another build already running on the shared server
            d.cap(8, "A build is already running on the server", "Only one build at a time on the free instance.")
            d.wait(4)
        d.wait(2)
        d.cap(8, "The factory runs unattended — every stage streams live",
              "Intake asks itself ≤5 questions and logs assumptions, then the groundability guard checks each topic against the docs.")
        d.hl((page.locator("#steps"), "7 stages"), (page.locator("#console"), "Live factory log"))
        for _ in range(9):
            d.wait(4)
            d.hl((page.locator("#steps"), "7 stages"), (page.locator("#console"), "Live factory log"))
        txt = page.locator("#console").inner_text()
        if "GAP" in txt:
            d.cap(8, "Gap caught before any agent exists",
                  "The brief asks for self-service password resets — the documents can't support it, so it's flagged and refused, not improvised.")
            d.wait(7)
        d.cap(8, "A full build takes about 7 minutes", "It finishes on the server and appears in the sidebar with its own report card.")
        d.wait(4)
        d.clear()

        # ---------- end card
        d.ensure()
        page.evaluate("""() => __demo.title("Forge<span class='acc'>AI</span>", 'Live: forgeai-frk3.onrender.com &nbsp;·&nbsp; Code: github.com/S-Harshni/forgeai')""")
        d.wait(6)

        video = page.video.path()
        ctx.close()
        browser.close()

    mp4 = OUT / "ForgeAI_demo.mp4"
    import imageio_ffmpeg
    subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-loglevel", "error", "-i", video, "-c:v", "libx264",
                    "-pix_fmt", "yuv420p", "-crf", "20", "-r", "30", "-movflags", "+faststart", str(mp4)], check=True)
    Path(video).unlink(missing_ok=True)
    print(mp4)


if __name__ == "__main__":
    record()
