"""
doc_export.py
-------------
Turns a court document built by app/documents.py into a file people can
download: a Word document (.docx) or a PDF. Both are written with Python's
standard library only (zipfile + plain text formats), so -- like the rest of
the backend -- nothing has to be installed.

A document arrives as a list of "blocks", each a dict with a "style" and its
text. The styles are:

    courtcase  bold; the court on the left, "right" on the right of the same line
                                  IN THE HIGH COURT OF ZIMBABWE ...... CASE NO. HC ..../26
    courtleft  bold, left         HELD AT HARARE
    formref    bold, left, small  Form No 29 / Summons in matrimonial action / Rule 68 (1)
    small      plain, no space after   In the matter between:
    partysep   bold               And
    court    centred, bold        (Domestic Violence Act [Chapter 5:16])
    title    centred, bold, between two full-width lines   SUMMONS
    right    right-aligned
    dated    bold                 DATED AT HARARE THIS 21st DAY OF OCTOBER 2026
    sigblock dotted signature line on the right half, then the lines under it
    to       "TO:" (or "AND TO:" in "label") then the lines, the first in bold
    pagebreak  starts a new page
    party    name on the left, role on the right   JANE DOE ...... Applicant
    heading  bold                 THE FACTS
    para     an ordinary paragraph (wraps)
    num      a numbered paragraph with a hanging indent ("num" holds "3.")
    sig      a signature line, then the text under it
    blank    an empty line

The PDF uses the Times fonts every PDF reader has built in, so nothing is
embedded and the files stay small (a few KB).
"""

import io
import re
import zipfile
from xml.sax.saxutils import escape as _xml_escape

# ---------------------------------------------------------------------------
# Word (.docx)
# ---------------------------------------------------------------------------
# A .docx file is a zip of XML files. Only four are required for Word,
# LibreOffice and Google Docs to open it; a fifth (styles.xml) sets the font.

_A4_TEXT_WIDTH_TWIPS = 11906 - 2 * 1440  # page width minus 1-inch margins

_CONTENT_TYPES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>
<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>
</Types>"""

_ROOT_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>
</Relationships>"""

_DOC_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
</Relationships>"""

# Times New Roman 12 pt, 1.5 line spacing, 6 pt after each paragraph -- the
# usual look of a document filed at a Zimbabwean court.
_STYLES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
<w:docDefaults>
<w:rPrDefault><w:rPr><w:rFonts w:ascii="Times New Roman" w:hAnsi="Times New Roman" w:cs="Times New Roman" w:eastAsia="Times New Roman"/><w:sz w:val="24"/><w:szCs w:val="24"/><w:lang w:val="en-ZW"/></w:rPr></w:rPrDefault>
<w:pPrDefault><w:pPr><w:spacing w:after="120" w:line="360" w:lineRule="auto"/></w:pPr></w:pPrDefault>
</w:docDefaults>
<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/><w:qFormat/></w:style>
</w:styles>"""


def _core_xml(title):
    return ("""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" xmlns:dc="http://purl.org/dc/elements/1.1/">
<dc:title>""" + _xml_escape(title) + """</dc:title><dc:creator>Self Actors Hub ZW</dc:creator>
</cp:coreProperties>""")


def _runs(text, bold=False, underline=False):
    """A run of text; line breaks inside the text become Word line breaks
    and tabs become Word tabs."""
    props = ""
    if bold or underline:
        props = "<w:rPr>" + ("<w:b/>" if bold else "") + ('<w:u w:val="single"/>' if underline else "") + "</w:rPr>"
    parts = []
    for i, line in enumerate(str(text).split("\n")):
        if i:
            parts.append("<w:br/>")
        for j, piece in enumerate(line.split("\t")):
            if j:
                parts.append("<w:tab/>")
            if piece:
                parts.append(f'<w:t xml:space="preserve">{_xml_escape(piece)}</w:t>')
    return f"<w:r>{props}{''.join(parts)}</w:r>"


def _paragraph(block):
    style = block.get("style", "para")
    text = block.get("text", "")
    ppr, body = "", ""
    line = '<w:bottom w:val="single" w:sz="8" w:space="4" w:color="000000"/>'
    if style == "court":
        ppr, body = '<w:jc w:val="center"/><w:spacing w:after="0"/>', _runs(text, bold=True)
    elif style == "courtcase":
        ppr = (f'<w:tabs><w:tab w:val="right" w:pos="{_A4_TEXT_WIDTH_TWIPS}"/></w:tabs>'
               '<w:spacing w:after="0" w:line="240" w:lineRule="auto"/>')
        body = _runs(text, bold=True) + _runs("\t" + block.get("right", ""), bold=True)
    elif style in ("courtleft", "formref"):
        ppr = '<w:spacing w:after="0" w:line="240" w:lineRule="auto"/>'
        body = _runs(text, bold=True)
        if style == "formref":
            ppr = '<w:spacing w:after="240" w:line="240" w:lineRule="auto"/>'
    elif style == "small":
        ppr, body = '<w:spacing w:after="0" w:line="240" w:lineRule="auto"/>', _runs(text)
    elif style == "partysep":
        ppr, body = '<w:spacing w:before="120" w:after="120"/>', _runs(text, bold=True)
    elif style == "title":
        # The document's name between two lines, as on filed court papers.
        ppr = (f'<w:keepNext/><w:pBdr><w:top w:val="single" w:sz="8" w:space="12" w:color="000000"/>{line}</w:pBdr>'
               '<w:jc w:val="center"/><w:spacing w:before="360" w:after="240"/>')
        body = _runs(text, bold=True)
    elif style == "dated":
        ppr, body = '<w:keepNext/><w:spacing w:before="240" w:after="120"/>', _runs(text, bold=True)
    elif style == "sigblock":
        lines = str(text).split("\n")
        ppr = '<w:keepLines/><w:keepNext/><w:ind w:left="4680"/><w:spacing w:before="480" w:line="276" w:lineRule="auto"/>'
        body = _runs("\u2026" * 17 + "\n") + _runs(lines[0], bold=True) + (_runs("\n" + "\n".join(lines[1:])) if len(lines) > 1 else "")
    elif style == "to":
        lines = str(text).split("\n")
        ppr = '<w:keepLines/><w:ind w:left="1080" w:hanging="1080"/><w:spacing w:before="240" w:line="276" w:lineRule="auto"/>'
        body = (_runs(block.get("label", "TO:") + "\t", bold=True) + _runs(lines[0], bold=True)
                + "".join(_runs("\n") + _runs(l) for l in lines[1:]))
        ppr = '<w:tabs><w:tab w:val="left" w:pos="1080"/></w:tabs>' + ppr
    elif style == "pagebreak":
        return '<w:p><w:r><w:br w:type="page"/></w:r></w:p>'
    elif style == "right":
        ppr, body = '<w:jc w:val="right"/>', _runs(text)
    elif style == "heading":
        ppr, body = '<w:keepNext/><w:spacing w:before="240"/>', _runs(text, bold=True)
    elif style == "party":
        ppr = f'<w:tabs><w:tab w:val="right" w:pos="{_A4_TEXT_WIDTH_TWIPS}"/></w:tabs><w:spacing w:after="0"/>'
        body = _runs(text, bold=True) + _runs("\t" + block.get("role", ""), bold=True)
    elif style == "num":
        left = 720 * (2 if block.get("indent") else 1)  # "indent": a sub-item such as (a), (b)
        ppr = f'<w:ind w:left="{left}" w:hanging="720"/><w:jc w:val="both"/>'
        body = _runs(f"{block.get('num', '')}\t{text}")
    elif style == "sig":
        ppr, body = '<w:keepLines/><w:spacing w:before="480"/>', _runs("______________________________\n" + text)
    elif style == "blank":
        body = ""
    else:  # para
        ppr, body = '<w:jc w:val="both"/>', _runs(text)
    if block.get("keep_with_next"):
        ppr = "<w:keepNext/>" + ppr
    return f"<w:p><w:pPr>{ppr}</w:pPr>{body}</w:p>"


def render_docx(blocks, title="Court document"):
    body = "".join(_paragraph(b) for b in blocks)
    document = ("""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>"""
                + body +
                """<w:sectPr><w:pgSz w:w="11906" w:h="16838"/><w:pgMar w:top="1440" w:right="1440" w:bottom="1440" w:left="1440" w:header="708" w:footer="708" w:gutter="0"/></w:sectPr></w:body></w:document>""")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", _CONTENT_TYPES)
        z.writestr("_rels/.rels", _ROOT_RELS)
        z.writestr("word/_rels/document.xml.rels", _DOC_RELS)
        z.writestr("word/document.xml", document)
        z.writestr("word/styles.xml", _STYLES)
        z.writestr("docProps/core.xml", _core_xml(title))
    return buf.getvalue()


# ---------------------------------------------------------------------------
# PDF
# ---------------------------------------------------------------------------
# Character widths (in 1/1000 of the font size) of the standard Times fonts in
# WinAnsi encoding, from the Adobe font metrics. They are what lets the PDF
# wrap lines and centre headings exactly as a PDF reader will draw them.

_TIMES_ROMAN = (
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    250, 333, 408, 500, 500, 833, 778, 180, 333, 333, 500, 564, 250, 333, 250, 278,
    500, 500, 500, 500, 500, 500, 500, 500, 500, 500, 278, 278, 564, 564, 564, 444,
    921, 722, 667, 667, 722, 611, 556, 722, 722, 333, 389, 722, 611, 889, 722, 722,
    556, 722, 667, 556, 611, 722, 722, 944, 722, 722, 611, 333, 278, 333, 469, 500,
    333, 444, 500, 444, 500, 444, 333, 500, 500, 278, 278, 500, 278, 778, 500, 500,
    500, 500, 333, 389, 278, 500, 500, 722, 500, 500, 444, 480, 200, 480, 541, 350,
    500, 350, 333, 500, 444, 1000, 500, 500, 333, 1000, 556, 333, 889, 350, 611, 350,
    350, 333, 333, 444, 444, 350, 500, 1000, 333, 980, 389, 333, 722, 350, 444, 722,
    250, 333, 500, 500, 500, 500, 200, 500, 333, 760, 276, 500, 564, 333, 760, 333,
    400, 564, 300, 300, 333, 500, 453, 250, 333, 300, 310, 500, 750, 750, 750, 444,
    722, 722, 722, 722, 722, 722, 889, 667, 611, 611, 611, 611, 333, 333, 333, 333,
    722, 722, 722, 722, 722, 722, 722, 564, 722, 722, 722, 722, 722, 722, 556, 500,
    444, 444, 444, 444, 444, 444, 667, 444, 444, 444, 444, 444, 278, 278, 278, 278,
    500, 500, 500, 500, 500, 500, 500, 564, 500, 500, 500, 500, 500, 500, 500, 500,
)

_TIMES_BOLD = (
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    250, 333, 555, 500, 500, 1000, 833, 278, 333, 333, 500, 570, 250, 333, 250, 278,
    500, 500, 500, 500, 500, 500, 500, 500, 500, 500, 333, 333, 570, 570, 570, 500,
    930, 722, 667, 722, 722, 667, 611, 778, 778, 389, 500, 778, 667, 944, 722, 778,
    611, 778, 722, 556, 667, 722, 722, 1000, 722, 722, 667, 333, 278, 333, 581, 500,
    333, 500, 556, 444, 556, 444, 333, 500, 556, 278, 333, 556, 278, 833, 556, 500,
    556, 556, 444, 389, 333, 556, 500, 722, 500, 500, 444, 394, 220, 394, 520, 350,
    500, 350, 333, 500, 500, 1000, 500, 500, 333, 1000, 556, 333, 1000, 350, 667, 350,
    350, 333, 333, 500, 500, 350, 500, 1000, 333, 1000, 389, 333, 722, 350, 444, 722,
    250, 333, 500, 500, 500, 500, 220, 500, 333, 747, 300, 500, 570, 333, 747, 333,
    400, 570, 300, 300, 333, 556, 540, 250, 333, 300, 330, 500, 750, 750, 750, 500,
    722, 722, 722, 722, 722, 722, 1000, 722, 667, 667, 667, 667, 389, 389, 389, 389,
    722, 722, 778, 778, 778, 778, 778, 570, 778, 722, 722, 722, 722, 722, 611, 556,
    500, 500, 500, 500, 500, 500, 722, 444, 444, 444, 444, 444, 278, 278, 278, 278,
    500, 556, 500, 500, 500, 500, 500, 570, 500, 556, 556, 556, 556, 500, 556, 500,
)

_PAGE_W, _PAGE_H = 595.28, 841.89          # A4 in points
_MARGIN = 72                                # 1 inch
_TEXT_W = _PAGE_W - 2 * _MARGIN
_SIZE, _LEADING = 12, 18                    # 12 pt type, 1.5 line spacing
_HANG = 36                                  # hanging indent for numbered paragraphs


def _encode(text):
    """PDF's built-in fonts use the Windows-1252 character set; anything
    outside it (rare in court documents) becomes '?'."""
    text = str(text).replace("‑", "-").replace(" ", " ")
    return text.encode("cp1252", errors="replace")


def _width(raw, bold=False):
    table = _TIMES_BOLD if bold else _TIMES_ROMAN
    return sum(table[c] for c in raw) * _SIZE / 1000.0


def _wrap(text, max_w, bold=False):
    """Splits text into lines no wider than max_w points, breaking at spaces
    (a single word longer than the line is broken where it must be)."""
    lines = []
    for paragraph in str(text).split("\n"):
        line = b""
        for word in _encode(paragraph).split(b" "):
            trial = (line + b" " + word) if line else word
            if _width(trial, bold) <= max_w:
                line = trial
                continue
            if line:
                lines.append(line)
            while _width(word, bold) > max_w:  # an unbreakable run of characters
                cut = len(word)
                while cut > 1 and _width(word[:cut], bold) > max_w:
                    cut -= 1
                lines.append(word[:cut])
                word = word[cut:]
            line = word
        lines.append(line)
    return lines


def _pdf_str(raw):
    return b"(" + raw.replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)") + b")"


class _Pages:
    """Collects drawing commands page by page, starting a new page whenever
    the next line would run into the bottom margin."""

    def __init__(self):
        self.pages = []
        self.new_page()

    def new_page(self):
        self.ops = []
        self.pages.append(self.ops)
        self.y = _PAGE_H - _MARGIN

    def need(self, height):
        if self.y - height < _MARGIN:
            self.new_page()

    def text(self, x, raw, bold=False):
        font = b"/F2" if bold else b"/F1"
        self.ops.append(b"BT " + font + b" %d Tf %.2f %.2f Td " % (_SIZE, x, self.y - _SIZE) + _pdf_str(raw) + b" Tj ET")

    def line(self, x1, x2, y):
        self.ops.append(b"%.2f %.2f m %.2f %.2f l 0.6 w S" % (x1, y, x2, y))

    def down(self, amount):
        self.y -= amount


def _draw(p, block):
    """Draws one block onto the pages, starting a new page when needed."""
    style = block.get("style", "para")
    text = block.get("text", "")
    if style == "blank":
        p.down(_LEADING)
        return
    if style == "pagebreak":
        p.new_page()
        return
    if style == "courtcase":
        right = _encode(block.get("right", ""))
        lines = _wrap(text, _TEXT_W - _width(right, bold=True) - 24, bold=True)
        p.need(_LEADING * len(lines))
        p.text(_MARGIN + _TEXT_W - _width(right, bold=True), right, bold=True)
        for raw in lines:
            p.text(_MARGIN, raw, bold=True)
            p.down(_SIZE + 2)
        return
    if style in ("courtleft", "formref", "small"):
        bold = style != "small"
        for raw in _wrap(text, _TEXT_W, bold=bold):
            p.need(_SIZE + 2)
            p.text(_MARGIN, raw, bold=bold)
            p.down(_SIZE + 2)
        p.down(_LEADING if style == "formref" else 2)
        return
    if style == "partysep":
        p.down(_LEADING / 3)
        p.need(_LEADING)
        p.text(_MARGIN, _encode(text), bold=True)
        p.down(_LEADING * 4 / 3)
        return
    if style == "title":
        # The document's name between two full-width lines.
        lines = _wrap(text, _TEXT_W, bold=True)
        p.need(_LEADING * (len(lines) + 4))
        p.down(_LEADING / 2)
        p.line(_MARGIN, _MARGIN + _TEXT_W, p.y)
        p.down(_LEADING * 0.9)
        for raw in lines:
            w = _width(raw, bold=True)
            p.text(_MARGIN + (_TEXT_W - w) / 2, raw, bold=True)
            p.down(_LEADING)
        p.line(_MARGIN, _MARGIN + _TEXT_W, p.y + 2)
        p.down(_LEADING * 0.9)
        return
    if style == "dated":
        lines = _wrap(text, _TEXT_W, bold=True)
        p.down(_LEADING / 2)
        p.need(_LEADING * (len(lines) + 5))
        for raw in lines:
            p.text(_MARGIN, raw, bold=True)
            p.down(_LEADING)
        return
    if style == "sigblock":
        x = _MARGIN + _TEXT_W * 0.55
        rows = str(text).split("\n")
        wrapped = [(_wrap(r, _TEXT_W * 0.45, bold=(i == 0)), i == 0) for i, r in enumerate(rows)]
        p.need(_LEADING * (sum(len(w) for w, _ in wrapped) + 3))
        p.down(_LEADING * 1.5)
        p.text(x, _encode("." * 45))
        p.down(_LEADING)
        for lines, bold in wrapped:
            for raw in lines:
                p.text(x, raw, bold=bold)
                p.down(_SIZE + 3)
        p.down(_LEADING / 2)
        return
    if style == "to":
        label = _encode(block.get("label", "TO:"))
        rows = str(text).split("\n")
        p.down(_LEADING / 2)
        p.need(_LEADING * (len(rows) + 1))
        p.text(_MARGIN, label, bold=True)
        for i, row in enumerate(rows):
            for raw in _wrap(row, _TEXT_W - 72, bold=(i == 0)):
                p.text(_MARGIN + 72, raw, bold=(i == 0))
                p.down(_SIZE + 3)
        return
    if style == "court":
        for raw in _wrap(text, _TEXT_W, bold=True):
            p.need(_LEADING)
            w = _width(raw, bold=True)
            p.text(_MARGIN + (_TEXT_W - w) / 2, raw, bold=True)
            p.down(_LEADING)
    elif style == "right":
        for raw in _wrap(text, _TEXT_W):
            p.need(_LEADING)
            p.text(_MARGIN + _TEXT_W - _width(raw), raw)
            p.down(_LEADING)
        p.down(_LEADING / 3)
    elif style == "party":
        role = _encode(block.get("role", ""))
        room = _TEXT_W - _width(role) - 24
        lines = _wrap(text, room, bold=True)
        p.need(_LEADING * len(lines))
        p.text(_MARGIN + _TEXT_W - _width(role, bold=True), role, bold=True)
        for raw in lines:
            p.text(_MARGIN, raw, bold=True)
            p.down(_LEADING)
    elif style == "heading":
        lines = _wrap(text, _TEXT_W, bold=True)
        p.down(_LEADING / 2)
        p.need(_LEADING * (len(lines) + 2))  # keep a heading with what follows
        for raw in lines:
            p.text(_MARGIN, raw, bold=True)
            p.down(_LEADING)
    elif style == "num":
        shift = _HANG if block.get("indent") else 0
        lines = _wrap(text, _TEXT_W - _HANG - shift)
        p.need(_LEADING * min(len(lines), 2))
        p.text(_MARGIN + shift, _encode(block.get("num", "")))
        for raw in lines:
            p.need(_LEADING)
            p.text(_MARGIN + _HANG + shift, raw)
            p.down(_LEADING)
        p.down(_LEADING / 3)
    elif style == "sig":
        lines = _wrap(text, _TEXT_W)
        p.need(_LEADING * (len(lines) + 3))
        p.down(_LEADING * 2)
        p.line(_MARGIN, _MARGIN + 200, p.y)
        p.down(4)
        for raw in lines:
            p.text(_MARGIN, raw)
            p.down(_LEADING)
    else:  # para
        for raw in _wrap(text, _TEXT_W):
            p.need(_LEADING)
            p.text(_MARGIN, raw)
            p.down(_LEADING)
        p.down(_LEADING / 3)




def _height(block):
    """How much page height a block takes, found by drawing it on a scratch page."""
    scratch = _Pages()
    scratch.y = 1e6
    _draw(scratch, block)
    return 1e6 - scratch.y


def _layout(blocks):
    """Draws the blocks page by page. A block marked keep_with_next (the
    signature and commissioner's section of an affidavit) is kept on the same
    page as the blocks that follow it."""
    p = _Pages()
    for i, block in enumerate(blocks):
        if block.get("keep_with_next") and not (i and blocks[i - 1].get("keep_with_next")):
            chain = [block]
            for nxt in blocks[i + 1:]:
                chain.append(nxt)
                if not nxt.get("keep_with_next"):
                    break
            total = sum(_height(b) for b in chain)
            if total < _PAGE_H - 2 * _MARGIN:
                p.need(total)
        _draw(p, block)
    return p.pages


def render_pdf(blocks, title="Court document"):
    pages = _layout(blocks)
    total = len(pages)
    objects = []  # objects[i] is PDF object number i + 1

    def add(body):
        objects.append(body)
        return len(objects)

    font_regular = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Times-Roman /Encoding /WinAnsiEncoding >>")
    font_bold = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Times-Bold /Encoding /WinAnsiEncoding >>")
    pages_obj = add(b"")  # filled in once the page objects exist
    page_ids = []
    for number, ops in enumerate(pages, start=1):
        footer = _encode(f"Page {number} of {total}")
        ops = ops + [b"BT /F1 9 Tf %.2f 40 Td " % ((_PAGE_W - _width(footer) * 9 / _SIZE) / 2) + _pdf_str(footer) + b" Tj ET"]
        stream = b"\n".join(ops)
        content = add(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream")
        page_ids.append(add(
            b"<< /Type /Page /Parent %d 0 R /MediaBox [0 0 %.2f %.2f] /Contents %d 0 R "
            b"/Resources << /Font << /F1 %d 0 R /F2 %d 0 R >> >> >>"
            % (pages_obj, _PAGE_W, _PAGE_H, content, font_regular, font_bold)))
    objects[pages_obj - 1] = (b"<< /Type /Pages /Kids [" + b" ".join(b"%d 0 R" % i for i in page_ids)
                              + b"] /Count %d >>" % total)
    catalog = add(b"<< /Type /Catalog /Pages %d 0 R >>" % pages_obj)
    info = add(b"<< /Title " + _pdf_str(_encode(title)) + b" /Producer (Self Actors Hub ZW) >>")

    out = io.BytesIO()
    out.write(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(out.tell())
        out.write(b"%d 0 obj\n" % number + body + b"\nendobj\n")
    xref = out.tell()
    out.write(b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1))
    for offset in offsets:
        out.write(b"%010d 00000 n \n" % offset)
    out.write(b"trailer\n<< /Size %d /Root %d 0 R /Info %d 0 R >>\nstartxref\n%d\n%%%%EOF\n"
              % (len(objects) + 1, catalog, info, xref))
    return out.getvalue()


def safe_filename(title, extension):
    """'Founding Affidavit - Jane Doe' -> 'Founding_Affidavit_-_Jane_Doe.docx'"""
    name = re.sub(r"[^A-Za-z0-9 _\-]", "", title).strip() or "court_document"
    return re.sub(r"\s+", "_", name)[:80] + "." + extension
