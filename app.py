import html, time
import streamlit as st
import runner

st.set_page_config(page_title="PDF slides to Markdown", page_icon="📝", layout="wide")

# ---------- look: Apple-style, works in light and dark ----------
st.markdown("""
<style>
html, body, [class*="css"], .stApp { font-family: -apple-system, BlinkMacSystemFont, "SF Pro Text", "Segoe UI", "Helvetica Neue", Inter, Roboto, Arial, sans-serif !important; }
.block-container { max-width: 1080px; padding-top: 2.2rem; }
[data-testid="stFileUploaderDropzoneInstructions"], [data-testid="stFileUploaderDropzone"] small { display: none !important; }
[data-testid="stVerticalBlockBorderWrapper"] { border-radius: 16px !important; border-color: rgba(128,128,128,.22) !important; }
[data-testid="stFileUploaderDropzone"] { border-radius: 12px; }
div.stButton > button, div.stDownloadButton > button { border-radius: 999px; padding: .5rem 1.2rem; font-weight: 600; }
div.stButton > button[kind="primary"], button[data-testid="stBaseButton-primary"] { background: #007AFF; border-color: #007AFF; color: #fff; }
div.stButton > button[kind="primary"]:hover, button[data-testid="stBaseButton-primary"]:hover { background: #2B8CFF; border-color: #2B8CFF; }
.p2m-title { font-size: 2.1rem; font-weight: 700; letter-spacing: -.02em; margin: 0; }
.p2m-sub { opacity: .65; margin: .15rem 0 1.1rem; }
.p2m-head { display: flex; align-items: center; justify-content: space-between; gap: 1rem; flex-wrap: wrap; }
.p2m-pill { display: inline-flex; align-items: center; gap: .5rem; padding: .3rem .8rem; border-radius: 999px; font-size: .82rem; font-weight: 600; }
.p2m-pill i { width: 8px; height: 8px; border-radius: 50%; background: currentColor; display: inline-block; }
.p2m-ok { background: rgba(52,199,89,.16); color: #2DA44E; } .p2m-busy { background: rgba(0,122,255,.16); color: #0A84FF; }
.p2m-h { display: flex; align-items: center; gap: .6rem; font-weight: 650; font-size: 1.05rem; margin-bottom: .6rem; }
.p2m-n { width: 22px; height: 22px; border-radius: 50%; background: #007AFF; color: #fff; font-size: .75rem; display: inline-flex; align-items: center; justify-content: center; }
.p2m-prog-top { display: flex; justify-content: space-between; gap: .8rem; font-size: .9rem; opacity: .85; }
.p2m-prog-top b { color: #0A84FF; font-size: 1.05rem; }
.p2m-bar { height: 8px; border-radius: 999px; background: rgba(128,128,128,.2); overflow: hidden; margin: .55rem 0 .35rem; }
.p2m-bar i { display: block; height: 100%; border-radius: 999px; background: #007AFF; transition: width .25s ease; }
.p2m-el { font-size: .78rem; opacity: .55; }
.p2m-log { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: .78rem; line-height: 1.5; height: 300px; overflow-y: auto;
           padding: .7rem .9rem; border-radius: 12px; background: rgba(128,128,128,.08); border: 1px solid rgba(128,128,128,.18); }
.p2m-log div { white-space: pre-wrap; word-break: break-word; } .p2m-log .t { opacity: .5; margin-right: .6rem; } .p2m-log .b { font-weight: 700; margin-right: .6rem; }
.p2m-log .info { color: #0A84FF; } .p2m-log .success { color: #2DA44E; } .p2m-log .warning { color: #D98E04; } .p2m-log .error { color: #E5484D; } .p2m-log .detail { opacity: .75; }
.p2m-legend { display: flex; gap: .9rem; font-size: .78rem; opacity: .8; margin: -.2rem 0 .6rem; }
.p2m-legend i { width: 8px; height: 8px; border-radius: 50%; display: inline-block; margin-right: .3rem; }
</style>
""", unsafe_allow_html=True)


@st.cache_resource
def shared():
    """One queue and one results store shared by every visitor."""
    return runner.JobQueue(), runner.ResultStore()


jobs, store = shared()
ss = st.session_state
ss.setdefault("rids", []); ss.setdefault("msgs", []); ss.setdefault("ukey", 0)
ss.setdefault("live", {"lines": [], "stage": "Ready.", "frac": 0.0, "elapsed": 0})


def progress_html(stage, frac, elapsed):
    pct = round(max(0.0, min(1.0, frac)) * 100)
    el = f"Elapsed {int(elapsed) // 60}:{int(elapsed) % 60:02d}" if elapsed else "&nbsp;"
    return (f'<div class="p2m-prog"><div class="p2m-prog-top"><span>{html.escape(stage)}</span><b>{pct}%</b></div>'
            f'<div class="p2m-bar"><i style="width:{pct}%"></i></div><div class="p2m-el">{el}</div></div>')


def log_html(lines):
    if not lines:
        return '<div class="p2m-log"><div class="detail">Nothing yet. Add PDFs and press Convert.</div></div>'
    rows = "".join(f'<div class="{c}"><span class="t">{t}</span><span class="b">{b}</span>{html.escape(x)}</div>' for t, c, b, x in lines[-120:])
    return f'<div class="p2m-log">{rows}</div>'


# ---------- header ----------
busy, waiting = jobs.status()
pill = (f'<span class="p2m-pill p2m-busy"><i></i>Busy, {waiting} waiting</span>' if busy else '<span class="p2m-pill p2m-ok"><i></i>Ready</span>')
st.markdown(f'<div class="p2m-head"><div><p class="p2m-title">PDF slides to Markdown</p>'
            f'<p class="p2m-sub">Keeps colors, bold, italic, highlights, tables and images. Picture-only slides are read with OCR.</p></div>{pill}</div>',
            unsafe_allow_html=True)

# ---------- 1 and 2 ----------
c1, c2 = st.columns([5, 4], gap="medium")
with c1.container(border=True):
    st.markdown('<div class="p2m-h"><span class="p2m-n">1</span>Choose your PDFs</div>', unsafe_allow_html=True)
    files = st.file_uploader(f"Up to {runner.MAX_FILES} PDFs (max {runner.MAX_MB} MB and {runner.MAX_PAGES} pages each, {runner.MAX_TOTAL_MB} MB in total)",
                             type="pdf", accept_multiple_files=True, key=f"up{ss.ukey}")
    if len(files) > runner.MAX_FILES:
        st.warning(f"Only the first {runner.MAX_FILES} files (in the order set on the right) will be converted. Run the rest afterwards.")

with c2.container(border=True):
    st.markdown('<div class="p2m-h"><span class="p2m-n">2</span>Options</div>', unsafe_allow_html=True)
    images = st.toggle("Extract images", value=True, help="Saved with the note and embedded in it.")
    ocr = st.toggle("Read text inside picture-only slides (OCR)", value=True, help="The text appears right under the image.")
    st.divider()
    combine = st.toggle("Combine all PDFs into one file", help="One Obsidian note and one chatbot file, in the order you choose.")
    combo_name, order_idx = "Combined", list(range(len(files)))
    if combine:
        combo_name = st.text_input("Name of the combined file", value="Combined")
        labels = ["By file name (Lecture 2 comes before Lecture 10)", "In the order I uploaded them", "I will type a number for each PDF"]
        mode = ["name", "given", "numbers"][labels.index(st.radio("How should the PDFs be numbered?", labels))]
        numbers = None
        if mode == "numbers" and files:
            st.caption("Smaller numbers come first. Equal numbers keep the upload order.")
            numbers = [st.number_input(f.name, min_value=1, value=i + 1, step=1, key=f"n{ss.ukey}_{i}") for i, f in enumerate(files)]
        order_idx = runner.core.order_indices([f.name for f in files], mode, numbers)
        if len(files) > 1:
            st.caption("Final order: " + "  →  ".join(f"{k + 1}. {files[i].name}" for k, i in enumerate(order_idx)))

# ---------- 3 and Activity ----------
c3, c4 = st.columns([2, 3], gap="medium")
with c3.container(border=True):
    st.markdown('<div class="p2m-h"><span class="p2m-n">3</span>Convert</div>', unsafe_allow_html=True)
    go = st.button("Convert", type="primary", disabled=not files, use_container_width=True)
    panel = st.empty()
with c4.container(border=True):
    st.markdown('<div class="p2m-h">Activity</div><div class="p2m-legend"><span><i style="background:#0A84FF"></i>Info</span>'
                '<span><i style="background:#2DA44E"></i>Done</span><span><i style="background:#D98E04"></i>Warning</span>'
                '<span><i style="background:#E5484D"></i>Error</span></div>', unsafe_allow_html=True)
    logbox = st.empty()

live = ss.live
panel.markdown(progress_html(live["stage"], live["frac"], live["elapsed"]), unsafe_allow_html=True)
logbox.markdown(log_html(live["lines"]), unsafe_allow_html=True)


class Live:
    """Updates the progress card and the activity log while a conversion runs (throttled so the page stays smooth)."""
    def __init__(self):
        self.lines, self.stage, self.frac, self.t0, self.last = [], "Starting…", 0.0, time.time(), 0.0

    def draw(self, force=False):
        now = time.time()
        if not force and now - self.last < 0.25: return
        self.last = now
        panel.markdown(progress_html(self.stage, self.frac, now - self.t0), unsafe_allow_html=True)
        logbox.markdown(log_html(self.lines), unsafe_allow_html=True)

    def log(self, raw):
        cat, badge = runner.classify(raw)
        self.lines.append((time.strftime("%H:%M:%S"), cat, badge, runner.clean_line(raw))); self.draw()

    def set_stage(self, s): self.stage = s; self.draw()
    def set_frac(self, f): self.frac = f; self.draw()


def run_batch(batch):
    L = Live()
    good, msgs, new_ids, results, total, seen = [], [], [], [], 0, {}
    for f in batch[:runner.MAX_FILES]:
        data = f.getvalue(); total += len(data)
        err = runner.check_pdf(f.name, data) or (f"The files together are larger than {runner.MAX_TOTAL_MB} MB. Convert fewer at a time." if total > runner.MAX_TOTAL_MB * 1024 * 1024 else None)
        if err: msgs.append(("error", err)); L.log(f"[ERROR] {err}"); continue
        stem = runner.safe_stem(f.name); seen[stem] = seen.get(stem, 0) + 1
        good.append((f.name if seen[stem] == 1 else f"{stem}_{seen[stem]}.pdf", data))     # keep names unique
    if good:
        ticket = jobs.join()
        if ticket is None:
            msgs.append(("warning", f"The converter is full right now ({runner.MAX_WAITING} people are already waiting). Please try again in a few minutes."))
            L.log("[WARN] The converter is full right now. Try again in a few minutes.")
        else:
            try:
                while True:
                    s = jobs.wait(ticket)
                    if s[0] == "go": break
                    if s[0] == "expired":
                        msgs.append(("warning", "You were dropped from the line because the page went idle. Press Convert again.")); good = []; break
                    L.stage = f"You are number {s[1]} in line. Estimated wait: about {s[2]} min. Keep this tab open."; L.draw(force=True)
                    time.sleep(1.5)
                L.t0 = time.time(); L.log(f"[INFO] Starting: {len(good)} file(s). Big decks take a few minutes.")
                for i, (name, data) in enumerate(good):
                    try:
                        res = runner.process_pdf(name, data, images, ocr, ticket=ticket,
                                                 on_progress=lambda x, i=i: L.set_frac((i + min(x, 1.0)) / len(good)),
                                                 on_status=L.set_stage, on_log=L.log)
                        results.append(res)
                    except TimeoutError as e:
                        msgs.append(("error", str(e))); L.log(f"[ERROR] {e}")
                    except Exception as e:
                        msgs.append(("error", f"{name} could not be converted: {e}")); L.log(f"[ERROR] {name} could not be converted: {e}")
            finally:
                jobs.done(ticket)           # always free the converter, even if the visitor closes the tab
        if results:
            if combine and len(results) > 1:
                L.stage = "Merging converted sections"; L.draw(force=True)
                new_ids = [store.put(runner.merge_results(results, combo_name))]
                L.log(f"[SUCCESS] Combined {len(results)} PDFs into {runner.core.clean_name(combo_name)}")
            else:
                new_ids = [store.put(x) for x in results]
            L.frac, L.stage = 1.0, "Done. Your files are ready below."
        else:
            L.stage = "Nothing was converted. See the log."
    L.draw(force=True)
    ss.live = {"lines": L.lines[-200:], "stage": L.stage, "frac": L.frac, "elapsed": time.time() - L.t0}
    ss.rids, ss.msgs = new_ids, msgs
    ss.ukey += 1                            # resets the uploader so the uploaded files are dropped from memory
    st.rerun()


if go:
    run_batch([files[i] for i in order_idx])

# ---------- results ----------
for level, text in ss.msgs:
    (st.error if level == "error" else st.warning)(text)

for k, rid in enumerate(ss.rids):
    r = store.get(rid)
    with st.container(border=True):
        if not r:
            st.info("These results expired to save server memory. Please convert the file again."); continue
        st.markdown(f'<div class="p2m-h">{html.escape(r["stem"])}</div>', unsafe_allow_html=True)
        d1, d2, d3 = st.columns(3)
        d1.download_button("Obsidian (.zip with images)", r["zip_bytes"], f"{r['stem']}_obsidian.zip", "application/zip", key=f"z{k}", use_container_width=True)
        d2.download_button("Chatbot (.md)", r["chat_bytes"], f"{r['stem']}_chat.md", "text/markdown", key=f"c{k}", use_container_width=True)
        d3.download_button("Obsidian note only (.md)", r["obs_bytes"], f"{r['stem']}_obsidian.md", "text/markdown", key=f"o{k}", use_container_width=True)
        st.caption(r.get("note") or next((l.strip() for l in r["log"].splitlines() if "slides kept" in l), ""))
        with st.expander("Preview the chatbot version"):
            st.code(r["chat_bytes"].decode("utf-8")[:20000], language="markdown")
        st.caption(f"Download now: results are deleted after {runner.STORE_MINUTES} minutes.")

st.caption("Works on PDFs with a real text layer (PowerPoint, Word, Google Slides, LaTeX exports). "
           "Slides that are only pictures are kept as images with their OCR text below. Colors cannot be recovered from pictures.")
