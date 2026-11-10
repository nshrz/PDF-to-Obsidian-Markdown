"""Glue between the web page and the converter, plus the safety rails that keep a free server from falling over:
a waiting line with a size limit, per-file limits, a time limit per job, and a size-limited store for finished results.
No Streamlit code here."""
import io, os, re, time, threading, zipfile, tempfile, contextlib, collections, uuid
import pdf2md_core as core

# ---- limits (change these if you like) ----
MAX_FILES = 6                 # PDFs per click
MAX_MB = 60                   # per PDF
MAX_TOTAL_MB = 150            # all PDFs of one click together
MAX_PAGES = 400               # per PDF
MAX_SECONDS = 15 * 60         # a single PDF that runs longer than this is stopped so it cannot block everyone
MAX_WAITING = 5               # people allowed to wait in line; more than this are asked to come back later
STALE = 15                    # seconds without a sign of life before a waiting person is dropped from the line
STORE_MB = 250                # total size of finished results kept in memory
STORE_MINUTES = 30            # results are deleted after this long
DEFAULT_JOB_SECONDS = 150     # guess for the wait estimate until real timings are known

_LOCK = threading.Lock()      # the converter keeps module-level state, so only one conversion runs at a time


class Ticket:
    def __init__(self):
        self.seen = time.time(); self.started = None

    def touch(self):
        self.seen = time.time()


class JobQueue:
    """First come, first served. One job runs at a time; people who leave the page are dropped automatically."""
    def __init__(self):
        self.cv = threading.Lock(); self.line = []; self.active = None
        self.durations = collections.deque(maxlen=5)

    def _prune(self):
        now = time.time()
        self.line = [t for t in self.line if now - t.seen < STALE]
        if self.active and now - self.active.seen > STALE + 60: self.active = None     # safety net for a stuck job

    def status(self):
        with self.cv:
            self._prune(); return (self.active is not None, len(self.line))

    def join(self):
        """Get a place in line, or None if the line is full."""
        with self.cv:
            self._prune()
            if len(self.line) >= MAX_WAITING: return None
            t = Ticket(); self.line.append(t); return t

    def wait(self, t):
        """('go',) when it is your turn, ('wait', position, minutes) otherwise, ('expired',) if you were dropped."""
        with self.cv:
            t.touch(); self._prune()
            if t not in self.line: return ("expired",)
            if self.active is None and self.line[0] is t:
                self.line.pop(0); self.active = t; t.started = time.time(); return ("go",)
            pos = self.line.index(t) + 1
            avg = sum(self.durations) / len(self.durations) if self.durations else DEFAULT_JOB_SECONDS
            return ("wait", pos, max(1, round(avg * (pos + (0.5 if self.active else 0)) / 60)))

    def done(self, t):
        with self.cv:
            if t in self.line: self.line.remove(t)
            if self.active is t:
                self.active = None
                if t.started: self.durations.append(time.time() - t.started)


class ResultStore:
    """Finished results live here (not in each visitor's session), with a total size cap and an expiry time."""
    def __init__(self):
        self.lock = threading.Lock(); self.items = collections.OrderedDict()

    @staticmethod
    def _size(res): return sum(len(res[k]) for k in ("zip_bytes", "obs_bytes", "chat_bytes"))

    def _evict(self):
        now = time.time()
        for k in [k for k, (ts, _, _) in self.items.items() if now - ts > STORE_MINUTES * 60]: del self.items[k]
        while len(self.items) > 1 and sum(s for _, s, _ in self.items.values()) > STORE_MB * 1024 * 1024:
            self.items.popitem(last=False)

    def put(self, res):
        with self.lock:
            rid = uuid.uuid4().hex; self.items[rid] = (time.time(), self._size(res), res); self._evict(); return rid

    def get(self, rid):
        with self.lock:
            self._evict(); it = self.items.get(rid)
            if it: self.items.move_to_end(rid)
            return it[2] if it else None


def safe_stem(name):
    stem = re.sub(r"[^\w\-. ()]+", "_", os.path.splitext(os.path.basename(name))[0]).strip(" ._") or "slides"
    return stem[:80]


def check_pdf(name, data):
    """Return an error message if this file should not be converted, else None."""
    if len(data) > MAX_MB * 1024 * 1024: return f"{name} is larger than {MAX_MB} MB."
    try:
        doc = core.fitz.open(stream=data, filetype="pdf")
    except Exception:
        return f"{name} could not be opened as a PDF."
    try:
        if doc.needs_pass: return f"{name} is password protected."
        if doc.page_count > MAX_PAGES: return f"{name} has {doc.page_count} pages; the limit is {MAX_PAGES}. Split it and convert the parts."
    finally:
        doc.close()
    return None


def classify(message):
    """Colour category and badge for one log line (same rules as the desktop window)."""
    m = message.lower()
    if any(x in m for x in ("failed", "error", "exception", "traceback", "cannot find", "could not")): return "error", "ERROR"
    if any(x in m for x in ("warning", "warn", "skipped", "skip", "not installed", "no text layer", "trying the next option")): return "warning", "WARN"
    if any(x in m for x in ("success", "saved ", "wrote ", "finished", "complete", "ready.", "combined file written", "converted ", "ocr installed.")): return "success", "DONE"
    if any(x in m for x in ("[info]", "[stage]", "starting", "opening", "reading", "analys", "processing", "page ", "ocr", "extract", "index",
                            "merge order", "installing", "detecting", "writing", "converting")): return "info", "INFO"
    return "detail", "LOG"


def clean_line(line):
    return re.sub(r"^\s*\[(INFO|SUCCESS|STAGE|ERROR|WARN|WARNING)\]\s*", "", line.rstrip("\r"), flags=re.I)


def process_pdf(name, data, images=True, ocr=True, on_progress=None, ticket=None, on_status=None, on_log=None):
    """Convert PDF bytes. Returns dict(stem, zip_bytes, obs_bytes, chat_bytes, log). Raises TimeoutError past MAX_SECONDS."""
    stem = safe_stem(name)
    start = time.time()

    def progress(x):
        if ticket: ticket.touch()
        if time.time() - start > MAX_SECONDS:
            raise TimeoutError(f"{name} took longer than {MAX_SECONDS // 60} minutes and was stopped.")
        if on_progress: on_progress(x)

    with _LOCK, tempfile.TemporaryDirectory() as tmp:
        pdf = os.path.join(tmp, stem + ".pdf")
        with open(pdf, "wb") as f: f.write(data)
        buf = io.StringIO()

        class Sink:                                   # every printed line goes to the log and, live, to the page
            def __init__(self): self.pending = ""
            def write(self, v):
                if not v: return 0
                buf.write(v); self.pending += v
                while "\n" in self.pending:
                    line, self.pending = self.pending.split("\n", 1)
                    if on_log and line.strip(): on_log(line)
                return len(v)
            def flush(self): pass

        core.progress_cb, core.status_cb = progress, on_status
        try:
            with contextlib.redirect_stdout(Sink()), contextlib.redirect_stderr(Sink()):
                core.convert(pdf, images=images, ocr=ocr)
        finally:
            core.progress_cb = core.status_cb = None
        obs = open(os.path.join(tmp, stem + "_obsidian.md"), "rb").read()
        chat = open(os.path.join(tmp, stem + "_chat.md"), "rb").read()
        out = io.BytesIO()
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr(f"{stem}/{stem}_obsidian.md", obs)
            imgdir = os.path.join(tmp, stem + "_images")
            if os.path.isdir(imgdir):
                for n in sorted(os.listdir(imgdir)):
                    z.write(os.path.join(imgdir, n), f"{stem}/{stem}_images/{n}", compress_type=zipfile.ZIP_STORED)
        return {"stem": stem, "zip_bytes": out.getvalue(), "obs_bytes": obs, "chat_bytes": chat, "log": buf.getvalue().strip()}


def merge_results(results, name="Combined"):
    """Join several finished results (already in the wanted order) into one result of the same shape."""
    name = core.clean_name(name)
    fix = lambda t: re.sub(r"!\[\[(.*?)\]\]", lambda m: f"![[{name}_images/{m.group(1)}]]", t)
    obs = core.merge_texts([(x["stem"], fix(x["obs_bytes"].decode("utf-8"))) for x in results], "obs").encode("utf-8")
    chat = core.merge_texts([(x["stem"], x["chat_bytes"].decode("utf-8")) for x in results], "chat").encode("utf-8")
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(f"{name}/{name}_obsidian.md", obs)
        for x in results:
            with zipfile.ZipFile(io.BytesIO(x["zip_bytes"])) as zr:
                for info in zr.infolist():
                    if info.is_dir() or info.filename.endswith(".md"): continue
                    z.writestr(f"{name}/{name}_images/{os.path.basename(info.filename)}", zr.read(info.filename), compress_type=zipfile.ZIP_STORED)
    return {"stem": name, "zip_bytes": out.getvalue(), "obs_bytes": obs, "chat_bytes": chat,
            "log": "\n\n".join(x["log"] for x in results), "note": f"Combined {len(results)} PDFs: " + ", ".join(x["stem"] for x in results)}
