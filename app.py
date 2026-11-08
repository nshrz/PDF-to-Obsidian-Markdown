import time
import streamlit as st
import runner

st.set_page_config(page_title="PDF slides to Markdown", page_icon="📝", layout="centered")


@st.cache_resource
def shared():
    """One queue and one results store shared by every visitor."""
    return runner.JobQueue(), runner.ResultStore()


jobs, store = shared()
ss = st.session_state
ss.setdefault("rids", []); ss.setdefault("msgs", []); ss.setdefault("ukey", 0)

st.title("PDF slides to Markdown")
st.write("Keeps **text colors, bold, italic, highlights, tables and images**, and reads the text inside picture-only slides (OCR). "
         "Gives you an Obsidian version (colors render) and a short-tag version for chatbots (fewest tokens).")
busy, waiting = jobs.status()
st.caption(("Converter is busy" if busy else "Converter is free") + (f", {waiting} waiting in line." if waiting else "."))

files = st.file_uploader(f"Choose up to {runner.MAX_FILES} PDFs (max {runner.MAX_MB} MB and {runner.MAX_PAGES} pages each)",
                         type="pdf", accept_multiple_files=True, key=f"up{ss.ukey}")
c1, c2 = st.columns(2)
images = c1.checkbox("Extract images", value=True)
ocr = c2.checkbox("Read text inside picture-only slides (OCR)", value=True)
if len(files) > runner.MAX_FILES:
    st.warning(f"Only the first {runner.MAX_FILES} files will be converted. Run the rest afterwards.")


def run_batch(batch):
    good, msgs, new_ids = [], [], []
    for f in batch[:runner.MAX_FILES]:
        data = f.getvalue(); err = runner.check_pdf(f.name, data)
        if err: msgs.append(("error", err))
        else: good.append((f.name, data))
    if good:
        ticket = jobs.join()
        if ticket is None:
            msgs.append(("warning", f"The converter is full right now ({runner.MAX_WAITING} people are already waiting). Please try again in a few minutes."))
        else:
            slot = st.empty()
            try:
                while True:
                    s = jobs.wait(ticket)
                    if s[0] == "go": break
                    if s[0] == "expired":
                        msgs.append(("warning", "You were dropped from the line because the page went idle. Press Convert again.")); good = []; break
                    slot.info(f"You are number {s[1]} in line. Estimated wait: about {s[2]} min. Keep this tab open.")
                    time.sleep(1.5)
                slot.empty()
                for i, (name, data) in enumerate(good):
                    bar = st.progress(0.0, text=f"Converting {name} ({i + 1} of {len(good)})... big decks take a few minutes")
                    try:
                        res = runner.process_pdf(name, data, images, ocr, ticket=ticket,
                                                 on_progress=lambda x, b=bar, n=name: b.progress(min(x, 1.0), text=f"Converting {n}..."))
                        new_ids.append(store.put(res))
                    except TimeoutError as e:
                        msgs.append(("error", str(e)))
                    except Exception as e:
                        msgs.append(("error", f"{name} could not be converted: {e}"))
                    bar.empty()
            finally:
                jobs.done(ticket)           # always free the converter, even if the visitor closes the tab
    ss.rids, ss.msgs = new_ids, msgs
    ss.ukey += 1                            # resets the uploader so the uploaded files are dropped from memory
    st.rerun()


if st.button("Convert", type="primary", disabled=not files):
    run_batch(files)

for level, text in ss.msgs:
    (st.error if level == "error" else st.warning)(text)

for k, rid in enumerate(ss.rids):
    r = store.get(rid)
    st.divider()
    if not r:
        st.info("These results expired to save server memory. Please convert the file again."); continue
    st.subheader(r["stem"])
    d1, d2, d3 = st.columns(3)
    d1.download_button("Obsidian (.zip with images)", r["zip_bytes"], f"{r['stem']}_obsidian.zip", "application/zip", key=f"z{k}")
    d2.download_button("Chatbot (.md)", r["chat_bytes"], f"{r['stem']}_chat.md", "text/markdown", key=f"c{k}")
    d3.download_button("Obsidian note only (.md)", r["obs_bytes"], f"{r['stem']}_obsidian.md", "text/markdown", key=f"o{k}")
    st.caption(next((l.strip() for l in r["log"].splitlines() if "slides kept" in l), ""))
    with st.expander("Preview the chatbot version"):
        st.code(r["chat_bytes"].decode("utf-8")[:20000], language="markdown")
    with st.expander("Conversion log"):
        st.text(r["log"])
    st.caption(f"Download now: results are deleted after {runner.STORE_MINUTES} minutes.")

st.divider()
st.caption("Works on PDFs with a real text layer (PowerPoint, Word, Google Slides, LaTeX exports). "
           "Slides that are only pictures are kept as images with their OCR text below. Colors cannot be recovered from pictures.")
