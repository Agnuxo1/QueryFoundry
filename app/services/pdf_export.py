import html
from datetime import datetime
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from reportlab.platypus.doctemplate import IndexingFlowable

from config import APP_NAME, CONTROL_SCHEMA, DATA_DICTIONARY_TABLE


def _paragraph_text(value) -> str:
    text = str(value or "").strip()
    if not text:
        text = "-"
    return html.escape(text).replace("\n", "<br/>")


def _build_styles():
    base_styles = getSampleStyleSheet()
    return {
        "cover_kicker": ParagraphStyle(
            "cover_kicker",
            parent=base_styles["Normal"],
            fontName="Helvetica-Bold",
            fontSize=11,
            leading=14,
            alignment=TA_CENTER,
            textColor=colors.HexColor("#0f172a"),
            spaceAfter=6,
        ),
        "cover_title": ParagraphStyle(
            "cover_title",
            parent=base_styles["Title"],
            fontName="Helvetica-Bold",
            fontSize=22,
            leading=28,
            alignment=TA_CENTER,
            textColor=colors.HexColor("#0f172a"),
            spaceAfter=10,
        ),
        "cover_subtitle": ParagraphStyle(
            "cover_subtitle",
            parent=base_styles["Normal"],
            fontName="Helvetica",
            fontSize=11,
            leading=15,
            alignment=TA_CENTER,
            textColor=colors.HexColor("#334155"),
            spaceAfter=8,
        ),
        "cover_org": ParagraphStyle(
            "cover_org",
            parent=base_styles["Normal"],
            fontName="Helvetica-Bold",
            fontSize=11,
            leading=15,
            alignment=TA_CENTER,
            textColor=colors.HexColor("#334155"),
            spaceAfter=6,
        ),
        "section_title": ParagraphStyle(
            "section_title",
            parent=base_styles["Heading2"],
            fontName="Helvetica-Bold",
            fontSize=15,
            leading=20,
            alignment=TA_LEFT,
            textColor=colors.HexColor("#0f172a"),
            spaceAfter=8,
            spaceBefore=8,
        ),
        "entry_title": ParagraphStyle(
            "entry_title",
            parent=base_styles["Heading3"],
            fontName="Helvetica-Bold",
            fontSize=12,
            leading=16,
            alignment=TA_LEFT,
            textColor=colors.HexColor("#0f172a"),
            spaceAfter=6,
            spaceBefore=6,
        ),
        "body": ParagraphStyle(
            "body",
            parent=base_styles["BodyText"],
            fontName="Helvetica",
            fontSize=9,
            leading=13,
            alignment=TA_LEFT,
            textColor=colors.HexColor("#1f2937"),
            spaceAfter=6,
        ),
        "table_header": ParagraphStyle(
            "table_header",
            parent=base_styles["BodyText"],
            fontName="Helvetica-Bold",
            fontSize=8.5,
            leading=11,
            alignment=TA_LEFT,
            textColor=colors.white,
        ),
        "table_cell": ParagraphStyle(
            "table_cell",
            parent=base_styles["BodyText"],
            fontName="Helvetica",
            fontSize=8.5,
            leading=11,
            alignment=TA_LEFT,
            textColor=colors.HexColor("#1f2937"),
        ),
        "label_cell": ParagraphStyle(
            "label_cell",
            parent=base_styles["BodyText"],
            fontName="Helvetica-Bold",
            fontSize=8.5,
            leading=11,
            alignment=TA_LEFT,
            textColor=colors.HexColor("#0f172a"),
        ),
    }


def _build_cover_metadata_table(styles, version_number: str, generated_at: datetime, entry_count: int, source_label: str):
    rows = [
        [
            Paragraph("Document version", styles["label_cell"]),
            Paragraph(_paragraph_text(version_number), styles["table_cell"]),
        ],
        [
            Paragraph("Generated at", styles["label_cell"]),
            Paragraph(_paragraph_text(generated_at.strftime("%d/%m/%Y %H:%M")), styles["table_cell"]),
        ],
        [
            Paragraph("Source table", styles["label_cell"]),
            Paragraph(_paragraph_text(source_label), styles["table_cell"]),
        ],
        [
            Paragraph("Entry count", styles["label_cell"]),
            Paragraph(_paragraph_text(entry_count), styles["table_cell"]),
        ],
    ]
    table = Table(rows, colWidths=[44 * mm, 108 * mm], hAlign="CENTER")
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#e2e8f0")),
                ("BACKGROUND", (1, 0), (1, -1), colors.HexColor("#f8fafc")),
                ("BOX", (0, 0), (-1, -1), 0.6, colors.HexColor("#cbd5e1")),
                ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#cbd5e1")),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ]
        )
    )
    return table


class DictionaryPdfDocTemplate(SimpleDocTemplate):
    def afterFlowable(self, flowable):
        entry_payload = getattr(flowable, "_dictionary_entry_payload", None)
        if not entry_payload:
            return
        self.notify(
            "DictionaryEntry",
            (
                entry_payload["item_label"],
                entry_payload["standard_name"],
                entry_payload["aliases"],
                self.page,
                entry_payload["bookmark_key"],
            ),
        )


class DictionaryCatalogOverview(IndexingFlowable):
    def __init__(self, styles):
        self.styles = styles
        self._table = None
        self._entries = []
        self._last_entries = []

    def beforeBuild(self):
        self._last_entries = self._entries[:]
        self._entries = []

    def isIndexing(self):
        return 1

    def isSatisfied(self):
        return self._entries == self._last_entries

    def notify(self, kind, stuff):
        if kind == "DictionaryEntry":
            self._entries.append(stuff)

    def _build_table(self, avail_width):
        if not self._last_entries:
            rows = [
                [
                    Paragraph("Standard name", self.styles["table_header"]),
                    Paragraph("Aliases", self.styles["table_header"]),
                    Paragraph("Location", self.styles["table_header"]),
                ],
                [
                    Paragraph("Generating index...", self.styles["table_cell"]),
                    Paragraph("-", self.styles["table_cell"]),
                    Paragraph("-", self.styles["table_cell"]),
                ],
            ]
        else:
            rows = [
                [
                    Paragraph("Standard name", self.styles["table_header"]),
                    Paragraph("Aliases", self.styles["table_header"]),
                    Paragraph("Location", self.styles["table_header"]),
                ]
            ]
            for item_label, standard_name, aliases, page_number, bookmark_key in self._last_entries:
                location_text = f'<a href="#{bookmark_key}">{html.escape(item_label)} - page {page_number}</a>'
                rows.append(
                    [
                        Paragraph(_paragraph_text(standard_name), self.styles["table_cell"]),
                        Paragraph(_paragraph_text(aliases), self.styles["table_cell"]),
                        Paragraph(location_text, self.styles["table_cell"]),
                    ]
                )

        table = Table(
            rows,
            colWidths=[62 * mm, 70 * mm, 40 * mm],
            repeatRows=1,
            hAlign="LEFT",
        )
        table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#ea580c")),
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                    ("BACKGROUND", (0, 1), (-1, -1), colors.white),
                    ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f8fafc")]),
                    ("BOX", (0, 0), (-1, -1), 0.6, colors.HexColor("#cbd5e1")),
                    ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#cbd5e1")),
                    ("LEFTPADDING", (0, 0), (-1, -1), 6),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                    ("TOPPADDING", (0, 0), (-1, -1), 6),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ]
            )
        )
        return table

    def wrap(self, availWidth, availHeight):
        self._table = self._build_table(availWidth)
        self.width, self.height = self._table.wrapOn(self.canv, availWidth, availHeight)
        return self.width, self.height

    def split(self, availWidth, availHeight):
        return self._table.splitOn(self.canv, availWidth, availHeight)

    def drawOn(self, canvas, x, y, _sW=0):
        self._table.drawOn(canvas, x, y, _sW)


def _build_entry_table(styles, entry):
    rows = [
        [
            Paragraph("Definition", styles["label_cell"]),
            Paragraph(_paragraph_text(entry.get("definition")), styles["table_cell"]),
        ],
        [
            Paragraph("Units", styles["label_cell"]),
            Paragraph(_paragraph_text(entry.get("units")), styles["table_cell"]),
        ],
        [
            Paragraph("Value domain", styles["label_cell"]),
            Paragraph(_paragraph_text(entry.get("value_domain")), styles["table_cell"]),
        ],
        [
            Paragraph("Aliases", styles["label_cell"]),
            Paragraph(_paragraph_text(entry.get("aliases")), styles["table_cell"]),
        ],
        [
            Paragraph("Usage count", styles["label_cell"]),
            Paragraph(_paragraph_text(entry.get("usage_count")), styles["table_cell"]),
        ],
    ]
    table = Table(rows, colWidths=[34 * mm, 138 * mm], hAlign="LEFT")
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#fff7ed")),
                ("BACKGROUND", (1, 0), (1, -1), colors.white),
                ("BOX", (0, 0), (-1, -1), 0.6, colors.HexColor("#cbd5e1")),
                ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#cbd5e1")),
                ("LEFTPADDING", (0, 0), (-1, -1), 7),
                ("RIGHTPADDING", (0, 0), (-1, -1), 7),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ]
        )
    )
    return table


def _draw_page_chrome(canvas, doc):
    canvas.saveState()
    page_width, page_height = A4
    page_number = canvas.getPageNumber()

    canvas.setStrokeColor(colors.HexColor("#cbd5e1"))
    canvas.setLineWidth(0.6)
    if page_number > 1:
        canvas.line(doc.leftMargin, page_height - 14 * mm, page_width - doc.rightMargin, page_height - 14 * mm)
        canvas.setFont("Helvetica-Bold", 9)
        canvas.setFillColor(colors.HexColor("#0f172a"))
        canvas.drawString(doc.leftMargin, page_height - 11.5 * mm, f"{APP_NAME} | Data Dictionary")

    canvas.line(doc.leftMargin, 12 * mm, page_width - doc.rightMargin, 12 * mm)
    canvas.setFont("Helvetica", 8)
    canvas.setFillColor(colors.HexColor("#475569"))
    canvas.drawString(doc.leftMargin, 8 * mm, "Data Dictionary")
    canvas.drawRightString(page_width - doc.rightMargin, 8 * mm, f"Page {page_number}")
    canvas.restoreState()


def build_data_dictionary_pdf(
    output_path,
    entries,
    version_number: str,
    generated_at: datetime,
    source_label: str = f"{CONTROL_SCHEMA}.{DATA_DICTIONARY_TABLE}",
):
    output_file = Path(output_path)
    output_file.parent.mkdir(parents=True, exist_ok=True)

    styles = _build_styles()
    doc = DictionaryPdfDocTemplate(
        str(output_file),
        pagesize=A4,
        leftMargin=16 * mm,
        rightMargin=16 * mm,
        topMargin=18 * mm,
        bottomMargin=18 * mm,
        title=f"Data Dictionary v{version_number}",
        author=APP_NAME,
        subject="Data Dictionary export",
    )

    entries = list(entries or [])
    overview_table = DictionaryCatalogOverview(styles)
    story = [
        Spacer(1, 30 * mm),
        Paragraph(APP_NAME, styles["cover_kicker"]),
        Paragraph("Portable PostgreSQL administration and data tooling", styles["cover_org"]),
        Paragraph("Data Dictionary Reference", styles["cover_title"]),
        Paragraph(
            "Structured export of the complete controlled data dictionary, organized for review and reference.",
            styles["cover_subtitle"],
        ),
        Spacer(1, 8 * mm),
        _build_cover_metadata_table(
            styles=styles,
            version_number=version_number,
            generated_at=generated_at,
            entry_count=len(entries),
            source_label=source_label,
        ),
        Spacer(1, 14 * mm),
        PageBreak(),
    ]

    if entries:
        story.append(Paragraph("1.0 Catalog Overview", styles["section_title"]))
        story.append(overview_table)
        story.append(Spacer(1, 8 * mm))
        story.append(Paragraph("2.0 Dictionary Entries", styles["section_title"]))
        for index, entry in enumerate(entries, start=1):
            item_label = f"2.{index}"
            bookmark_key = f"dictionary_entry_{index:04d}"
            entry_title = Paragraph(
                f'<a name="{bookmark_key}"/>{item_label} {html.escape(str(entry.get("standard_name") or "(unnamed)"))}',
                styles["entry_title"],
            )
            entry_title._dictionary_entry_payload = {
                "item_label": item_label,
                "standard_name": str(entry.get("standard_name") or ""),
                "aliases": str(entry.get("aliases") or ""),
                "bookmark_key": bookmark_key,
            }
            story.append(entry_title)
            story.append(_build_entry_table(styles, entry))
            story.append(Spacer(1, 5 * mm))
    else:
        story.append(
            Paragraph(
                "No entries were found in the data dictionary at the time this PDF was generated.",
                styles["body"],
            )
        )

    doc.multiBuild(story, onFirstPage=_draw_page_chrome, onLaterPages=_draw_page_chrome)
    return output_file
