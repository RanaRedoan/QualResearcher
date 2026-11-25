"""Word and plain-text exports of a finished job.

The Word file is deliberately plain: black text, one Bangla font (Hind Siliguri),
a red "check this manually" box at the very top, a small interview-details table,
then the Bangla transcript, then (if made) the English translation on a new page.
"""
import io
import re

from django.conf import settings
from docx import Document
from docx.enum.section import WD_ORIENT
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_BREAK
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

from .pipeline import format_timestamp

LATIN_FONT = "Calibri"
BANGLA_FONT = "Hind Siliguri"  # installed by start.bat; Word substitutes a Bangla font if missing
BLACK = RGBColor(0, 0, 0)
GREY = RGBColor(0x55, 0x55, 0x55)
BN_DIGITS = str.maketrans("0123456789", "০১২৩৪৫৬৭৮৯")

DEMOGRAPHIC_FIELDS = [
    ("respondent_id", "Respondent ID"),
    ("age", "Age"),
    ("gender", "Gender"),
    ("location", "Location"),
    ("occupation", "Occupation"),
    ("education", "Education"),
    ("interview_date", "Interview date"),
    ("interviewer", "Interviewer"),
    ("other", "Notes"),
]

_BN_ROLES = {"Interviewer": "প্রশ্নকারী", "Respondent": "উত্তরদাতা", "Moderator": "সঞ্চালক",
             "Note-taker": "নোট গ্রহণকারী", "Participant": "অংশগ্রহণকারী", "Other": "অন্যান্য",
             "Speaker": "বক্তা"}


def bangla_label(label):
    """'Participant 3' → 'অংশগ্রহণকারী ৩'; unknown labels are kept as they are."""
    m = re.match(r"^(Interviewer|Respondent|Moderator|Note-taker|Participant|Other|Speaker)\s*(\d*)$", label.strip())
    if not m:
        return label
    return (_BN_ROLES[m.group(1)] + (" " + m.group(2).translate(BN_DIGITS) if m.group(2) else "")).strip()


def merged_turns(segments, key):
    """Join back-to-back segments of the same speaker into one paragraph."""
    turns = []
    for s in segments:
        text = (s.get(key) or "").strip()
        if not text:
            continue
        if turns and turns[-1]["speaker"] == s["speaker"]:
            turns[-1]["text"] += " " + text
        else:
            turns.append({"speaker": s["speaker"], "text": text, "start": s["start"]})
    return turns


# ---------------------------------------------------------------- low-level helpers
def _fonts(run, size=11, bold=False, color=BLACK):
    run.font.name = LATIN_FONT
    run.font.size = Pt(size)
    run.font.bold = bold
    run.font.color.rgb = color
    rpr = run._element.get_or_add_rPr()
    fonts = rpr.find(qn("w:rFonts"))
    if fonts is None:
        fonts = OxmlElement("w:rFonts")
        rpr.insert(0, fonts)
    fonts.set(qn("w:ascii"), LATIN_FONT)
    fonts.set(qn("w:hAnsi"), LATIN_FONT)
    fonts.set(qn("w:cs"), BANGLA_FONT)
    fonts.set(qn("w:eastAsia"), BANGLA_FONT)
    for tag, val in (("w:szCs", str(int(size * 2))), ):
        el = OxmlElement(tag)
        el.set(qn("w:val"), val)
        rpr.append(el)
    if bold:
        rpr.append(OxmlElement("w:bCs"))
    lang = OxmlElement("w:lang")
    lang.set(qn("w:val"), "en-US")
    lang.set(qn("w:bidi"), "bn-BD")
    rpr.append(lang)
    return run


def _para(doc, text="", size=11, bold=False, color=BLACK, align=None, after=6, line=1.15, keep_next=False):
    p = doc.add_paragraph()
    pf = p.paragraph_format
    pf.space_before, pf.space_after, pf.line_spacing = Pt(0), Pt(after), line
    pf.keep_with_next = keep_next
    if align is not None:
        p.alignment = align
    if text:
        _fonts(p.add_run(text), size, bold, color)
    return p


def _heading(doc, text):
    p = _para(doc, text, size=13, bold=True, after=10, keep_next=True)
    p.paragraph_format.space_before = Pt(18)
    return p


def _cell(cell, text, bold=False, size=10):
    cell.text = ""
    p = cell.paragraphs[0]
    p.paragraph_format.space_after = Pt(0)
    _fonts(p.add_run(text), size, bold)


def _table_borders(table, color="808080", size="4"):
    tbl_pr = table._tbl.tblPr
    borders = OxmlElement("w:tblBorders")
    for edge in ("top", "left", "bottom", "right", "insideH", "insideV"):
        el = OxmlElement(f"w:{edge}")
        el.set(qn("w:val"), "single")
        el.set(qn("w:sz"), size)
        el.set(qn("w:color"), color)
        borders.append(el)
    tbl_pr.append(borders)
    layout = OxmlElement("w:tblLayout")
    layout.set(qn("w:type"), "fixed")
    tbl_pr.append(layout)


def _widths(table, widths):
    table.autofit = False
    for i, w in enumerate(widths):
        table.columns[i].width = w
        for c in table.columns[i].cells:
            c.width = w


def _repeat_header(row):
    tr_pr = row._tr.get_or_add_trPr()
    el = OxmlElement("w:tblHeader")
    el.set(qn("w:val"), "true")
    tr_pr.append(el)


def _page_number_footer(section):
    p = section.footer.paragraphs[0]
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = p.add_run()
    for kind, text in (("begin", None), (None, "PAGE"), ("end", None)):
        if kind:
            el = OxmlElement("w:fldChar")
            el.set(qn("w:fldCharType"), kind)
        else:
            el = OxmlElement("w:instrText")
            el.set(qn("xml:space"), "preserve")
            el.text = text
        run._r.append(el)
    _fonts(run, 9, color=GREY)


def _new_document(landscape=False):
    doc = Document()
    sec = doc.sections[0]
    sec.page_width, sec.page_height = Cm(21), Cm(29.7)  # A4
    if landscape:
        sec.orientation = WD_ORIENT.LANDSCAPE
        sec.page_width, sec.page_height = sec.page_height, sec.page_width
    sec.left_margin = sec.right_margin = sec.top_margin = sec.bottom_margin = Cm(2.54)
    normal = doc.styles["Normal"]
    normal.font.name, normal.font.size = LATIN_FONT, Pt(11)
    normal.element.rPr.rFonts.set(qn("w:cs"), BANGLA_FONT)
    normal.element.rPr.rFonts.set(qn("w:eastAsia"), BANGLA_FONT)
    _page_number_footer(sec)
    return doc


AI_NOTICE_LEAD = "This transcription is prepared with the help of AI."
AI_NOTICE_BODY = " AI can make mistakes, so don't forget to check it manually."
RED = RGBColor(0xA6, 0x1B, 0x1B)


def _ai_notice(doc, width_cm):
    """A red box at the very top of every Word file: the text is AI-assisted, check it by hand."""
    table = doc.add_table(rows=1, cols=1)
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    cell = table.rows[0].cells[0]
    cell.text = ""
    p = cell.paragraphs[0]
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.paragraph_format.space_before = Pt(5)
    p.paragraph_format.space_after = Pt(5)
    _fonts(p.add_run(AI_NOTICE_LEAD), 10, bold=True, color=RED)
    _fonts(p.add_run(AI_NOTICE_BODY), 10, color=RED)
    tc_pr = cell._tc.get_or_add_tcPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), "FDECEC")
    tc_pr.append(shd)
    margins = OxmlElement("w:tcMar")
    for edge, w in (("top", 60), ("bottom", 60), ("left", 160), ("right", 160)):
        el = OxmlElement(f"w:{edge}")
        el.set(qn("w:w"), str(w))
        el.set(qn("w:type"), "dxa")
        margins.append(el)
    tc_pr.append(margins)
    _table_borders(table, color="C62828", size="12")
    _widths(table, (Cm(width_cm),))
    spacer = _para(doc, after=10)
    spacer.paragraph_format.line_spacing = 0.6


def _front_matter(doc, job, width_cm, partial_note=None):
    _ai_notice(doc, width_cm)
    _para(doc, job.title, size=14, bold=True, align=WD_ALIGN_PARAGRAPH.CENTER, after=4)
    subtitle = ("Bangla Transcript and English Translation" if job.has_english else "Bangla Transcript")
    if partial_note:
        subtitle = "Partial Bangla Transcript"
    _para(doc, f"{job.get_interview_type_display()}: {subtitle}", size=11, color=GREY,
          align=WD_ALIGN_PARAGRAPH.CENTER, after=10 if partial_note else 16)
    if partial_note:
        _para(doc, partial_note, size=10, bold=True, align=WD_ALIGN_PARAGRAPH.CENTER, after=16)

    speakers = list(dict.fromkeys(s["speaker"] for s in job.segments))
    rows = [("Interview type", job.get_interview_type_display()),
            ("Duration", job.duration_display),
            ("Speakers", "; ".join(speakers) or "-"),
            ("Audio file", job.original_filename)]
    demo = job.demographics or {}
    rows[1:1] = [(label, str(demo[key]).strip()) for key, label in DEMOGRAPHIC_FIELDS if str(demo.get(key, "")).strip()]

    table = doc.add_table(rows=0, cols=2)
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    for label, value in rows:
        cells = table.add_row().cells
        _cell(cells[0], label, bold=True)
        _cell(cells[1], value)
    _table_borders(table)
    _widths(table, (Cm(4.2), Cm(width_cm - 4.2)))
    if job.uncertain_count:
        _para(doc, f"Note: {job.uncertain_count} passage(s) were hard to hear and are marked [অস্পষ্ট].",
              size=9.5, color=GREY, after=0).paragraph_format.space_before = Pt(8)


def _turns(doc, job, key, timestamps):
    bn = key == "bn"
    if timestamps:
        items = [{"speaker": s["speaker"], "text": s[key], "start": s["start"]} for s in job.segments if s.get(key)]
    else:
        items = merged_turns(job.segments, key)
    for t in items:
        p = _para(doc, after=6, line=1.3 if bn else 1.15)
        if timestamps:
            stamp = format_timestamp(t["start"])
            _fonts(p.add_run(f"[{stamp.translate(BN_DIGITS) if bn else stamp}]  "), 9.5, color=GREY)
        label = bangla_label(t["speaker"]) if bn else t["speaker"]
        _fonts(p.add_run(f"{label}: "), 11, bold=True)
        _fonts(p.add_run(t["text"]), 11)


def _page_break(doc):
    doc.add_paragraph().add_run().add_break(WD_BREAK.PAGE)


# ---------------------------------------------------------------- public API
def build_docx(job, layout="clean", partial_note=None) -> io.BytesIO:
    """layout: "clean" (no timestamps, speaker turns joined), "timestamps", or
    "table" (landscape, Bangla and English side by side; needs a translation).
    partial_note: set for an unfinished job (job.segments then holds what is done so far)."""
    table_layout = layout == "table" and job.has_english
    doc = _new_document(landscape=table_layout)
    width = 29.7 - 5.08 if table_layout else 21 - 5.08
    _front_matter(doc, job, width, partial_note)

    if table_layout:
        _heading(doc, "Transcript")
        table = doc.add_table(rows=1, cols=3)
        widths = (Cm(2.8), Cm((width - 2.8) / 2), Cm((width - 2.8) / 2))
        for c, text in zip(table.rows[0].cells, ("Speaker", "বাংলা", "English")):
            _cell(c, text, bold=True)
        _repeat_header(table.rows[0])
        for s in job.segments:
            cells = table.add_row().cells
            _cell(cells[0], f"{s['speaker']}\n{format_timestamp(s['start'])}", size=9)
            _cell(cells[1], s["bn"], size=10.5)
            _cell(cells[2], s.get("en", ""), size=10)
        _table_borders(table, "A0A0A0")
        _widths(table, widths)
    else:
        _heading(doc, "বাংলা ট্রান্সক্রিপ্ট")
        _turns(doc, job, "bn", timestamps=layout == "timestamps")
        if job.has_english:
            _page_break(doc)
            _heading(doc, "English Translation")
            _turns(doc, job, "en", timestamps=layout == "timestamps")

    buf = io.BytesIO()
    doc.core_properties.title = job.title
    doc.save(buf)
    buf.seek(0)
    return buf
