import streamlit as st
from runner import process_pdf

st.set_page_config(page_title="PDF slides to Markdown", page_icon="📝", layout="centered")
st.title("PDF slides to Markdown")
st.write("Keeps **text colors, bold, italic, highlights, tables and images**, and reads the text inside picture-only slides (OCR). "
         "Gives you an Obsidian version (colors render) and a short-tag version for chatbots (fewest tokens).")

files = st.file_uploader("Choose one or more PDFs", type="pdf", accept_multiple_files=True)
c1, c2 = st.columns(2)
images = c1.checkbox("Extract images", value=True)
ocr = c2.checkbox("Read text inside picture-only slides (OCR)", value=True)

if "results" not in st.session_state:
    st.session_state.results = []

if st.button("Convert", type="primary", disabled=not files):
    st.session_state.results = []
    for i, f in enumerate(files):
        bar = st.progress(0.0, text=f"Converting {f.name} ({i + 1} of {len(files)})... big decks take a minute or two")
        try:
            res = process_pdf(f.name, f.getvalue(), images, ocr, on_progress=lambda x, b=bar, n=f.name: b.progress(min(x, 1.0), text=f"Converting {n}..."))
            st.session_state.results.append(res)
        except Exception as e:
            st.session_state.results.append({"stem": f.name, "error": str(e)})
        bar.empty()

for k, r in enumerate(st.session_state.results):
    st.divider()
    st.subheader(r["stem"])
    if "error" in r:
        st.error("Could not convert this file: " + r["error"])
        continue
    d1, d2, d3 = st.columns(3)
    d1.download_button("Obsidian (.zip with images)", r["zip_bytes"], f"{r['stem']}_obsidian.zip", "application/zip", key=f"z{k}")
    d2.download_button("Chatbot (.md)", r["chat_bytes"], f"{r['stem']}_chat.md", "text/markdown", key=f"c{k}")
    d3.download_button("Obsidian note only (.md)", r["obs_bytes"], f"{r['stem']}_obsidian.md", "text/markdown", key=f"o{k}")
    st.caption(next((l.strip() for l in r["log"].splitlines() if "slides kept" in l), ""))
    with st.expander("Preview the chatbot version"):
        st.code(r["chat_bytes"].decode("utf-8")[:20000], language="markdown")
    with st.expander("Conversion log"):
        st.text(r["log"])

st.divider()
st.caption("Works on PDFs with a real text layer (PowerPoint, Word, Google Slides, LaTeX exports). "
           "Slides that are only pictures are kept as images with their OCR text below. Colors cannot be recovered from pictures.")
