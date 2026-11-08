"""Glue between the web page and the converter: one PDF in, zip + markdown out. No Streamlit code here."""
import io, os, re, threading, zipfile, tempfile, contextlib
import pdf2md_core as core

_LOCK = threading.Lock()      # the converter uses module-level state, so run one conversion at a time


def safe_stem(name):
    stem = re.sub(r"[^\w\-. ()]+", "_", os.path.splitext(os.path.basename(name))[0]).strip(" ._") or "slides"
    return stem[:80]


def process_pdf(name, data, images=True, ocr=True, on_progress=None):
    """Convert PDF bytes. Returns dict(stem, zip_bytes, obs_bytes, chat_bytes, log)."""
    stem = safe_stem(name)
    with _LOCK, tempfile.TemporaryDirectory() as tmp:
        pdf = os.path.join(tmp, stem + ".pdf")
        with open(pdf, "wb") as f: f.write(data)
        buf = io.StringIO()
        core.progress_cb = on_progress
        try:
            with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
                core.convert(pdf, images=images, ocr=ocr)
        finally:
            core.progress_cb = None
        obs_path, chat_path = os.path.join(tmp, stem + "_obsidian.md"), os.path.join(tmp, stem + "_chat.md")
        obs, chat = open(obs_path, "rb").read(), open(chat_path, "rb").read()
        out = io.BytesIO()
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr(f"{stem}/{stem}_obsidian.md", obs)
            imgdir = os.path.join(tmp, stem + "_images")
            if os.path.isdir(imgdir):
                for n in sorted(os.listdir(imgdir)):
                    z.write(os.path.join(imgdir, n), f"{stem}/{stem}_images/{n}", compress_type=zipfile.ZIP_STORED)
        return {"stem": stem, "zip_bytes": out.getvalue(), "obs_bytes": obs, "chat_bytes": chat, "log": buf.getvalue().strip()}
