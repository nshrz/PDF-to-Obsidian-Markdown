# PDF slides to Markdown (colors kept)

Web app that turns lecture-slide PDFs into Markdown that keeps text colors, bold, italic, highlights, tables and images,
and reads the text inside picture-only slides (OCR). It makes an Obsidian version and a short-tag version for chatbots.

## Put it online for free (Streamlit Community Cloud)

GitHub Pages cannot run this (it only serves static pages), so the app runs on Streamlit Community Cloud, which deploys straight from a GitHub repo.

1. Make a free account at github.com and create a **New repository** (name it `pdf2md`; public or private both work).
2. In the repo click **Add file > Upload files**, drag in all the files from this folder (`app.py`, `runner.py`, `pdf2md_core.py`,
   `requirements.txt`, `packages.txt`, `README.md`) and click **Commit changes**.
3. Go to https://share.streamlit.io and sign in with GitHub.
4. Click **Create app**, choose your repo, branch `main`, main file `app.py`.
5. Open **Advanced settings** and pick **Python 3.12**, then click **Deploy**. The first build takes a few minutes.

You get a link like `https://yourname-pdf2md.streamlit.app`. Open it, upload PDFs, click Convert, download the results.

## Good to know
- Your PDFs are processed on Streamlit's servers and are not kept by the app. The link is public by default; in the app's
  **Share** settings you can restrict it to specific email addresses.
- The free tier has about 1 GB of memory. For very large decks, untick OCR or split the PDF.
- The app does one conversion at a time, so others using the link wait their turn.
- It needs PDFs with a text layer (PowerPoint, Word, Google Slides, LaTeX exports). Colors cannot be recovered from pictures.
- If the build fails on `packages.txt`, delete that file (it only supplies libraries the OCR needs) and redeploy; OCR may then be unavailable.
- Also works on Hugging Face Spaces (choose the Streamlit SDK and upload the same files).

## Run it on your own computer instead
    pip install streamlit pymupdf rapidocr onnxruntime
    streamlit run app.py
