"""pdf2md v3 (with combine option) - PDF slides -> Markdown that keeps text color, bold, italic, highlights, tables and images.

SETUP (once)
    1. Install Python from python.org  (on Windows tick "Add python.exe to PATH").
    2. Open a terminal and run:   pip install pymupdf rapidocr onnxruntime
       (rapidocr + onnxruntime is the OCR used for slides that contain no text; it is optional)

RUN
    python pdf2md_v3.py "My Lecture.pdf"        one file
    python pdf2md_v3.py "My Lecture.pdf" "Other.pdf"
    python pdf2md_v3.py "C:\\path\\to\\folder"     every PDF in a folder
    python pdf2md_v3.py                          every PDF in the current folder
  Options:  --combine     merge all the PDFs into ONE note (use --name=Title and --order=name|given; default order is by file name)
            --no-images   skip image extraction
            --no-ocr      do not OCR slides that have no text

OUTPUT (next to each PDF)
    <name>_obsidian.md + <name>_images/    colored text as HTML spans, ==highlights==, images embedded
    <name>_chat.md                         short tags like <red>, no images, legend line on top (fewest tokens)
Needs a PDF with a text layer (PowerPoint/Word/Google Slides/LaTeX exports). Slides that contain no text (only pictures) are
kept as images and OCR'd automatically: the text found in the picture is written right below it as a quote
(colors cannot be recovered from pictures).
"""
import sys, os, re, glob, collections
import pymupdf as fitz

BUL = "•▪●○◦‣⁃➢➔➤►▸■□◆◇§Øü"
MARKERS = set(BUL + "–-·o")
WATERMARK_FRAC = 0.4        # a short line on more than 40% of pages is a watermark
MIN_IMG_PT = 70             # ignore images smaller than this (points)
MAX_IMG_PAGES = 5           # images on more pages than this are logos/backgrounds
progress_cb = None          # optional: function(fraction 0..1) used by the window version
status_cb = None            # optional: function(stage_text) used by the window version


def report_stage(message):
    """Send a human-readable stage update to the GUI and the command-line log."""
    if status_cb:
        try:
            status_cb(message)
        except Exception:
            pass
    print(f"[STAGE] {message}")


# ---------- colors ----------
def hue(r, g, b):
    mx, mn = max(r, g, b), min(r, g, b)
    d = mx - mn
    if mx < 60 or d < 40 or d / mx < 0.2: return None
    if mx == r: h = ((g - b) / d + 6) % 6
    elif mx == g: h = (b - r) / d + 2
    else: h = (r - g) / d + 4
    h *= 60
    for lim, name in ((15, "red"), (40, "orange"), (70, "yellow"), (170, "green"), (260, "blue"), (345, "purple")):
        if h < lim: return name
    return "red"


def color_name(c):
    return hue((c >> 16) & 255, (c >> 8) & 255, c & 255)


# ---------- shapes: highlights and table cells ----------
def get_fills(page):
    hl, cells, seen = [], [], set()
    area = page.rect.width * page.rect.height
    for d in page.get_drawings():
        f, r = d.get("fill"), d["rect"]
        if not f or r.width * r.height > 0.35 * area: continue
        rgb = tuple(int(x * 255) for x in f[:3])
        key = tuple(round(v) for v in (r.x0, r.y0, r.x1, r.y1)) + rgb
        if key in seen: continue
        seen.add(key)
        lum = 0.3 * rgb[0] + 0.59 * rgb[1] + 0.11 * rgb[2]
        if r.width >= 25 and r.height >= 10 and lum < 250: cells.append(fitz.Rect(r))
        if lum >= 150 and r.height < 60:                 # light, small = highlighter
            n = hue(*rgb)
            if n: hl.append((fitz.Rect(r), n))
    for a in page.annots() or []:
        if a.type[0] in (8, 9, 10, 11):                  # highlight / underline / squiggly / strikeout
            c = a.colors.get("stroke") if a.type[0] == 8 else None
            if c:
                n = hue(*(int(x * 255) for x in c[:3]))
                if n:
                    for q in (a.vertices or []):
                        pass
                    hl.append((fitz.Rect(a.rect), n))
    return hl, cells


def find_tables(cells):
    """Grid-like groups of filled rectangles with the same column starts, stacked directly on each other."""
    rows = collections.defaultdict(list)
    for r in cells: rows[round(r.y0 / 5)].append(r)
    tables, cur = [], None
    for k in sorted(k for k, v in rows.items() if len(v) >= 2):
        rs = sorted(rows[k], key=lambda r: r.x0)
        xs = {round(r.x0 / 8) for r in rs}
        if cur and xs == cur["xs"] and rs[0].y0 - cur["rows"][-1][0].y1 < 12:
            cur["rows"].append(rs)
        else:
            if cur: tables.append(cur)
            cur = {"xs": xs, "rows": [rs]}
    if cur: tables.append(cur)
    return [t for t in tables if len(t["rows"]) >= 2]


# ---------- text ----------
def is_marker(sp, i):
    t = sp["text"].strip()
    if len(t) != 1: return False
    sym = any(k in sp["font"] for k in ("Wingdings", "Symbol", "Courier"))
    return t in BUL or (sym and t in MARKERS) or (i == 0 and t in "–-·")


def make_segs(spans, hl):
    segs = []
    for s in spans:
        if not s["text"].strip():
            segs.append((s["text"], None)); continue
        low = s["font"].lower()
        bold = bool(s["flags"] & 16) or "bold" in low or "black" in low
        ital = bool(s["flags"] & 2) or "italic" in low or "oblique" in low
        p = fitz.Point((s["bbox"][0] + s["bbox"][2]) / 2, (s["bbox"][1] + s["bbox"][3]) / 2)
        bg = next((n for rc, n in hl if rc.contains(p)), None)
        segs.append((s["text"], (color_name(s["color"]), bold, ital, bg)))
    return segs


def page_content(page, ocr=False):
    hl, cells = get_fills(page)
    raw = [b for b in page.get_text("dict")["blocks"] if b["type"] == 0]
    spans_all = [s for b in raw for l in b["lines"] for s in l["spans"] if s["text"] != ""]
    # tables
    tables = []
    for t in find_tables(cells):
        grid, filled = [], 0
        for rs in t["rows"]:
            row = []
            for rc in rs:
                sp = [s for s in spans_all if s["text"].strip() and rc.contains(
                    fitz.Point((s["bbox"][0] + s["bbox"][2]) / 2, (s["bbox"][1] + s["bbox"][3]) / 2))]
                sp.sort(key=lambda s: (round(s["bbox"][1] / 6), s["bbox"][0]))
                segs = []
                for s in sp:
                    if segs: segs.append((" ", None))
                    segs += make_segs([s], hl)
                row.append(segs)
                filled += bool(segs)
            grid.append(row)
        total = sum(len(r) for r in grid)
        if filled >= 0.6 * total:
            x0 = min(r.x0 for rs in t["rows"] for r in rs); x1 = max(r.x1 for rs in t["rows"] for r in rs)
            y0 = min(r.y0 for rs in t["rows"] for r in rs); y1 = max(r.y1 for rs in t["rows"] for r in rs)
            tables.append({"kind": "table", "cells": grid, "bbox": fitz.Rect(x0, y0, x1, y1), "y": y0, "x": x0, "x1": x1})
    skip = [t["bbox"] for t in tables]
    # rows
    rows = []
    for b in raw:
        brow = []
        for l in b["lines"]:
            spans = [s for s in l["spans"] if s["text"] != ""]
            spans = [s for s in spans if not any(bb.contains(fitz.Point((s["bbox"][0] + s["bbox"][2]) / 2, (s["bbox"][1] + s["bbox"][3]) / 2)) for bb in skip)]
            if not spans: continue
            y = (l["bbox"][1] + l["bbox"][3]) / 2
            for r in brow:
                if abs(r["y"] - y) < 5:
                    gap = min(s["bbox"][0] for s in spans) - max(s["bbox"][2] for s in r["spans"])
                    r["spans"] += [{"text": " | " if gap > 25 else " ", "font": "", "flags": 0, "size": spans[0]["size"], "color": 0, "bbox": spans[0]["bbox"]}] + spans
                    r["x0"] = min(r["x0"], l["bbox"][0]); break
            else:
                brow.append({"y": y, "x0": l["bbox"][0], "spans": list(spans), "h": l["bbox"][3] - l["bbox"][1]})
        rows += brow
    items = []
    for r in rows:
        sp = sorted(r["spans"], key=lambda s: s["bbox"][0]) if not any(s["font"] == "" for s in r["spans"]) else r["spans"]
        if not "".join(s["text"] for s in sp).strip(): continue
        groups, cur = [], []
        for i, s in enumerate(sp):
            if is_marker(s, i) and (i == 0 or any(x["text"].strip() for x in cur)) and i + 1 < len(sp):
                if cur: groups.append(cur)
                cur = [s]
            else:
                cur.append(s)
        groups.append(cur)
        for gi, g in enumerate(groups):
            marker, mx = None, r["x0"]
            if g and is_marker(g[0], 0 if gi == 0 else 1) and len(g) > 1:
                marker, mx, g = "-", g[0]["bbox"][0], g[1:]
            segs = make_segs(g, hl)
            real = [s for s in g if s["text"].strip()]
            if not real: continue
            txt = "".join(t for t, _ in segs)
            items.append({"kind": "row", "marker": marker, "segs": segs, "size": max(s["size"] for s in real),
                          "x": mx, "x1": max(s["bbox"][2] for s in real), "tx": real[0]["bbox"][0], "y": r["y"], "h": r["h"],
                          "sub": marker is not None and gi > 0,
                          "num": gi == 0 and bool(re.match(r"\s*(\d+[.)]|[a-zA-Z][.)])\s?", txt))})
    # join wrapped lines into the previous row
    merged = []
    for it in items:
        p = merged[-1] if merged else None
        if (p and not it["marker"] and not it["num"] and 0 < it["y"] - p["y"] < 2.2 * p["h"]
                and it["tx"] >= p["tx"] - 4 and abs(it["size"] - p["size"]) <= max(1.5, 0.25 * p["size"])):
            prev = plain(p)
            nxt = plain(it)
            if prev and (prev[-1] not in ".:;?!" or nxt[:1].islower()):
                p["segs"] += [(" ", None)] + it["segs"]
                p.pop("_plain", None)  # wrapped-line merge changes the cached text
                p["y"] = it["y"]; p["x1"] = max(p["x1"], it["x1"]); continue
        merged.append(it)
    out = merged + tables
    # reading order: keep PDF order for rows, put tables where their top edge falls
    for t in tables:
        out.remove(t)
        pos = next((i for i, r in enumerate(out) if r["y"] > t["y"]), len(out))
        out.insert(pos, t)
    return out


def plain(it):
    """Return normalized text, caching it until the item's segments are changed."""
    cached = it.get("_plain")
    if cached is not None:
        return cached
    if it["kind"] == "table":
        value = " ".join(re.sub(r"\s+", " ", "".join(t for t, _ in cell)).strip()
                         for row in it["cells"] for cell in row)
    else:
        value = re.sub(r"\s+", " ", "".join(t for t, _ in it["segs"])).strip()
    it["_plain"] = value
    return value


# ---------- rendering ----------
def render_segs(segs, mode, heading=False):
    segs = [list(s) for s in segs]
    merged = []
    for t, st in segs:
        if st is None:
            if merged: merged[-1][0] += t
            continue
        if merged and merged[-1][1] == st: merged[-1][0] += t
        else: merged.append([t, st])
    if merged:
        merged[0][0] = re.sub(r"^(\s*\d+[.)])(?=[A-Za-z])", r"\1 ", merged[0][0])
    out = ""
    for t, (c, b, i, bg) in merged:
        m = re.match(r"^(\s*)(.*?)(\s*)$", t, re.S)
        core = re.sub(r"\s+", " ", m.group(2))
        if not core:
            out += t; continue
        if b and not heading: core = "**" + core + "**"
        if i: core = "*" + core + "*"
        if c: core = f'<span style="color:{c}">{core}</span>' if mode == "obs" else f"<{c}>{core}</{c}>"
        if bg:
            if mode == "obs": core = f"=={core}==" if bg == "yellow" else f'<mark style="background:{bg}">{core}</mark>'
            else: core = f"<bg-{bg}>{core}</bg-{bg}>"
        out += (" " if m.group(1) else "") + core + (" " if m.group(3) else "")
    out = re.sub(r" {2,}", " ", out).strip()
    if mode != "obs":
        prev = None
        while prev != out:
            prev = out
            out = re.sub(r"</([a-z-]+)>( ?)<\1>", r"\2", out)
    return out


def render_table(t, mode):
    rows = [[render_segs(c, mode).replace("|", "\\|") for c in row] for row in t["cells"]]
    rows = [r for r in rows if any(r)]
    if not rows: return ""
    w = max(len(r) for r in rows)
    lines = ["| " + " | ".join(r + [""] * (w - len(r))) + " |" for r in rows]
    lines.insert(1, "|" + " --- |" * w)
    return "\n" + "\n".join(lines) + "\n"


# ---------- OCR for slides without a text layer ----------
_ocr = {"engine": None, "kind": None, "tried": False, "err": "", "warned": False}


def _load_ocr():
    """Load an OCR engine: the newer 'rapidocr' package first, then the older 'rapidocr_onnxruntime'."""
    if _ocr["tried"]: return _ocr["engine"] is not None
    _ocr["tried"] = True
    errs = []
    import logging
    logging.disable(logging.INFO)               # silence the OCR package's progress chatter
    for kind, mod in (("new", "rapidocr"), ("old", "rapidocr_onnxruntime")):
        try:
            m = __import__(mod, fromlist=["RapidOCR"])
            _ocr["engine"], _ocr["kind"] = m.RapidOCR(), kind
            return True
        except Exception as ex:
            errs.append(f"{mod}: {type(ex).__name__}: {str(ex)[:120]}")
    _ocr["err"] = "; ".join(errs)
    return False


def ocr_available():
    """(True, '') if text inside pictures can be read, else (False, reason). Used by the window."""
    _ocr["tried"], _ocr["engine"] = False, None
    ok = _load_ocr()
    return ok, _ocr["err"]


def ocr_lines(page):
    """Return a list of text lines found in the rendered page, or None if no OCR engine is available."""
    if not _load_ocr():
        try:                                        # last resort: Tesseract through PyMuPDF
            tp = page.get_textpage_ocr(dpi=150, full=True)
            return [l.strip() for l in page.get_text("text", textpage=tp).splitlines() if l.strip()]
        except Exception:
            if not _ocr["warned"]:
                _ocr["warned"] = True
                print("  (OCR is not available, so text inside pictures was skipped. Reason: " + (_ocr["err"] or "unknown") + ")")
                print("  (Fix: in the window click 'Install OCR', or run:  pip install rapidocr onnxruntime)")
            return None
    png = page.get_pixmap(dpi=150).tobytes("png")
    try:
        if _ocr["kind"] == "new":
            out = _ocr["engine"](png)
            found = list(zip(out.boxes, out.txts, out.scores)) if out.boxes is not None else []
        else:
            res, _ = _ocr["engine"](png)
            found = res or []
    except Exception:
        found = []
    boxes = sorted(((b[0][1], b[0][0], b[3][1] - b[0][1], t) for b, t, sc in found if float(sc) > 0.5), key=lambda z: (z[0], z[1]))
    lines = []
    for y, x, h, t in boxes:
        if lines and abs(lines[-1]["y"] - y) < 0.6 * max(h, lines[-1]["h"]):
            lines[-1]["parts"].append((x, t)); lines[-1]["h"] = max(h, lines[-1]["h"])
        else:
            lines.append({"y": y, "h": h, "parts": [(x, t)]})
    return [" ".join(t for _, t in sorted(l["parts"])) for l in lines]


# ---------- images ----------
def _image_key(info):
    """Properties shared by Page.get_image_info() and Page.get_images()."""
    cs = str(info.get("cs-name", "")).lower()
    # get_images() reports just "ICCBased", while get_image_info() includes
    # the embedded ICC profile description in its cs-name value.
    if cs.startswith("iccbased"):
        cs = "iccbased"
    return (int(info.get("width", 0)), int(info.get("height", 0)),
            int(info.get("bpc", 0)), cs)


def _image_infos_with_xrefs(page):
    """Fast image placement lookup with an exact, slower fallback for ambiguous pages.

    PyMuPDF's xrefs=True mode calculates image hashes to map placements to xrefs.
    Most PDFs have unique image dimensions / bit depth / colorspace per page, so
    we can map those safely from the page resource list without hashing. If two
    different resources share the same metadata, use PyMuPDF's exact lookup.
    """
    infos = page.get_image_info(hashes=False, xrefs=False)
    if not infos:
        return infos
    resources = collections.defaultdict(set)
    for item in page.get_images(full=True):
        if not item or not item[0]:
            continue
        try:
            key = (int(item[2]), int(item[3]), int(item[4]), str(item[5]).lower())
            resources[key].add(int(item[0]))
        except (IndexError, TypeError, ValueError):
            # Unknown resource metadata: let PyMuPDF perform the exact lookup.
            return page.get_image_info(xrefs=True)

    mapped = []
    for info in infos:
        matches = resources.get(_image_key(info), set())
        if len(matches) != 1:
            # Ambiguous resources or an unmatched inline/unusual image: do not guess.
            # The exact method also handles nested forms and returns xref=0 for inline images.
            return page.get_image_info(xrefs=True)
        item = dict(info)
        item["xref"] = next(iter(matches)) if matches else 0  # inline / unresolvable image
        mapped.append(item)
    return mapped


def page_images(doc, page, usage, outdir, stem, pno, saved):
    """Extract placed images efficiently, preserving original encoded bytes when safe.

    Older code decoded every image to a Pixmap and re-encoded it as PNG. For ordinary
    JPEG/PNG images that wastes CPU and often creates much larger files. Images with
    masks or browser-unfriendly formats still use the Pixmap conversion fallback.
    """
    found = []
    # Map xrefs without expensive hashes where the PDF's image resources are unambiguous.
    for info in _image_infos_with_xrefs(page):
        xref = info.get("xref")
        r = fitz.Rect(info["bbox"]) & page.rect
        if not xref or r.is_empty or r.width < MIN_IMG_PT or r.height < MIN_IMG_PT or usage[xref] > MAX_IMG_PAGES:
            continue
        if xref not in saved:
            try:
                extracted = doc.extract_image(xref)
                ext = (extracted.get("ext") or "").lower()
                # These formats can be embedded directly by Obsidian/browser engines.
                # Avoid decoding/re-encoding unless an alpha/color mask must be applied.
                direct_ext = {"jpeg": "jpg", "jpg": "jpg", "png": "png", "gif": "gif",
                              "bmp": "bmp", "webp": "webp"}
                can_copy = (ext in direct_ext and info.get("colorspace") in (1, 3)
                            and not extracted.get("smask", 0) and not info.get("has-mask", False))
                os.makedirs(outdir, exist_ok=True)
                if can_copy:
                    name = f"{stem}_s{pno}_{len(saved)+1}.{direct_ext[ext]}"
                    with open(os.path.join(outdir, name), "wb") as fh:
                        fh.write(extracted["image"])
                else:
                    pix = fitz.Pixmap(doc, xref)
                    sm = extracted.get("smask", 0)
                    if sm:
                        pix = fitz.Pixmap(pix, fitz.Pixmap(doc, sm))
                    if pix.colorspace and pix.colorspace.n > 3:
                        pix = fitz.Pixmap(fitz.csRGB, pix)
                    name = f"{stem}_s{pno}_{len(saved)+1}.png"
                    pix.save(os.path.join(outdir, name))
                saved[xref] = name
            except Exception:
                continue
        found.append((saved[xref], r))
    return found


def snapshot(page, outdir, stem, pno):
    os.makedirs(outdir, exist_ok=True)
    name = f"{stem}_s{pno}_page.png"
    page.get_pixmap(dpi=110).save(os.path.join(outdir, name))
    return name


def place_images(entries, imgs):
    """Images beside text go to the end of the slide; images above/below text go where they sit."""
    for name, r in sorted(imgs, key=lambda x: (-x[1].y0, -x[1].x0)):
        beside = any(e["kind"] != "img" and r.y0 - 5 <= e["y"] <= r.y1 + 5 for e in entries)
        pos = len(entries)
        if not beside:
            idx = [i for i, e in enumerate(entries) if e["y"] < r.y0]
            pos = (max(idx) + 1) if idx else 0
        entries.insert(pos, {"kind": "img", "name": name, "y": r.y0})
    return entries


# ---------- main ----------
def convert(path, images=True, ocr=True, outdir=None, stem=None):
    filename = os.path.basename(path)
    report_stage(f"Opening {filename}")
    doc = fitz.open(path)
    stem = stem or os.path.splitext(filename)[0]
    base = os.path.join(outdir or os.path.dirname(os.path.abspath(path)), stem)
    imgdir = base + "_images"
    n = len(doc)
    print(f"\n[INFO] {filename}: {n} pages detected")
    report_stage("Reading page text, formatting and layout")
    pages = []
    scan_stride = max(1, n // 18)
    for i in range(n):
        pages.append(page_content(doc[i]))
        if progress_cb: progress_cb(0.02 + 0.23 * (i + 1) / max(1, n))
        if (i + 1) % scan_stride == 0 or i + 1 == n:
            msg = f"Analysed page text and layout: {i + 1}/{n}"
            if status_cb:
                try: status_cb(msg)
                except Exception: pass
            print(f"[INFO] {msg}")
    report_stage("Detecting repeated headers and animation build-ups")
    cnt = collections.Counter()
    for ents in pages:
        for t in {plain(e) for e in ents if e["kind"] == "row"}: cnt[t] += 1
    bad = {t for t, c in cnt.items() if c > WATERMARK_FRAC * n and len(t) < 40} | {"Loading…", "Loading..."}
    pages = [[e for e in ents if not (e["kind"] == "row" and plain(e) in bad)] for ents in pages]
    keep = []
    for i, ents in enumerate(pages):                      # drop animation build-up slides
        cur = {plain(e) for e in ents}
        nxt = {plain(e) for e in pages[i + 1]} if i + 1 < n else set()
        if cur and nxt and cur <= nxt: continue
        keep.append(i)
    print(f"[SUCCESS] Slide filtering complete: {len(keep)} kept, {n - len(keep)} build-up slides removed")
    usage = collections.Counter()
    if images:
        report_stage("Indexing embedded images")
        for i in keep:
            for im in doc[i].get_images(): usage[im[0]] += 1
        print(f"[INFO] Found {len(usage)} distinct embedded image resources")
    saved, textless, ncolor, ntables, nocr = {}, [], 0, 0, 0
    out = {"obs": [], "chat": []}
    report_stage("Extracting images and building Markdown")
    process_stride = max(1, len(keep) // 18)
    for ki, i in enumerate(keep):
        if progress_cb: progress_cb(0.27 + 0.62 * (ki + 1) / max(1, len(keep)))
        if ki % process_stride == 0 or ki + 1 == len(keep):
            msg = f"Processing slide {ki + 1}/{len(keep)} (PDF page {i + 1})"
            if status_cb:
                try: status_cb(msg)
                except Exception: pass
            print(f"[INFO] {msg}")
        ents = list(pages[i])
        page = doc[i]
        notext = not ents
        imgs = page_images(doc, page, usage, imgdir, stem, i + 1, saved) if images else []
        if notext:
            textless.append(i + 1)
            if images and not imgs: imgs = [(snapshot(page, imgdir, stem, i + 1), page.rect)]
        if notext and ocr:
            print(f"[INFO] OCR: reading picture-only page {i + 1}")
            lines_found = ocr_lines(page)
            if lines_found: ents.append({"kind": "ocr", "lines": lines_found, "y": 1e9}); nocr += 1
        place_images(ents, imgs)
        if not ents: continue
        rows = [e for e in ents if e["kind"] == "row"]
        sizes = sorted(r["size"] for r in rows) or [0]
        body = sizes[len(sizes) // 2]
        xs = sorted({round(r["x"] / 10) for r in rows if r["marker"]})
        for mode in out:
            lines, first = [], True
            for k, e in enumerate(ents):
                if e["kind"] == "img":
                    if mode == "obs": lines.append(f"\n![[{e['name']}]]\n")
                    continue
                if e["kind"] == "table":
                    lines.append(render_table(e, mode)); continue
                if e["kind"] == "ocr":
                    lines.append("\n> *Text found in image:*\n" + "\n".join("> " + t for t in e["lines"]) + "\n"); continue
                allbold = all(st[1] for _, st in e["segs"] if st)
                head = (first and k == 0 and not e["marker"] and not e["num"] and len(plain(e)) < 100
                        and e["y"] < 0.35 * page.rect.height and (e["size"] >= body * 1.15 or allbold))
                txt = render_segs(e["segs"], mode, head)
                if not txt: continue
                if head: lines.append("\n## " + txt + "\n")
                elif e["marker"]:
                    lvl = 1 if e["sub"] else (xs.index(round(e["x"] / 10)) if round(e["x"] / 10) in xs else 0)
                    lines.append("  " * min(lvl, 3) + "- " + txt)
                else: lines.append(txt)
                first = False
            if notext and mode == "chat" and not any(e["kind"] == "ocr" for e in ents): lines.append("[slide has no text, picture only]")
            out[mode].append("\n".join(lines).strip())
    report_stage("Writing Obsidian and compact Markdown files")
    if progress_cb: progress_cb(0.94)
    for mode, suffix in (("obs", "_obsidian.md"), ("chat", "_chat.md")):
        text = "\n\n---\n\n".join(p for p in out[mode] if p).strip() + "\n"
        text = re.sub(r"(?<![A-Za-z0-9])o(?=[A-Z][a-z])", "\x00", text).replace("\n\x00", "\n  - ").replace("\x00", "\n  - ")
        text = re.sub(r"\n{3,}", "\n\n", text)
        if mode == "chat":
            tags = sorted(set(re.findall(r"<((?:bg-)?[a-z]+)>", text)))
            if tags:
                text = ("Legend: " + "; ".join((f"<{t}> = {t[3:]} highlight" if t.startswith("bg-") else f"<{t}> = {t} text") for t in tags)
                        + "; ** = bold; * = italic. These mark what the teacher emphasized.\n\n") + text
        else:
            ncolor = len(re.findall(r'<span style|==|<mark', text)); ntables = len(re.findall(r"\n\| --- ", text))
        with open(base + suffix, "w", encoding="utf-8") as f: f.write(text)
        print(f"[SUCCESS] Saved {os.path.basename(base + suffix)} ({len(text)} characters, about {len(text)//4} tokens)")
        if progress_cb: progress_cb(0.94 + (0.05 if mode == "obs" else 0.06))
    print(f"[SUCCESS] {len(keep)} slides kept ({n - len(keep)} animation build-ups removed), {ncolor} colored/highlighted pieces, "
          f"{ntables} tables, {len(saved)} images saved, {nocr} picture-only slides OCR'd")
    if textless:
        print(f"[WARN] Slides with no text layer: {textless[:25]}{' ...' if len(textless) > 25 else ''}"
              + (" (kept as pictures)" if images else "") + (" (OCR is off)" if not ocr else ""))
    if progress_cb: progress_cb(1.0)
    report_stage(f"Finished {filename}")


# ---------- combining several PDFs into one ----------
def natural_key(text):
    """Sort key so that 'Lecture 2' comes before 'Lecture 10'."""
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", text)]


def order_indices(names, mode="name", numbers=None):
    """Return the positions of names in the order they should be merged.
    mode: 'name' (natural sort by file name), 'given' (as listed), 'numbers' (sort by the numbers typed, ties keep list order)."""
    idx = list(range(len(names)))
    if mode == "name": idx.sort(key=lambda i: natural_key(os.path.basename(names[i])))
    elif mode == "numbers" and numbers: idx.sort(key=lambda i: (float(numbers[i]), i))
    return idx


def legend_line(text):
    tags = sorted(set(re.findall(r"<((?:bg-)?[a-z]+)>", text)))
    if not tags: return ""
    return ("Legend: " + "; ".join((f"<{t}> = {t[3:]} highlight" if t.startswith("bg-") else f"<{t}> = {t} text") for t in tags)
            + "; ** = bold; * = italic. These mark what the teacher emphasized.\n\n")


def merge_texts(parts, mode):
    """parts = [(title, markdown text)] in final order. One title heading per file; one shared legend for chat mode."""
    chunks = []
    for title, text in parts:
        if mode == "chat": text = re.sub(r"\ALegend: [^\n]*\n\n", "", text)
        chunks.append(f"# {title}\n\n{text.strip()}")
    out = "\n\n---\n\n".join(chunks) + "\n"
    return (legend_line(out) + out) if mode == "chat" else out


def clean_name(text, default="Combined"):
    return (re.sub(r"[^\w\-. ()]+", "_", text).strip(" ._") or default)[:80]


def combine_files(files, name="Combined", images=True, ocr=True, out_dir=None, order="name", numbers=None):
    """Convert several PDFs and write ONE combined Obsidian note, ONE combined chat file and one images folder."""
    global progress_cb
    import shutil, tempfile
    ordered = [files[i] for i in order_indices(files, order, numbers)]
    out_dir = out_dir or os.path.dirname(os.path.abspath(ordered[0]))
    name = clean_name(name)
    outer, n, used = progress_cb, len(ordered), set()
    obs_parts, chat_parts = [], []
    print("[INFO] Merge order: " + "  ->  ".join(f"{k + 1}. {os.path.basename(f)}" for k, f in enumerate(ordered)))
    try:
        with tempfile.TemporaryDirectory() as tmp:
            img_out = os.path.join(out_dir, name + "_images")
            for k, f in enumerate(ordered):
                report_stage(f"Converting PDF {k + 1}/{n}: {os.path.basename(f)}")
                title = clean_name(os.path.splitext(os.path.basename(f))[0], "slides")
                stem, j = title, 2
                while stem in used: stem = f"{title}_{j}"; j += 1
                used.add(stem)
                progress_cb = (lambda fr, k=k: outer((k + fr) / n)) if outer else None
                try:
                    convert(f, images, ocr, outdir=tmp, stem=stem)
                except Exception as e:
                    print(f"[ERROR] Failed on {f}: {e}"); continue
                print(f"[SUCCESS] Converted {os.path.basename(f)}; preparing its combined section")
                obs_text = open(os.path.join(tmp, stem + "_obsidian.md"), encoding="utf-8").read()
                chat_text = open(os.path.join(tmp, stem + "_chat.md"), encoding="utf-8").read()
                # Images are gathered into one shared folder beside the combined note.
                # Update Obsidian embeds so they still resolve after being moved.
                image_folder = name + "_images"
                obs_text = re.sub(
                    r"!\[\[(.*?)\]\]",
                    lambda m: f"![[{image_folder}/{m.group(1)}]]",
                    obs_text
                )
                obs_parts.append((title, obs_text))
                chat_parts.append((title, chat_text))
                d = os.path.join(tmp, stem + "_images")
                if os.path.isdir(d):
                    os.makedirs(img_out, exist_ok=True)
                    for fn in os.listdir(d): shutil.move(os.path.join(d, fn), os.path.join(img_out, fn))
    finally:
        progress_cb = outer
    if not obs_parts: raise RuntimeError("None of the PDFs could be converted.")
    report_stage("Merging converted sections and finalising shared image links")
    paths = {}
    for mode, parts, suffix in (("obs", obs_parts, "_obsidian.md"), ("chat", chat_parts, "_chat.md")):
        text = merge_texts(parts, mode)
        paths[mode] = os.path.join(out_dir, name + suffix)
        with open(paths[mode], "w", encoding="utf-8") as fh: fh.write(text)
        print(f"[SUCCESS] Combined file written: {paths[mode]} ({len(text)} characters, about {len(text)//4} tokens)")
    if progress_cb: progress_cb(1.0)
    report_stage("Combined export complete")
    return paths


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    flags = {a for a in sys.argv[1:] if a.startswith("--")}
    if "--help" in flags or "-h" in sys.argv: sys.exit(__doc__)
    files = []
    for a in (args or ["."]):
        if os.path.isdir(a): files += sorted(glob.glob(os.path.join(a, "*.pdf")))
        else: files += sorted(glob.glob(a)) or [a]
    if not files: sys.exit("No PDF files found. Put this script in the folder with your PDFs, or pass a file name.")
    if "--combine" in flags:
        name = next((a[7:] for a in flags if a.startswith("--name=")), "Combined")
        order = next((a[8:] for a in flags if a.startswith("--order=")), "name")
        combine_files(files, name, images="--no-images" not in flags, ocr="--no-ocr" not in flags, order=order)
        sys.exit(0)
    for f in files:
        try: convert(f, images="--no-images" not in flags, ocr="--no-ocr" not in flags)
        except Exception as e: print(f"  FAILED on {f}: {e}")
