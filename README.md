# PDF slides to Markdown (colors kept)

The web app has the same layout and features as the desktop window: switches for images / OCR / combine, numbering for combined files, a live progress card and a colored activity log.

Web app that turns lecture-slide PDFs into Markdown that keeps text colors, bold, italic, highlights, tables and images,
and reads the text inside picture-only slides (OCR). It makes an Obsidian version and a short-tag version for chatbots.

## Put it online for free (Streamlit Community Cloud)

GitHub Pages cannot run this (it only serves static pages), so the app runs on Streamlit Community Cloud, which deploys straight from a GitHub repo.

1. Make a free account at github.com and create a **New repository** (name it `pdf2md`; public or private both work).
2. In the repo click **Add file > Upload files**, drag in all the files from this folder (`app.py`, `runner.py`, `pdf2md_core.py`,
   `requirements.txt`, `packages.txt`, `README.md`) **and the `.streamlit` folder** (it holds the upload-size limit and the blue accent color; if your computer hides
   it, skip it, the app still checks sizes itself and sets its own colors) and click **Commit changes**.
3. Go to https://share.streamlit.io and sign in with GitHub.
4. Click **Create app**, choose your repo, branch `main`, main file `app.py`.
5. Open **Advanced settings** and pick **Python 3.12**, then click **Deploy**. The first build takes a few minutes.

You get a link like `https://yourname-pdf2md.streamlit.app`. Open it, upload PDFs, click Convert, download the results.

## Combining several PDFs into one
Tick **Combine all the PDFs into one file**, choose how they are numbered (by file name, upload order, or numbers you type), and you get one Obsidian note and one chatbot file with the PDFs in that order. Each PDF starts under its own `# file name` heading.

## What happens when many people use it at once
- **One conversion runs at a time.** Everyone else waits in a line and sees their place and an estimated wait. If they close the tab they drop out of the line automatically.
- **The line has a limit** (5 waiting). Anyone beyond that is told the converter is full and to come back in a few minutes, instead of crashing the server.
- **Per-file limits:** 60 MB, 400 pages, 6 files and 150 MB in total per click. A single PDF that runs longer than 15 minutes is stopped so it cannot block everyone.
- **Finished results are kept in a shared store** with a total size cap, and are deleted after 30 minutes. Uploaded files are dropped from memory after each run.
- All limits are the constants at the top of `runner.py`.
- Honest limit: this is still one small free server. Dozens of people just having the page open at once can use up its memory, and Streamlit may then
  show a "resource limits" page for a while. For a class-sized group it should be fine. For more, deploy a second copy for another group or move to a paid host.

## Good to know
- Your PDFs are processed on Streamlit's servers and are not kept by the app. The link is public by default; in the app's
  **Share** settings you can restrict it to specific email addresses.
- The free tier has about 1 GB of memory. For very large decks, untick OCR or split the PDF.
- It needs PDFs with a text layer (PowerPoint, Word, Google Slides, LaTeX exports). Colors cannot be recovered from pictures.
- If the build fails on `packages.txt`, delete that file (it only supplies libraries the OCR needs) and redeploy; OCR may then be unavailable.
- Also works on Hugging Face Spaces (choose the Streamlit SDK and upload the same files).

## Run it on your own computer instead
    pip install streamlit pymupdf rapidocr onnxruntime
    streamlit run app.py
