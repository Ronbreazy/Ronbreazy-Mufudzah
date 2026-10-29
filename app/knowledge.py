"""
knowledge.py
------------
The chatbot's reference library. You put your legal material into files in
the `knowledge_base/` folder; when the backend starts, this module reads
them, cuts them into short entries, and stores them in the database. The
chatbot then searches those entries (see app/retrieval.py) and uses what it
finds to answer.

Supported files:
  .md / .txt  plain text you write yourself (format below)
  .docx       a Word document; Heading 1/2 paragraphs split it into sections
  .pdf        e.g. an Act or a judgment. Needs `pip install pypdf`. Only PDFs
              that contain real text work -- a scanned page (a photo of a
              page) has no text to read and is skipped with a message.

Text file format (.md or .txt) -- everything is optional except the text:

    Title: Protection orders
    Source: Domestic Violence Act [Chapter 5:16]

    # Protection orders

    ## Who can apply?
    Any person who is in a domestic relationship with the respondent...

  - `Title:` and `Source:` lines at the very top are optional. The source is
    what the chatbot shows as the origin of an answer, so name the Act, rule,
    or document the text came from.
  - Headings (#, ##, ...) split the file into sections. Phrasing a heading
    like the question a person would ask ("Where do I apply?") helps the
    chatbot find it.

PDFs have no headings we can rely on, so each PDF page becomes its own
entries, labelled with the page number ("Domestic Violence Act, page 12") so
a person can check the original. Name the PDF after the document -- the file
name is used as its title.

Files whose name starts with an underscore, and README files, are ignored.

To check what was read from a file before relying on it:
    python -m app.knowledge knowledge_base/SomeAct.pdf
"""

import hashlib
import os
import re
import statistics
import sys

from app import config
from app import database
from app.textutil import split_into_chunks

_SUPPORTED_EXTENSIONS = (".md", ".txt", ".pdf", ".docx")
# Files people often drop in that can't be read; reported, not silently ignored.
_UNSUPPORTED_HINTS = (".doc", ".rtf", ".odt", ".pptx", ".ppt", ".xlsx", ".xls", ".jpg", ".jpeg", ".png")
_HEADING = re.compile(r"^\s{0,3}(#{1,6})\s+(.+?)\s*#*\s*$")
_META_LINE = re.compile(r"^\s*(title|source)\s*:\s*(.+?)\s*$", re.IGNORECASE)

# Bump this whenever the way files are read or cleaned changes, so files that
# were read by an older version get read again instead of keeping old results.
PARSER_VERSION = "4"


class KnowledgeFileError(Exception):
    """A file that can't be used, with a message a person can act on."""


def _prettify(file_stem):
    """Turns a file name into a title: "my_notes" -> "My Notes". Words that
    already have a capital letter after the first (IECMS, ZWLA, ...) are left
    exactly as written, so "IECMS Guide.pdf" doesn't become "Iecms Guide"."""
    words = file_stem.replace("_", " ").replace("-", " ").split()
    return " ".join(w if any(c.isupper() for c in w[1:]) else w.capitalize() for w in words)


# ---------------------------------------------------------------------------
# Text files (.md / .txt)
# ---------------------------------------------------------------------------

def _parse_text_file(path, lines=None):
    file_name = os.path.basename(path)
    if lines is None:
        with open(path, encoding="utf-8-sig", errors="replace") as f:
            lines = f.read().splitlines()

    # Optional "Title:" / "Source:" lines at the very top.
    meta = {}
    i = 0
    while i < len(lines):
        m = _META_LINE.match(lines[i])
        if not m:
            break
        meta[m.group(1).lower()] = m.group(2)
        i += 1
    body = lines[i:]

    # Split the rest into sections at each heading.
    sections = []  # (level, heading, [lines])
    level, heading, current = 0, None, []
    for line in body:
        h = _HEADING.match(line)
        if h:
            sections.append((level, heading, current))
            level, heading, current = len(h.group(1)), h.group(2), []
        else:
            current.append(line)
    sections.append((level, heading, current))

    doc_title = meta.get("title")
    if not doc_title:
        doc_title = next((hd for lv, hd, _ in sections if lv == 1 and hd), None)
    if not doc_title:
        doc_title = _prettify(os.path.splitext(file_name)[0])
    source = meta.get("source") or doc_title

    entries = []
    for _lv, heading, section_lines in sections:
        text = "\n".join(section_lines).strip()
        if not text:
            continue
        title = doc_title if (not heading or heading == doc_title) else f"{doc_title}: {heading}"
        for chunk in split_into_chunks(text, config.KNOWLEDGE_CHUNK_CHARS):
            entries.append({"title": title, "content": chunk, "source": source, "file_name": file_name})
    return entries


# ---------------------------------------------------------------------------
# Word files (.docx)
# ---------------------------------------------------------------------------
# A .docx is a zip holding word/document.xml. Each paragraph is read in
# order; paragraphs styled as headings ("Heading 1", "Heading 2", "Title")
# become "#"/"##" lines, so a Word document is split into entries at its
# headings exactly like a .md file. Table cells are read as text too.

_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def _docx_lines(path):
    import zipfile
    import xml.etree.ElementTree as ET
    try:
        with zipfile.ZipFile(path) as z:
            root = ET.fromstring(z.read("word/document.xml"))
            styles = {}
            if "word/styles.xml" in z.namelist():
                for st in ET.fromstring(z.read("word/styles.xml")).iter(_W + "style"):
                    name = st.find(_W + "name")
                    styles[st.get(_W + "styleId")] = (name.get(_W + "val") if name is not None else "")
    except (zipfile.BadZipFile, KeyError, ET.ParseError) as e:
        raise KnowledgeFileError(f"it could not be read as a Word document ({type(e).__name__}). "
                                 "If it is an old .doc file, open it in Word and Save As .docx")

    lines = []
    for para in root.iter(_W + "p"):
        text = "".join(
            (t.text or "") if t.tag == _W + "t" else ("\t" if t.tag == _W + "tab" else "\n")
            for t in para.iter() if t.tag in (_W + "t", _W + "tab", _W + "br", _W + "cr")
        ).strip()
        if not text:
            lines.append("")
            continue
        style_el = para.find(f"{_W}pPr/{_W}pStyle")
        style_id = style_el.get(_W + "val") if style_el is not None else ""
        style = (styles.get(style_id) or style_id or "").lower().replace(" ", "")
        if style == "title" or style == "heading1":
            lines.append("# " + text.replace("\n", " "))
        elif style.startswith("heading"):
            lines.append("## " + text.replace("\n", " "))
        else:
            if para.find(f"{_W}pPr/{_W}numPr") is not None:
                text = "- " + text  # a Word bullet or numbered-list item
            lines.extend(text.split("\n"))
            lines.append("")
    return lines


def _parse_docx_file(path):
    lines = _docx_lines(path)
    if sum(len(l) for l in lines) < 20:
        raise KnowledgeFileError("it has no readable text")
    # A document with only one Heading 1 is the document title, and its
    # Heading 1 sections are what should split it; with several Heading 1s
    # each one becomes its own section.
    h1 = [l for l in lines if l.startswith("# ")]
    if len(h1) > 1:
        lines = ["#" + l if l.startswith("# ") else l for l in lines]
    return _parse_text_file(path, lines)


# ---------------------------------------------------------------------------
# PDF files
# ---------------------------------------------------------------------------
# A PDF stores where each line of text sits on the page, not paragraphs. What
# comes out of extraction is one line per visual line, words split with
# hyphens at line ends, and headers/footers/page numbers repeated on every
# page. The functions below tidy that into readable paragraphs so a passage
# makes sense when the chatbot shows or quotes it.

_PAGE_NUMBER_LINE = re.compile(r"^\s*(page\s+)?\d{1,4}(\s*(of|/)\s*\d{1,4})?\s*$", re.IGNORECASE)
_LIST_START = re.compile(r"^(\(?\d{1,3}[.)]|\([a-z]{1,3}\)|\(\d{1,3}\))\s+")
_SECTION_HEADING = re.compile(r"^\d{1,3}[.]\s+\S")
_ENDS_SENTENCE = re.compile(r"[.:;!?”\")]$")
_DOT_LEADER = re.compile(r"\.{5,}")


def _is_contents_page(lines):
    """A table of contents (or of figures) is a list of titles with dotted
    leaders running to page numbers. Its words are the same as the headings
    it lists, so left in, it would be shown instead of the real section. A
    page with 3+ such lines is dropped."""
    return sum(1 for line in lines if _DOT_LEADER.search(line)) >= 3


def _shape(line):
    """A line with its digits blanked out, so "Page 3" and "Page 14" count
    as the same line when looking for repeated headers and footers."""
    return re.sub(r"\d+", "#", line.lower()).strip()


def _edge_indexes(lines):
    """Positions of the first two and last two non-blank lines of a page --
    the only places a running header or footer can be."""
    filled = [i for i, line in enumerate(lines) if line.strip()]
    return set(filled[:2] + filled[-2:])


def _remove_headers_and_footers(pages_lines):
    """Drops page numbers, plus running headers/footers: a short line at the
    very top or bottom of the page that repeats (ignoring digits) on at least
    half the pages. Only done for documents of 4+ pages, where "repeats on
    half the pages" really does mean a header or footer, and only at the page
    edges, so a line in the body of a page is never removed.
    """
    cleaned = [[l for l in lines if not _PAGE_NUMBER_LINE.match(l)] for lines in pages_lines]
    if len(cleaned) < 4:
        return cleaned

    seen_on = {}
    for lines in cleaned:
        for shape in {_shape(lines[i]) for i in _edge_indexes(lines)}:
            seen_on[shape] = seen_on.get(shape, 0) + 1
    repeated = {shape for shape, n in seen_on.items() if n >= len(cleaned) / 2 and len(shape) <= 100}

    result = []
    for lines in cleaned:
        edges = _edge_indexes(lines)
        result.append([l for i, l in enumerate(lines) if not (i in edges and _shape(l) in repeated)])
    return result


def _reflow(lines):
    """Joins the page's visual lines into paragraphs.

    A new paragraph starts at a blank line, at a numbered/lettered clause such
    as "12." or "(a)" after a finished sentence, or after a line that ends a
    sentence AND is noticeably shorter than the page's normal line width (the
    short last line of a paragraph). A short numbered line with no full stop
    ("3. Application for a licence") is a section heading and stays on its own
    line. Words split by a hyphen at a line end are stitched back together.
    """
    lengths = [len(l) for l in lines if l.strip()]
    if not lengths:
        return []
    typical = statistics.median(lengths) if len(lengths) >= 5 else max(lengths)

    def looks_like_heading(line):
        return (bool(_SECTION_HEADING.match(line)) and not _ENDS_SENTENCE.search(line)
                and len(line) < 0.6 * typical)

    paragraphs, buf, prev_len, buf_is_heading = [], "", 0, False
    for raw in lines:
        line = raw.strip()
        if not line:
            if buf:
                paragraphs.append(buf)
                buf = ""
            continue
        if not buf:
            buf, prev_len, buf_is_heading = line, len(line), looks_like_heading(line)
            continue
        ends_sentence = bool(_ENDS_SENTENCE.search(buf))
        starts_new_clause = ends_sentence and bool(_LIST_START.match(line))
        short_last_line = ends_sentence and prev_len < 0.75 * typical and line[0].isupper()
        if buf_is_heading or starts_new_clause or short_last_line:
            paragraphs.append(buf)
            buf, buf_is_heading = line, looks_like_heading(line)
        elif re.search(r"[A-Za-z]-$", buf) and line[0].islower():
            buf = buf[:-1] + line  # "licen-" + "ce" -> "licence"
        else:
            buf = buf + " " + line
        prev_len = len(line)
    if buf:
        paragraphs.append(buf)
    return [re.sub(r"[ \t]+", " ", p).strip() for p in paragraphs if p.strip()]


def _parse_pdf_file(path):
    try:
        from pypdf import PdfReader
    except ImportError:
        raise KnowledgeFileError("PDF files need the 'pypdf' package -- install it with: pip install pypdf")

    file_name = os.path.basename(path)
    try:
        reader = PdfReader(path)
        if reader.is_encrypted:
            # Many PDFs are "encrypted" with an empty password just to stop
            # editing; those open fine. A real password does not.
            if not reader.decrypt(""):
                raise KnowledgeFileError("it is password-protected")
        raw_pages = [(page.extract_text() or "") for page in reader.pages]
    except KnowledgeFileError:
        raise
    except Exception as e:  # noqa: BLE001 - pypdf raises many different errors for damaged files
        raise KnowledgeFileError(f"it could not be read as a PDF ({type(e).__name__}: {e})")

    pages_lines = [[l.rstrip() for l in text.splitlines()] for text in raw_pages]
    pages_lines = _remove_headers_and_footers(pages_lines)

    total_text = sum(len(l.strip()) for lines in pages_lines for l in lines)
    if not raw_pages or total_text < 20 * len(raw_pages):
        raise KnowledgeFileError(
            "it has no readable text -- it is probably a scanned document (an image of the pages). "
            "Run it through OCR first (for example, a 'make searchable' option in a PDF tool), "
            "or type the important parts into a .md file")

    doc_title = _prettify(os.path.splitext(file_name)[0])
    entries = []
    for page_number, lines in enumerate(pages_lines, start=1):
        if _is_contents_page(lines):
            continue
        paragraphs = _reflow(lines)
        if not paragraphs:
            continue
        for chunk in split_into_chunks("\n\n".join(paragraphs), config.KNOWLEDGE_CHUNK_CHARS):
            entries.append({
                "title": f"{doc_title} (page {page_number})",
                "content": chunk,
                "source": f"{doc_title}, page {page_number}",
                "file_name": file_name,
            })
    return entries


# ---------------------------------------------------------------------------
# Loading the folder
# ---------------------------------------------------------------------------

def parse_file(path):
    """Reads one file and returns its entries:
    [{title, content, source, file_name}, ...]. Raises KnowledgeFileError
    (with a message a person can act on) if the file can't be used.
    """
    lower = path.lower()
    if lower.endswith(".pdf"):
        return _parse_pdf_file(path)
    if lower.endswith(".docx"):
        return _parse_docx_file(path)
    return _parse_text_file(path)


def _usable_names(directory):
    if not os.path.isdir(directory):
        return []
    return [
        name for name in sorted(os.listdir(directory))
        if not name.startswith("_")
        and not name.lower().startswith("readme")
        and name.lower().endswith(_SUPPORTED_EXTENSIONS)
    ]


def load_entries(directory, log=lambda message: None):
    """Reads every usable file in `directory`; files that can't be read are
    reported through `log` and skipped, never fatal."""
    entries = []
    for name in _usable_names(directory):
        try:
            entries.extend(parse_file(os.path.join(directory, name)))
        except KnowledgeFileError as e:
            log(f"Knowledge base: skipped {name}: {e}")
    return entries


def _file_key(path):
    """A fingerprint of a file's contents plus the settings that affect how
    it is read. If it hasn't changed since last time, it isn't read again --
    reading a long PDF on every start-up would be slow."""
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            digest.update(block)
    return f"{digest.hexdigest()}:{config.KNOWLEDGE_CHUNK_CHARS}:{PARSER_VERSION}"


def sync(log=print):
    """Brings the database in line with the knowledge_base/ folder: reads
    files that are new or changed, drops the entries of files that changed
    or were deleted, and leaves everything else alone. Called every time the
    backend starts. Problems are reported through `log`. Returns the total
    number of entries now loaded.
    """
    directory = config.KNOWLEDGE_DIR
    names = _usable_names(directory)
    if os.path.isdir(directory):
        for other in sorted(os.listdir(directory)):
            if other.lower().endswith(_UNSUPPORTED_HINTS) and not other.startswith("_"):
                log(f"Knowledge base: skipped {other}: this file type can't be read. "
                    "Save it as .docx, .pdf, .md or .txt")
    current = {name: _file_key(os.path.join(directory, name)) for name in names}
    loaded = database.knowledge_file_keys()

    remove = [name for name, key in loaded.items() if current.get(name) != key]
    to_read = [name for name in names if loaded.get(name) != current[name]]

    new_entries = []
    for name in to_read:
        try:
            entries = parse_file(os.path.join(directory, name))
        except KnowledgeFileError as e:
            log(f"Knowledge base: skipped {name}: {e}")
            continue
        if not entries:
            log(f"Knowledge base: {name} has no text to load.")
            continue
        for entry in entries:
            entry["file_key"] = current[name]
        new_entries.extend(entries)
        log(f"Knowledge base: read {name} ({len(entries)} entries)")

    database.update_knowledge(remove, new_entries)
    return database.count_knowledge()


if __name__ == "__main__":
    # A quick way to see what the chatbot would learn from a file:
    #     python -m app.knowledge knowledge_base/SomeAct.pdf
    if len(sys.argv) < 2:
        print("Usage: python -m app.knowledge <file> [more files...]")
        sys.exit(1)
    for file_path in sys.argv[1:]:
        try:
            found = parse_file(file_path)
        except KnowledgeFileError as err:
            print(f"{file_path}: {err}")
            continue
        print(f"=== {file_path}: {len(found)} entries ===")
        for number, entry in enumerate(found[:15], start=1):
            print(f"\n[{number}] {entry['title']}   (source: {entry['source']})")
            print(entry["content"])
        if len(found) > 15:
            print(f"\n... and {len(found) - 15} more entries")
