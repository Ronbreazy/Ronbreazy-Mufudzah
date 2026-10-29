# Knowledge base

This folder is the chatbot's reference library. Put your legal material here
as ordinary text files, **restart the backend**, and the chatbot will search
these files when someone asks a question.

- Supported files: `.md`, `.txt` and `.pdf` (see "PDF files" below)
- Files starting with `_` and README files (like this one) are ignored
- The backend prints `Knowledge base: N entries loaded` when it starts, so
  you can see your files were picked up

## How to write a file

See `_EXAMPLE_format.md` in this folder (copy it, rename it without the
leading underscore, and replace the text with your own).

The short version:

1. Optionally start with `Title:` and `Source:` lines. The **source** is
   what the chatbot shows as where an answer came from, so name the real Act,
   rule or document.
2. Use headings (`##`) to split the file into topics. Word a heading like the
   question a person would ask: `## Where do I apply for a protection order?`
3. Keep each section short and in plain language.

## Tips for good answers

- **Ask-and-match:** the chatbot finds entries by the words they share with
  the question, so use the words people actually use. If people say "peace
  order" and "protection order", mention both.
- **Only put in what you have checked.** The chatbot repeats what is in these
  files. If a file is wrong, its answers will be wrong.
- **Always fill in `Source:`** so users can check the original.
- Check the terms of any website you copy from before copying large amounts
  of text. Summarising in your own words is usually safer.
- After editing a file, restart the backend. Deleted files disappear from the
  chatbot on the next start.

## PDF files

You can drop PDFs straight into this folder, for example an Act or a judgment.

1. Install the PDF reader once: `pip install pypdf`
2. Name the file after the document (e.g. `Domestic Violence Act.pdf`). The
   file name becomes the title, and answers are labelled with the page, such
   as "Domestic Violence Act, page 12", so people can check the original.
3. Restart the backend. You'll see `Knowledge base: read <file> (N entries)`.

Things to know:
- **Only PDFs that contain real text work.** If you can select and copy text
  in the PDF, it's fine. A scanned document (a photo of each page) has no text
  to read, and the backend will say so and skip it. Run such a file through
  OCR first (a "make searchable" or "recognise text" option in a PDF tool),
  or type the important parts into a `.md` file.
- **Check what was read before relying on it.** PDFs don't store paragraphs,
  only the position of each line, so the text is pieced back together
  (running headers, footers and page numbers are removed, split words are
  repaired). That works well for ordinary text but not perfectly. To see
  exactly what the chatbot will learn from a file, run:
  `python -m app.knowledge "knowledge_base/Domestic Violence Act.pdf"`
- **Tables, columns and forms read poorly.** Text in a table or in
  side-by-side columns can come out jumbled. For those, a hand-written `.md`
  summary is better.
- **A PDF can't carry a `Source:` line**, so the file name is the source. Name
  it properly.
- Files are only read again when they change, so restarts stay fast even
  with long documents.
- A PDF that can't be read never stops the backend; it's skipped with a
  message in the terminal.
