#!/usr/bin/env python3
"""
Generate PDF analysis report for IntelliScale CTL Workflow bugs.
"""
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import inch, mm
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT, TA_JUSTIFY
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    PageBreak, KeepTogether, HRFlowable, ListFlowable, ListItem
)
from reportlab.graphics.shapes import Drawing, Rect, String, Line, Polygon, Group, Circle
from reportlab.graphics import renderPDF
from io import BytesIO
from datetime import datetime

# ── Colour Palette ──────────────────────────────────────────────
C_PRIMARY = colors.HexColor('#1e3a5f')
C_ACCENT = colors.HexColor('#2563eb')
C_SUCCESS = colors.HexColor('#16a34a')
C_DANGER = colors.HexColor('#dc2626')
C_WARNING = colors.HexColor('#d97706')
C_LIGHT_BG = colors.HexColor('#f8fafc')
C_BORDER = colors.HexColor('#e2e8f0')
C_TEXT = colors.HexColor('#1e293b')
C_TEXT_LIGHT = colors.HexColor('#64748b')
C_FLOW_BG = colors.HexColor('#dbeafe')
C_FLOW_DECISION = colors.HexColor('#fef3c7')
C_FLOW_ACTION = colors.HexColor('#e0f2fe')
C_FLOW_ERROR = colors.HexColor('#fee2e2')
C_FLOW_SUCCESS = colors.HexColor('#dcfce7')
C_FLOW_BORDER = colors.HexColor('#94a3b8')


def build_styles():
    styles = getSampleStyleSheet()

    styles.add(ParagraphStyle(
        'DocTitle', parent=styles['Normal'], fontSize=28, fontName='Helvetica-Bold',
        textColor=C_PRIMARY, spaceAfter=6, alignment=TA_LEFT))
    styles.add(ParagraphStyle(
        'DocSubtitle', parent=styles['Normal'], fontSize=14, fontName='Helvetica',
        textColor=C_TEXT_LIGHT, spaceAfter=20, alignment=TA_LEFT))
    styles.add(ParagraphStyle(
        'SectionHead', parent=styles['Normal'], fontSize=18, fontName='Helvetica-Bold',
        textColor=C_PRIMARY, spaceBefore=24, spaceAfter=10))
    styles.add(ParagraphStyle(
        'SubHead', parent=styles['Normal'], fontSize=14, fontName='Helvetica-Bold',
        textColor=C_ACCENT, spaceBefore=16, spaceAfter=6))
    styles.add(ParagraphStyle(
        'SubHead2', parent=styles['Normal'], fontSize=12, fontName='Helvetica-Bold',
        textColor=C_TEXT, spaceBefore=12, spaceAfter=4))
    styles.add(ParagraphStyle(
        'Body', parent=styles['Normal'], fontSize=10, fontName='Helvetica',
        textColor=C_TEXT, leading=14, spaceAfter=6, alignment=TA_JUSTIFY))
    styles.add(ParagraphStyle(
        'BodyBold', parent=styles['Normal'], fontSize=10, fontName='Helvetica-Bold',
        textColor=C_TEXT, leading=14, spaceAfter=6))
    styles.add(ParagraphStyle(
        'CodeBlock', parent=styles['Normal'], fontSize=8.5, fontName='Courier',
        textColor=C_TEXT, leading=11, spaceAfter=4, leftIndent=12,
        backColor=colors.HexColor('#f1f5f9')))
    styles.add(ParagraphStyle(
        'BugTitle', parent=styles['Normal'], fontSize=12, fontName='Helvetica-Bold',
        textColor=C_DANGER, spaceBefore=14, spaceAfter=4))
    styles.add(ParagraphStyle(
        'RecTitle', parent=styles['Normal'], fontSize=12, fontName='Helvetica-Bold',
        textColor=C_SUCCESS, spaceBefore=14, spaceAfter=4))
    styles.add(ParagraphStyle(
        'Caption', parent=styles['Normal'], fontSize=9, fontName='Helvetica-Oblique',
        textColor=C_TEXT_LIGHT, alignment=TA_CENTER, spaceBefore=4, spaceAfter=12))
    styles.add(ParagraphStyle(
        'SmallNote', parent=styles['Normal'], fontSize=8, fontName='Helvetica',
        textColor=C_TEXT_LIGHT, leading=10, spaceAfter=4))
    styles.add(ParagraphStyle(
        'FlowLabel', parent=styles['Normal'], fontSize=7.5, fontName='Helvetica',
        textColor=C_TEXT, leading=9, alignment=TA_CENTER))
    return styles


# ── Flowchart Drawing Helpers ───────────────────────────────────
def _rounded_rect(d, x, y, w, h, fill, stroke=C_FLOW_BORDER, r=6):
    """Draw a rounded rectangle (approximated with a regular rect + radius)."""
    rect = Rect(x, y, w, h, rx=r, ry=r, fillColor=fill, strokeColor=stroke, strokeWidth=0.75)
    d.add(rect)


def _diamond(d, cx, cy, hw, hh, fill, stroke=C_FLOW_BORDER):
    """Draw a diamond (decision) centred at (cx, cy)."""
    pts = [cx, cy + hh, cx + hw, cy, cx, cy - hh, cx - hw, cy]
    poly = Polygon(pts, fillColor=fill, strokeColor=stroke, strokeWidth=0.75)
    d.add(poly)


def _text_block(d, cx, cy, text, font='Helvetica', size=7.5, color=C_TEXT):
    """Centre a single-line text string at (cx, cy)."""
    s = String(cx, cy - size * 0.35, text, fontName=font, fontSize=size, fillColor=color, textAnchor='middle')
    d.add(s)


def _multiline_text(d, cx, top_y, lines, font='Helvetica', size=7.5, color=C_TEXT, leading=10):
    """Centre multiple lines of text starting from top_y downward."""
    for i, line in enumerate(lines):
        _text_block(d, cx, top_y - i * leading, line, font, size, color)


def _arrow_down(d, x, y1, y2, color=C_FLOW_BORDER):
    """Vertical arrow from (x, y1) down to (x, y2)."""
    d.add(Line(x, y1, x, y2, strokeColor=color, strokeWidth=0.75))
    # arrowhead
    d.add(Polygon([x, y2, x - 3, y2 + 6, x + 3, y2 + 6],
                  fillColor=color, strokeColor=color, strokeWidth=0))


def _arrow_right(d, x1, y, x2, color=C_FLOW_BORDER):
    """Horizontal arrow from (x1, y) to (x2, y)."""
    d.add(Line(x1, y, x2, y, strokeColor=color, strokeWidth=0.75))
    d.add(Polygon([x2, y, x2 - 6, y + 3, x2 - 6, y - 3],
                  fillColor=color, strokeColor=color, strokeWidth=0))


def _arrow_left(d, x1, y, x2, color=C_FLOW_BORDER):
    """Horizontal arrow from (x1, y) to (x2, y) pointing left."""
    d.add(Line(x1, y, x2, y, strokeColor=color, strokeWidth=0.75))
    d.add(Polygon([x2, y, x2 + 6, y + 3, x2 + 6, y - 3],
                  fillColor=color, strokeColor=color, strokeWidth=0))


def _label_on_line(d, x, y, text, font='Helvetica', size=6.5, color=C_TEXT_LIGHT):
    s = String(x, y, text, fontName=font, fontSize=size, fillColor=color, textAnchor='middle')
    d.add(s)


# ── CTL Workflow Flowchart ──────────────────────────────────────
def draw_ctl_workflow():
    W, H = 500, 720
    d = Drawing(W, H)

    # Background
    d.add(Rect(0, 0, W, H, fillColor=colors.white, strokeColor=None))

    # Title
    _text_block(d, W / 2, H - 15, 'CTL WORKFLOW (ctl_workflow) - New Bales Flow',
                'Helvetica-Bold', 11, C_PRIMARY)

    # Column positions
    col1 = 130  # Main flow
    col2 = 370  # Side actions / errors

    # ── Row 1: Start ──
    y = H - 50
    _rounded_rect(d, col1 - 50, y - 12, 100, 24, C_FLOW_BG)
    _text_block(d, col1, y, 'Operator Scans Barcode')

    # ── Row 2: Search for DNote ──
    _arrow_down(d, col1, y - 12, y - 40)
    y -= 55
    _rounded_rect(d, col1 - 65, y - 15, 130, 30, C_FLOW_ACTION)
    _multiline_text(d, col1, y + 5, ['Search all DeliveryNotes', 'for barcode match'])

    # ── Row 3: Found? ──
    _arrow_down(d, col1, y - 15, y - 40)
    y -= 58
    _diamond(d, col1, y, 55, 22, C_FLOW_DECISION)
    _text_block(d, col1, y, 'Found?')

    # No path
    _arrow_right(d, col1 + 55, y, col2 - 40)
    _rounded_rect(d, col2 - 40, y - 10, 80, 20, C_FLOW_ERROR)
    _text_block(d, col2, y, 'Error: Not Found')
    _label_on_line(d, (col1 + 55 + col2 - 40) / 2, y + 6, 'No')

    # Yes path
    _label_on_line(d, col1 - 15, y - 25, 'Yes')
    _arrow_down(d, col1, y - 22, y - 46)

    # ── Row 4: Check state == 'checked' ──
    y -= 64
    _diamond(d, col1, y, 65, 22, C_FLOW_DECISION)
    _text_block(d, col1, y, "State == 'checked'?")

    # No path
    _arrow_right(d, col1 + 65, y, col2 - 50)
    _rounded_rect(d, col2 - 50, y - 10, 100, 20, C_FLOW_ERROR)
    _text_block(d, col2, y, 'Error: Not Checked')
    _label_on_line(d, (col1 + 65 + col2 - 50) / 2, y + 6, 'No')

    _label_on_line(d, col1 - 15, y - 25, 'Yes')
    _arrow_down(d, col1, y - 22, y - 46)

    # ── Row 5: Another DNote scanning? ──
    y -= 64
    _diamond(d, col1, y, 65, 22, C_FLOW_DECISION)
    _text_block(d, col1, y, 'Another active?')

    _arrow_right(d, col1 + 65, y, col2 - 55)
    _rounded_rect(d, col2 - 55, y - 10, 110, 20, C_FLOW_ERROR)
    _text_block(d, col2, y, 'Error: Another active')
    _label_on_line(d, (col1 + 65 + col2 - 55) / 2, y + 6, 'Yes')

    _label_on_line(d, col1 - 15, y - 25, 'No')
    _arrow_down(d, col1, y - 22, y - 46)

    # ── Row 6: Activate DNote ──
    y -= 60
    _rounded_rect(d, col1 - 65, y - 15, 130, 30, C_FLOW_ACTION)
    _multiline_text(d, col1, y + 5, ['Activate DNote', 'is_being_scanned = True'])

    _arrow_down(d, col1, y - 15, y - 40)

    # ── Row 7: Create WeighingRecord ──
    y -= 55
    _rounded_rect(d, col1 - 65, y - 15, 130, 30, C_FLOW_ACTION)
    _multiline_text(d, col1, y + 5, ['Create WeighingRecord', 'with scale weight'])

    _arrow_down(d, col1, y - 15, y - 40)

    # ── Row 8: Call ERP ──
    y -= 55
    _rounded_rect(d, col1 - 65, y - 15, 130, 30, C_FLOW_ACTION)
    _multiline_text(d, col1, y + 5, ['Call ERP API', '/api/bales/update-mass/'])

    # ERP fail path
    _arrow_right(d, col1 + 65, y, col2 - 40)
    _rounded_rect(d, col2 - 40, y - 10, 80, 20, C_FLOW_ERROR)
    _text_block(d, col2, y, 'Sync Error')
    _label_on_line(d, (col1 + 65 + col2 - 40) / 2, y + 6, 'Fail')

    _label_on_line(d, col1 - 15, y - 18, 'OK')
    _arrow_down(d, col1, y - 15, y - 38)

    # ── Row 9: Add to scanned list ──
    y -= 52
    _rounded_rect(d, col1 - 65, y - 15, 130, 30, C_FLOW_ACTION)
    _multiline_text(d, col1, y + 5, ['add_scanned_barcode()', 'scanned_bales_count += 1'])

    _arrow_down(d, col1, y - 15, y - 38)

    # ── Row 10: All scanned? ──
    y -= 55
    _diamond(d, col1, y, 60, 22, C_FLOW_DECISION)
    _text_block(d, col1, y, 'All scanned?')

    # No → loop back (just show text)
    _arrow_right(d, col1 + 60, y, col2 - 55)
    _rounded_rect(d, col2 - 55, y - 10, 110, 20, C_FLOW_BG)
    _text_block(d, col2, y, 'Wait for next scan')
    _label_on_line(d, (col1 + 60 + col2 - 55) / 2, y + 6, 'No')

    _label_on_line(d, col1 - 15, y - 25, 'Yes')
    _arrow_down(d, col1, y - 22, y - 46)

    # ── Row 11: User clicks Close ──
    y -= 60
    _rounded_rect(d, col1 - 65, y - 15, 130, 30, C_FLOW_ACTION)
    _multiline_text(d, col1, y + 5, ['User clicks Close DNote', '(explicit confirmation)'])

    _arrow_down(d, col1, y - 15, y - 38)

    # ── Row 12: ERP update status ──
    y -= 52
    _rounded_rect(d, col1 - 70, y - 15, 140, 30, C_FLOW_SUCCESS)
    _multiline_text(d, col1, y + 5, ['ERP: update-status → laid', "Django: status='Closed'"])

    return d


# ── CTL Workflow Recall Flowchart ───────────────────────────────
def draw_ctl_recall_flow():
    W, H = 500, 650
    d = Drawing(W, H)
    d.add(Rect(0, 0, W, H, fillColor=colors.white, strokeColor=None))

    _text_block(d, W / 2, H - 15, 'CTL WORKFLOW - Recall & Reweigh Flow (WITH BUGS)',
                'Helvetica-Bold', 11, C_DANGER)

    col1 = 130
    col2 = 370

    # Row 1: DNote in laid state
    y = H - 50
    _rounded_rect(d, col1 - 65, y - 12, 130, 24, C_FLOW_BG)
    _text_block(d, col1, y, "DNote: state='laid', closed")

    _arrow_down(d, col1, y - 12, y - 36)

    # Row 2: User triggers recall
    y -= 50
    _rounded_rect(d, col1 - 65, y - 15, 130, 30, C_FLOW_ACTION)
    _multiline_text(d, col1, y + 5, ['User: Recall DNote', 'API: recall-all-bale'])

    _arrow_down(d, col1, y - 15, y - 38)

    # Row 3: Odoo side
    y -= 52
    _rounded_rect(d, col1 - 70, y - 20, 140, 40, C_FLOW_SUCCESS)
    _multiline_text(d, col1, y + 10, ["Odoo: state -> 'checked'", 'All bale masses -> 0', 'Bale states -> open'])

    # Django side (parallel)
    _arrow_right(d, col1 + 70, y, col2 - 60)
    _rounded_rect(d, col2 - 60, y - 20, 120, 40, C_FLOW_ERROR)
    _multiline_text(d, col2, y + 10, ["BUG: Django odoo_data", "state STILL 'laid'", "status STILL 'Closed'"], color=C_DANGER)

    _arrow_down(d, col1, y - 20, y - 44)

    # Row 4: Django reset
    y -= 58
    _rounded_rect(d, col1 - 65, y - 15, 130, 30, C_FLOW_ACTION)
    _multiline_text(d, col1, y + 5, ['Django: scanned = 0', 'WeighingRecords deleted'])

    _arrow_down(d, col1, y - 15, y - 38)

    # Row 5: RACE CONDITION
    y -= 58
    _rounded_rect(d, 20, y - 25, W - 40, 50, colors.HexColor('#fef2f2'), stroke=C_DANGER)
    _text_block(d, W / 2, y + 12, 'RACE CONDITION WINDOW', 'Helvetica-Bold', 9, C_DANGER)
    _multiline_text(d, W / 2, y - 2, [
        'Sync task may read DB BEFORE recall saves, then OVERWRITE scanned data back to old values',
        'Result: scanned_bales_count restored to 10, but all WeighingRecords deleted & Odoo masses = 0',
    ], size=7, leading=10)

    _arrow_down(d, col1, y - 25, y - 50)

    # Row 6: Sync runs
    y -= 68
    _rounded_rect(d, col1 - 70, y - 20, 140, 40, C_FLOW_ACTION)
    _multiline_text(d, col1, y + 10, ['Sync task runs (every 30s)', "Updates odoo_data: state='checked'", "Sets status='Open'"])

    # If race: overwrite
    _arrow_right(d, col1 + 70, y, col2 - 65)
    _rounded_rect(d, col2 - 65, y - 20, 130, 40, C_FLOW_ERROR)
    _multiline_text(d, col2, y + 10, ['IF RACE: save() overwrites', 'scanned_bales_count = 10', 'scanned_barcodes = [all 10]'], color=C_DANGER)

    _arrow_down(d, col1, y - 20, y - 44)

    # Row 7: Celery check
    y -= 62
    _rounded_rect(d, 20, y - 25, W - 40, 50, colors.HexColor('#fef2f2'), stroke=C_DANGER)
    _text_block(d, W / 2, y + 12, 'check_completed_delivery_notes (every 1 min)', 'Helvetica-Bold', 9, C_DANGER)
    _multiline_text(d, W / 2, y - 2, [
        "Finds: is_being_scanned=False, status='Open', scanned_count=10 (overwritten), state='checked'",
        "is_scanning_complete() = True -> FIRES! Sets Odoo state to 'laid' with ALL BALES AT 0 MASS",
    ], size=7, leading=10, color=C_DANGER)

    _arrow_down(d, col1, y - 25, y - 50)

    # Row 8: Result
    y -= 68
    _rounded_rect(d, col1 - 80, y - 20, 160, 40, C_FLOW_ERROR, stroke=C_DANGER)
    _multiline_text(d, col1, y + 10, ["RESULT: DNote set to 'laid'", 'All 10 bales have 0 mass', 'Data integrity compromised'], 'Helvetica-Bold', 8, C_DANGER)

    return d


# ── CTL Commercial Workflow Flowchart ───────────────────────────
def draw_ctl_commercial_workflow():
    W, H = 500, 700
    d = Drawing(W, H)
    d.add(Rect(0, 0, W, H, fillColor=colors.white, strokeColor=None))

    _text_block(d, W / 2, H - 15, 'CTL COMMERCIAL WORKFLOW (ctl_commercial_workflow)',
                'Helvetica-Bold', 11, C_PRIMARY)

    col1 = 130
    col2 = 370

    y = H - 50
    _rounded_rect(d, col1 - 50, y - 12, 100, 24, C_FLOW_BG)
    _text_block(d, col1, y, 'Operator Scans Barcode')

    _arrow_down(d, col1, y - 12, y - 40)
    y -= 55
    _rounded_rect(d, col1 - 65, y - 15, 130, 30, C_FLOW_ACTION)
    _multiline_text(d, col1, y + 5, ['Search DeliveryNotes', 'for barcode match'])

    _arrow_down(d, col1, y - 15, y - 40)
    y -= 58
    _diamond(d, col1, y, 55, 22, C_FLOW_DECISION)
    _text_block(d, col1, y, 'Found?')

    _arrow_right(d, col1 + 55, y, col2 - 40)
    _rounded_rect(d, col2 - 40, y - 10, 80, 20, C_FLOW_ERROR)
    _text_block(d, col2, y, 'Error: Not Found')
    _label_on_line(d, (col1 + 55 + col2 - 40) / 2, y + 6, 'No')

    _label_on_line(d, col1 - 15, y - 25, 'Yes')
    _arrow_down(d, col1, y - 22, y - 46)

    # NO state check for commercial!
    y -= 60
    _rounded_rect(d, col1 - 70, y - 12, 140, 24, colors.HexColor('#fef9c3'), stroke=C_WARNING)
    _text_block(d, col1, y, "NO state='checked' check!", 'Helvetica-Bold', 8, C_WARNING)

    _arrow_down(d, col1, y - 12, y - 36)

    # Activate
    y -= 50
    _diamond(d, col1, y, 65, 22, C_FLOW_DECISION)
    _text_block(d, col1, y, 'allow_bale_insert?')

    _label_on_line(d, col1 + 72, y + 6, 'Yes')
    _arrow_right(d, col1 + 65, y, col2 - 60)
    _rounded_rect(d, col2 - 60, y - 15, 120, 30, C_FLOW_ACTION)
    _multiline_text(d, col2, y + 5, ['Create new bale in Odoo', 'via create-commercial-bale'])

    _label_on_line(d, col1 - 15, y - 25, 'No')
    _arrow_down(d, col1, y - 22, y - 46)

    # Activate DNote
    y -= 60
    _rounded_rect(d, col1 - 65, y - 15, 130, 30, C_FLOW_ACTION)
    _multiline_text(d, col1, y + 5, ['Activate DNote', 'is_being_scanned = True'])

    _arrow_down(d, col1, y - 15, y - 40)

    # Create/Update WeighingRecord
    y -= 55
    _rounded_rect(d, col1 - 65, y - 15, 130, 30, C_FLOW_ACTION)
    _multiline_text(d, col1, y + 5, ['Create/Update', 'WeighingRecord'])

    _arrow_down(d, col1, y - 15, y - 40)

    # Call ERP
    y -= 55
    _rounded_rect(d, col1 - 65, y - 15, 130, 30, C_FLOW_ACTION)
    _multiline_text(d, col1, y + 5, ['Call ERP: update-mass', 'or create-commercial-bale'])

    _arrow_down(d, col1, y - 15, y - 40)

    # Track
    y -= 55
    _rounded_rect(d, col1 - 65, y - 15, 130, 30, C_FLOW_ACTION)
    _multiline_text(d, col1, y + 5, ['add_scanned_barcode()', 'Track completion'])

    _arrow_down(d, col1, y - 15, y - 40)

    # Close
    y -= 55
    _rounded_rect(d, col1 - 70, y - 20, 140, 40, C_FLOW_SUCCESS)
    _multiline_text(d, col1, y + 10, ['User: Close DNote', 'API: close-marshalled-at-scale', "(not update-status='laid')"])

    return d


# ── Background Tasks Flowchart ──────────────────────────────────
def draw_background_tasks():
    W, H = 500, 400
    d = Drawing(W, H)
    d.add(Rect(0, 0, W, H, fillColor=colors.white, strokeColor=None))

    _text_block(d, W / 2, H - 15, 'Background Celery Tasks & Their Interactions',
                'Helvetica-Bold', 11, C_PRIMARY)

    # Left: sync task
    lx = 130
    rx = 370

    y = H - 55
    _rounded_rect(d, lx - 80, y - 20, 160, 40, C_FLOW_BG)
    _multiline_text(d, lx, y + 10, ['sync_odoo_delivery_notes', '(every 30 seconds)'], 'Helvetica-Bold', 8)

    _arrow_down(d, lx, y - 20, y - 44)
    y -= 58
    _rounded_rect(d, lx - 80, y - 25, 160, 50, C_FLOW_ACTION)
    _multiline_text(d, lx, y + 12, ['1. Fetch from Odoo API', '2. Update odoo_data (full overwrite)', '3. Map state to status', "4. save() ALL fields"])

    # Right: check task
    y2 = H - 55
    _rounded_rect(d, rx - 80, y2 - 20, 160, 40, C_FLOW_BG)
    _multiline_text(d, rx, y2 + 10, ['check_completed_delivery_notes', '(every 1 minute)'], 'Helvetica-Bold', 8)

    _arrow_down(d, rx, y2 - 20, y2 - 44)
    y2 -= 58
    _rounded_rect(d, rx - 80, y2 - 25, 160, 50, C_FLOW_ACTION)
    _multiline_text(d, rx, y2 + 12, ['Filter: is_being_scanned=False', "status='Open', count > 0", "Check: is_scanning_complete()", "AND odoo_state == 'checked'"])

    _arrow_down(d, rx, y2 - 25, y2 - 50)
    y2 -= 64
    _rounded_rect(d, rx - 80, y2 - 15, 160, 30, C_FLOW_ERROR, stroke=C_DANGER)
    _multiline_text(d, rx, y2 + 5, ["If match: set Odoo -> 'laid'", "Django status -> 'Closed'"], color=C_DANGER)

    # Warning box
    y3 = 80
    _rounded_rect(d, 20, y3 - 30, W - 40, 60, colors.HexColor('#fef2f2'), stroke=C_DANGER)
    _text_block(d, W / 2, y3 + 18, 'CRITICAL: sync task does NOT use select_for_update() or any locking',
                'Helvetica-Bold', 8, C_DANGER)
    _multiline_text(d, W / 2, y3 - 2, [
        "sync_single_delivery_note reads all fields, modifies odoo_data/status, then save()'s ALL fields",
        'This can overwrite scanned_bales_count, scanned_barcodes, and is_being_scanned set by concurrent operations',
        "Combined with check_completed_delivery_notes, this creates the premature 'laid' bug",
    ], size=7, leading=10)

    return d


# ── Severity table helper ───────────────────────────────────────
def severity_badge(text, color):
    return f'<font color="{color}"><b>{text}</b></font>'


# ── Main document build ─────────────────────────────────────────
def build_pdf(output_path):
    doc = SimpleDocTemplate(
        output_path, pagesize=A4,
        topMargin=0.6 * inch, bottomMargin=0.6 * inch,
        leftMargin=0.7 * inch, rightMargin=0.7 * inch,
        title='IntelliScale CTL Workflow Analysis',
        author='Claude Code Analysis'
    )

    S = build_styles()
    elements = []

    # ── Cover / Title ───────────────────────────────────────────
    elements.append(Spacer(1, 40))
    elements.append(Paragraph('IntelliScale CTL Workflow', S['DocTitle']))
    elements.append(Paragraph('Bug Analysis & Recommendations Report', S['DocSubtitle']))
    elements.append(HRFlowable(width='100%', thickness=2, color=C_PRIMARY, spaceAfter=12))

    meta = [
        ['Date:', datetime.now().strftime('%B %d, %Y')],
        ['Scope:', 'ctl_workflow recall/reweigh bug investigation'],
        ['Affected Component:', 'IntelliScale Django App + Odoo receiving module'],
        ['Severity:', 'CRITICAL - Data integrity risk (0-mass bales marked as laid)'],
    ]
    mt = Table(meta, colWidths=[1.5 * inch, 4.5 * inch])
    mt.setStyle(TableStyle([
        ('FONTNAME', (0, 0), (0, -1), 'Helvetica-Bold'),
        ('FONTNAME', (1, 0), (1, -1), 'Helvetica'),
        ('FONTSIZE', (0, 0), (-1, -1), 10),
        ('TEXTCOLOR', (0, 0), (0, -1), C_TEXT_LIGHT),
        ('TEXTCOLOR', (1, 0), (1, -1), C_TEXT),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
    ]))
    elements.append(mt)
    elements.append(Spacer(1, 16))

    # Executive Summary
    elements.append(Paragraph('Executive Summary', S['SectionHead']))
    elements.append(Paragraph(
        'The <b>ctl_workflow</b> process in IntelliScale correctly handles the initial weighing of bales '
        'and creation of delivery notes. However, when a delivery note needs to be <b>recalled</b> (for reweighing), '
        'a critical race condition between the <b>Celery sync task</b> and the <b>recall operation</b> causes the '
        'delivery note to be prematurely set to <b>"laid"</b> status in Odoo with all bales still at <b>0 mass</b>. '
        'This report identifies <b>7 bugs</b> in the recall/reweigh flow, with the race condition being the root cause '
        'of the reported issue.', S['Body']))

    elements.append(Paragraph(
        'The core problem: <b>sync_single_delivery_note()</b> reads the delivery note from the database, modifies '
        'odoo_data and status fields, then calls <b>save()</b> which writes ALL model fields back. If a recall operation '
        'resets scanned_bales_count to 0 between the sync\'s read and save, the sync <b>overwrites the reset</b>, '
        'restoring the old count. The <b>check_completed_delivery_notes</b> Celery task then sees what appears to be '
        'a fully-scanned delivery note with state="checked" and fires, setting Odoo to "laid".', S['Body']))

    elements.append(PageBreak())

    # ── TABLE OF CONTENTS ───────────────────────────────────────
    elements.append(Paragraph('Table of Contents', S['SectionHead']))
    toc_items = [
        '1. Workflow Descriptions',
        '2. CTL Workflow Flowchart - Normal Flow',
        '3. CTL Workflow Flowchart - Recall Flow (with bugs)',
        '4. CTL Commercial Workflow Flowchart',
        '5. Background Tasks Interaction Diagram',
        '6. Bug Registry (7 bugs identified)',
        '7. Root Cause Analysis',
        '8. Recommendations & Fixes',
        '9. Files Affected',
    ]
    for item in toc_items:
        elements.append(Paragraph(item, S['Body']))
    elements.append(PageBreak())

    # ── SECTION 1: Workflow Descriptions ────────────────────────
    elements.append(Paragraph('1. Workflow Descriptions', S['SectionHead']))

    elements.append(Paragraph('1.1 ctl_workflow (Grower Workflow)', S['SubHead']))
    elements.append(Paragraph(
        'The <b>ctl_workflow</b> is the primary process for weighing grower tobacco bales. It requires delivery notes '
        'to be in <b>"checked"</b> state before scanning can begin. The flow is:', S['Body']))

    steps_ctl = [
        'Odoo creates delivery notes with bales (synced to IntelliScale every 30s)',
        'Delivery note must be in "checked" state (verified by receiving staff in Odoo)',
        'Operator scans a bale barcode at the weighing station',
        'System finds the matching delivery note, activates it for scanning',
        'Scale weight is captured and sent to Odoo via /api/bales/update-mass/',
        'Barcode is added to the scanned list, count incremented',
        'Repeat until all bales are scanned (is_scanning_complete() == True)',
        'Operator explicitly clicks "Close Delivery Note" to set Odoo state to "laid"',
    ]
    for i, step in enumerate(steps_ctl, 1):
        elements.append(Paragraph(f'<b>{i}.</b> {step}', S['Body']))

    elements.append(Paragraph('1.2 ctl_commercial_workflow (Commercial Workflow)', S['SubHead']))
    elements.append(Paragraph(
        'The <b>ctl_commercial_workflow</b> is similar but designed for commercial/marshalled bales. Key differences:', S['Body']))

    diffs = [
        '<b>No state check</b>: Does NOT require state="checked" before scanning',
        '<b>allow_bale_insert</b>: Can create new bales in Odoo via /api/bales/create-commercial-bale',
        '<b>allow_marshalling</b>: Supports lot_number and group_number marshalling fields',
        '<b>Different close endpoint</b>: Uses /api/grower-delivery-notes/close-marshalled-at-scale',
        'Uses <b>number_of_bales_delivered</b> instead of number_of_bales for completion tracking',
    ]
    for diff in diffs:
        elements.append(Paragraph(f'  - {diff}', S['Body']))

    elements.append(Paragraph('1.3 Recall Mechanisms', S['SubHead']))
    elements.append(Paragraph('There are three recall mechanisms available:', S['Body']))

    recall_table_data = [
        ['Mechanism', 'Endpoint', 'Odoo Effect', 'Django Effect'],
        ['Full DNote Recall\n(recall_delivery_note)', '/api/bales/recall-all-bale', "All masses -> 0\nState -> 'checked'", 'Records deleted\nscanned = 0'],
        ['Single Bale Recall\n(recall_bale view)', '/api/bales/recall-bale/', 'Bale mass -> 0\nDNote state unchanged', 'Record deleted\ncount -= 1'],
        ['Recall & Update\n(recall_and_update_bale)', '/api/bales/update-mass/\n(mass=0.01)', 'Bale mass -> 0.01\nDNote state unchanged', 'Record deleted\ncount -= 1'],
    ]
    rt = Table(recall_table_data, colWidths=[1.4 * inch, 1.5 * inch, 1.5 * inch, 1.5 * inch])
    rt.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), C_PRIMARY),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, 0), 8),
        ('FONTNAME', (0, 1), (-1, -1), 'Helvetica'),
        ('FONTSIZE', (0, 1), (-1, -1), 7.5),
        ('GRID', (0, 0), (-1, -1), 0.5, C_BORDER),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('TOPPADDING', (0, 0), (-1, -1), 6),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
        ('LEFTPADDING', (0, 0), (-1, -1), 6),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, C_LIGHT_BG]),
    ]))
    elements.append(rt)

    elements.append(PageBreak())

    # ── SECTION 2: CTL Workflow Flowchart ───────────────────────
    elements.append(Paragraph('2. CTL Workflow - Normal Flow (New Bales)', S['SectionHead']))
    elements.append(Paragraph(
        'This flowchart shows the normal scanning flow for new bales. This flow works correctly.',
        S['Body']))
    elements.append(draw_ctl_workflow())
    elements.append(Paragraph('Figure 1: CTL Workflow - Normal bale scanning flow', S['Caption']))

    elements.append(PageBreak())

    # ── SECTION 3: CTL Recall Flowchart ─────────────────────────
    elements.append(Paragraph('3. CTL Workflow - Recall & Reweigh Flow (WITH BUGS)', S['SectionHead']))
    elements.append(Paragraph(
        'This flowchart shows what happens when a delivery note is recalled for reweighing. '
        'The red annotations highlight the race condition that causes the reported bug.',
        S['Body']))
    elements.append(draw_ctl_recall_flow())
    elements.append(Paragraph('Figure 2: CTL Workflow - Recall flow showing race condition bug', S['Caption']))

    elements.append(PageBreak())

    # ── SECTION 4: CTL Commercial Workflow Flowchart ────────────
    elements.append(Paragraph('4. CTL Commercial Workflow', S['SectionHead']))
    elements.append(Paragraph(
        'The commercial workflow differs primarily in not requiring the "checked" state and supporting '
        'bale insertion (creating new bales in Odoo during scanning).', S['Body']))
    elements.append(draw_ctl_commercial_workflow())
    elements.append(Paragraph('Figure 3: CTL Commercial Workflow flow', S['Caption']))

    elements.append(PageBreak())

    # ── SECTION 5: Background Tasks ─────────────────────────────
    elements.append(Paragraph('5. Background Tasks Interaction', S['SectionHead']))
    elements.append(Paragraph(
        'Two Celery tasks run in the background and interact with the delivery note lifecycle. '
        'The lack of locking in the sync task is the enabler for the race condition bug.', S['Body']))
    elements.append(draw_background_tasks())
    elements.append(Paragraph('Figure 4: Background Celery tasks and their interactions', S['Caption']))

    elements.append(PageBreak())

    # ── SECTION 6: Bug Registry ─────────────────────────────────
    elements.append(Paragraph('6. Bug Registry', S['SectionHead']))
    elements.append(Paragraph(
        'Seven bugs were identified during the analysis. Bug #1 is the root cause of the reported issue. '
        'The remaining bugs are contributing factors or independent issues in the recall/reweigh flow.', S['Body']))

    # Bug 1
    elements.append(Paragraph('BUG-001: Race Condition - Sync Task Overwrites Recall Data [CRITICAL]', S['BugTitle']))
    elements.append(Paragraph(
        '<b>Location:</b> scale/tasks.py :: sync_single_delivery_note() + scale/views/delivery_note.py :: recall_delivery_note()', S['Body']))
    elements.append(Paragraph(
        '<b>Root Cause:</b> sync_single_delivery_note() reads ALL delivery note fields from the database, modifies only '
        'odoo_data and status, then calls save() which writes ALL fields back. If recall_delivery_note() resets '
        'scanned_bales_count to 0 and scanned_barcodes to [] between the sync\'s DB read and its save(), the sync '
        'overwrites these fields with their pre-recall values (e.g., count=10, barcodes=[all 10]).', S['Body']))
    elements.append(Paragraph(
        '<b>Impact:</b> After the overwrite, check_completed_delivery_notes() sees scanned_bales_count=10 (matching '
        'number_of_bales=10), odoo_state="checked", is_being_scanned=False, and status="Open". All conditions are met, '
        'so it calls update_dnote_completion_status() which sets the Odoo state to "laid" - even though all bales have '
        '0 mass. This is the exact bug reported by the user.', S['Body']))
    elements.append(Paragraph(
        '<b>Reproduction:</b> Trigger a delivery note recall while the sync task is running. The 30-second sync interval '
        'creates a high probability of overlap.', S['Body']))

    # Bug 2
    elements.append(Paragraph('BUG-002: recall_delivery_note Does Not Update Local odoo_data [HIGH]', S['BugTitle']))
    elements.append(Paragraph(
        '<b>Location:</b> scale/views/delivery_note.py :: recall_delivery_note() lines 154-159', S['Body']))
    elements.append(Paragraph(
        '<b>Description:</b> After the recall-all-bale API succeeds (setting Odoo state to "checked"), the local '
        'odoo_data[\'state\'] remains "laid". The ctl_workflow requires state=="checked" before scanning, so the '
        'operator cannot scan until the next sync task runs (up to 30 seconds). This creates an unnecessary delay and '
        'confusing UX where the recall appears to succeed but scanning is blocked.', S['Body']))
    elements.append(Paragraph(
        '<b>Fix:</b> After successful recall API, set delivery_note.odoo_data[\'state\'] = \'checked\' locally.', S['Body']))

    # Bug 3
    elements.append(Paragraph('BUG-003: recall_delivery_note Does Not Reset Local Status [HIGH]', S['BugTitle']))
    elements.append(Paragraph(
        '<b>Location:</b> scale/views/delivery_note.py :: recall_delivery_note() lines 154-159', S['Body']))
    elements.append(Paragraph(
        '<b>Description:</b> After recall, the local status field remains "Closed" (from the original close). '
        'The delivery note does not appear in the "Open" delivery notes list on the weighing station until '
        'the sync task runs and sets status="Open". Combined with BUG-002, this means the delivery note is '
        'invisible and unscalable for up to 30 seconds after recall.', S['Body']))
    elements.append(Paragraph(
        '<b>Fix:</b> After successful recall API, set delivery_note.status = \'Open\'.', S['Body']))

    # Bug 4
    elements.append(Paragraph('BUG-004: Sync Maps "laid" State to "Open" Status [MEDIUM]', S['BugTitle']))
    elements.append(Paragraph(
        '<b>Location:</b> scale/tasks.py :: sync_single_delivery_note() lines 121-124', S['Body']))
    elements.append(Paragraph(
        '<b>Description:</b> The state-to-status mapping includes "laid" in the list that maps to "Open": '
        '<font face="Courier" size="8">if odoo_record[\'state\'] in [\'open\', \'checked\', \'laid\']: '
        'delivery_note.status = \'Open\'</font>. This means after a delivery note is legitimately closed '
        '(state="laid", status="Closed"), the next sync resets status back to "Open". While '
        'check_completed_delivery_notes requires odoo_state=="checked" (not "laid") to fire, this creates '
        'an inconsistent local state and can interact with other bugs.', S['Body']))
    elements.append(Paragraph(
        '<b>Fix:</b> Change the mapping to exclude "laid" from the "Open" status mapping. Delivery notes with '
        'state="laid" should map to status="Closed" locally.', S['Body']))

    # Bug 5
    elements.append(Paragraph('BUG-005: Individual Bale Recall Does Not Transition DNote State [MEDIUM]', S['BugTitle']))
    elements.append(Paragraph(
        '<b>Location:</b> scale/views/ajax.py :: recall_and_update_bale() and scale/views/delivery_note.py :: recall_bale()', S['Body']))
    elements.append(Paragraph(
        '<b>Description:</b> When individual bales are recalled from a "laid" delivery note, the Odoo delivery note '
        'state remains "laid". The ctl_workflow requires state=="checked" to scan, so the operator cannot rescan '
        'the recalled bale. The recall_and_update_bale endpoint calls /api/bales/update-mass/ with mass=0.01 '
        '(not /api/bales/recall-bale/), and neither endpoint changes the delivery note state. '
        'The operator is stuck: the bale is recalled but cannot be re-weighed.', S['Body']))
    elements.append(Paragraph(
        '<b>Fix:</b> After individual bale recall on a "laid" dnote, call the Odoo API to transition the delivery '
        'note state back to "checked", or add an IntelliScale-specific endpoint that handles this transition.', S['Body']))

    # Bug 6
    elements.append(Paragraph('BUG-006: recall_and_update_bale Uses update-mass Instead of recall-bale [LOW]', S['BugTitle']))
    elements.append(Paragraph(
        '<b>Location:</b> scale/views/ajax.py :: recall_and_update_bale() line 106', S['Body']))
    elements.append(Paragraph(
        '<b>Description:</b> The recall_and_update_bale endpoint calls /api/bales/update-mass/?mass=0.01 instead of '
        '/api/bales/recall-bale/. The update-mass API validates mass > 0, so 0.01 is used as a workaround. However, '
        'the recall-bale API exists specifically for this purpose and sets mass to exactly 0. Using update-mass '
        'leaves bales with mass=0.01 in Odoo rather than 0, which could affect mass totals and reports.', S['Body']))
    elements.append(Paragraph(
        '<b>Fix:</b> Use the /api/bales/recall-bale/ endpoint for individual bale recalls.', S['Body']))

    # Bug 7
    elements.append(Paragraph('BUG-007: No Concurrency Protection on DeliveryNote save() [MEDIUM]', S['BugTitle']))
    elements.append(Paragraph(
        '<b>Location:</b> scale/tasks.py :: sync_single_delivery_note() and scale/models/delivery_note.py', S['Body']))
    elements.append(Paragraph(
        '<b>Description:</b> The sync task uses a plain get_or_create() + save() pattern without any concurrency '
        'control. Since IntelliScale uses SQLite (which doesn\'t support row-level locking), even Django\'s '
        'select_for_update() in the weighing station view provides limited protection. Any concurrent modification '
        'of a delivery note during sync can lead to lost updates. This affects not just recall but also normal '
        'scanning operations that coincide with sync cycles.', S['Body']))
    elements.append(Paragraph(
        '<b>Fix:</b> Use update() with specific fields instead of save() in the sync task. Alternatively, use '
        'F() expressions or conditional updates to avoid overwriting fields managed by other operations.', S['Body']))

    elements.append(PageBreak())

    # ── Bug Summary Table ───────────────────────────────────────
    elements.append(Paragraph('Bug Summary', S['SubHead']))
    bug_summary = [
        ['ID', 'Severity', 'Description', 'Status'],
        ['BUG-001', 'CRITICAL', 'Race condition: sync overwrites recall data', 'Root Cause'],
        ['BUG-002', 'HIGH', 'Recall does not update local odoo_data state', 'Contributing'],
        ['BUG-003', 'HIGH', 'Recall does not reset local status', 'Contributing'],
        ['BUG-004', 'MEDIUM', 'Sync maps "laid" to "Open" status', 'Independent'],
        ['BUG-005', 'MEDIUM', 'Individual recall blocks rescan (state stuck)', 'Independent'],
        ['BUG-006', 'LOW', 'Uses update-mass instead of recall-bale', 'Independent'],
        ['BUG-007', 'MEDIUM', 'No concurrency protection on sync save()', 'Enabler'],
    ]
    bs = Table(bug_summary, colWidths=[0.7 * inch, 0.85 * inch, 3.1 * inch, 0.95 * inch])
    bs.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), C_PRIMARY),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 8.5),
        ('FONTNAME', (0, 1), (-1, -1), 'Helvetica'),
        ('GRID', (0, 0), (-1, -1), 0.5, C_BORDER),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('TOPPADDING', (0, 0), (-1, -1), 6),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
        ('LEFTPADDING', (0, 0), (-1, -1), 6),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, C_LIGHT_BG]),
        # Color severity cells
        ('TEXTCOLOR', (1, 1), (1, 1), C_DANGER),  # CRITICAL
        ('TEXTCOLOR', (1, 2), (1, 3), C_WARNING),  # HIGH
        ('TEXTCOLOR', (1, 4), (1, 4), colors.HexColor('#b45309')),  # MEDIUM
        ('TEXTCOLOR', (1, 5), (1, 5), colors.HexColor('#b45309')),  # MEDIUM
        ('TEXTCOLOR', (1, 6), (1, 6), C_SUCCESS),  # LOW
        ('TEXTCOLOR', (1, 7), (1, 7), colors.HexColor('#b45309')),  # MEDIUM
        ('FONTNAME', (1, 1), (1, -1), 'Helvetica-Bold'),
    ]))
    elements.append(bs)

    elements.append(PageBreak())

    # ── SECTION 7: Root Cause Analysis ──────────────────────────
    elements.append(Paragraph('7. Root Cause Analysis', S['SectionHead']))

    elements.append(Paragraph('The Sequence of Events', S['SubHead']))
    elements.append(Paragraph(
        'The reported bug ("scanning the first barcode on a recalled dnote sets it to laid") is caused by '
        'the following sequence:', S['Body']))

    sequence = [
        ['Step', 'Action', 'Django State', 'Odoo State'],
        ['1', 'All 10 bales scanned, dnote closed', "status='Closed'\nscanned_count=10\nis_being_scanned=False", "state='laid'\nAll masses > 0"],
        ['2', 'User triggers Recall DNote', "API call to Odoo...\n(takes ~1-3 seconds)", "Processing recall..."],
        ['3', 'CONCURRENTLY: Sync task\nreads Django DB', "Sync reads:\nscanned_count=10\nscanned_barcodes=[all]", "(Sync reading Odoo API)"],
        ['4', 'Recall API returns OK.\nDjango resets data.', "scanned_count=0\nscanned_barcodes=[]\nRecords deleted", "state='checked'\nAll masses=0"],
        ['5', 'Sync task saves\n(overwrites!)', "scanned_count=10 (!!!)\nscanned_barcodes=[all]\nodoo_data.state='checked'\nstatus='Open'", "state='checked'\nAll masses=0"],
        ['6', 'check_completed task\nruns (every 1 min)', "Sees: count=10, complete,\nstate='checked'\nFIRES -> set to laid", "state='laid' (!!!)\nAll masses STILL 0"],
        ['7', 'Operator scans barcode', "ERROR: state='laid'\nnot 'checked'\nOR dnote already laid", "CORRUPTED:\n10 bales with 0 mass\nmarked as 'laid'"],
    ]
    sq = Table(sequence, colWidths=[0.5 * inch, 1.5 * inch, 2 * inch, 1.8 * inch])
    sq.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), C_PRIMARY),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 7.5),
        ('FONTNAME', (0, 1), (-1, -1), 'Helvetica'),
        ('GRID', (0, 0), (-1, -1), 0.5, C_BORDER),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('TOPPADDING', (0, 0), (-1, -1), 5),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
        ('LEFTPADDING', (0, 0), (-1, -1), 4),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, C_LIGHT_BG]),
        # Highlight the critical rows
        ('BACKGROUND', (0, 5), (-1, 5), colors.HexColor('#fef2f2')),
        ('BACKGROUND', (0, 6), (-1, 6), colors.HexColor('#fef2f2')),
        ('BACKGROUND', (0, 7), (-1, 7), colors.HexColor('#fef2f2')),
        ('TEXTCOLOR', (2, 5), (2, 5), C_DANGER),
        ('TEXTCOLOR', (2, 6), (2, 6), C_DANGER),
        ('TEXTCOLOR', (3, 6), (3, 6), C_DANGER),
    ]))
    elements.append(sq)
    elements.append(Paragraph('Table 1: Race condition sequence of events (rows 5-7 highlighted as critical)', S['Caption']))

    elements.append(Paragraph('Why This Is Hard to Reproduce Consistently', S['SubHead']))
    elements.append(Paragraph(
        'The race condition depends on the sync task (30-second interval) reading the Django database '
        'BEFORE the recall operation saves, but completing its save AFTER. This is a ~1-3 second window '
        'during each 30-second cycle. The probability is roughly 3-10% per recall operation, making it '
        'intermittent but frequent enough to be a real production issue.', S['Body']))

    elements.append(PageBreak())

    # ── SECTION 8: Recommendations ──────────────────────────────
    elements.append(Paragraph('8. Recommendations', S['SectionHead']))

    # Rec 1
    elements.append(Paragraph('REC-001: Use update() Instead of save() in Sync Task [CRITICAL FIX]', S['RecTitle']))
    elements.append(Paragraph(
        'Change sync_single_delivery_note() to use Django\'s <b>update()</b> method with only the fields it owns, '
        'instead of the full-object save() pattern. This prevents overwriting fields managed by other operations.', S['Body']))
    elements.append(Paragraph(
        '<font face="Courier" size="8"># BEFORE (BUG): reads all fields, saves all fields<br/>'
        'delivery_note.odoo_data = odoo_record<br/>'
        'delivery_note.status = \'Open\'<br/>'
        'delivery_note.save()  # Overwrites scanned_bales_count, is_being_scanned, etc.<br/><br/>'
        '# AFTER (FIX): only update the fields the sync task owns<br/>'
        'DeliveryNote.objects.filter(pk=delivery_note.pk).update(<br/>'
        '&nbsp;&nbsp;&nbsp;&nbsp;odoo_data=odoo_record,<br/>'
        '&nbsp;&nbsp;&nbsp;&nbsp;delivery_note_number=document_number,<br/>'
        '&nbsp;&nbsp;&nbsp;&nbsp;status=mapped_status,<br/>'
        '&nbsp;&nbsp;&nbsp;&nbsp;is_synced=True,<br/>'
        '&nbsp;&nbsp;&nbsp;&nbsp;last_sync_attempt=timezone.now(),<br/>'
        '&nbsp;&nbsp;&nbsp;&nbsp;sync_error_message=\'\'<br/>'
        ')</font>', S['Body']))
    elements.append(Paragraph(
        '<b>Note:</b> Using update() bypasses the model\'s save() method (and thus QR code generation). '
        'QR code generation should be moved to a separate method or triggered only on creation.', S['Body']))

    # Rec 2
    elements.append(Paragraph('REC-002: Update Local State After Recall [HIGH PRIORITY]', S['RecTitle']))
    elements.append(Paragraph(
        'After a successful recall API call, immediately update the local delivery note state to match '
        'what Odoo now has. This eliminates the 30-second blind window and prevents UX confusion.', S['Body']))
    elements.append(Paragraph(
        '<font face="Courier" size="8"># In recall_delivery_note(), after successful API call:<br/>'
        'with transaction.atomic():<br/>'
        '&nbsp;&nbsp;&nbsp;&nbsp;WeighingRecord.objects.filter(delivery_note=delivery_note).delete()<br/>'
        '&nbsp;&nbsp;&nbsp;&nbsp;delivery_note.scanned_barcodes = []<br/>'
        '&nbsp;&nbsp;&nbsp;&nbsp;delivery_note.scanned_bales_count = 0<br/>'
        '&nbsp;&nbsp;&nbsp;&nbsp;delivery_note.status = \'Open\'  # NEW<br/>'
        '&nbsp;&nbsp;&nbsp;&nbsp;delivery_note.is_being_scanned = False  # NEW (explicit)<br/>'
        '&nbsp;&nbsp;&nbsp;&nbsp;if delivery_note.odoo_data:  # NEW<br/>'
        '&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;delivery_note.odoo_data[\'state\'] = \'checked\'<br/>'
        '&nbsp;&nbsp;&nbsp;&nbsp;delivery_note.save()</font>', S['Body']))

    # Rec 3
    elements.append(Paragraph('REC-003: Fix Status Mapping for "laid" State [MEDIUM]', S['RecTitle']))
    elements.append(Paragraph(
        'Change the sync task\'s state-to-status mapping so that "laid" maps to "Closed", not "Open". '
        'This prevents the sync from reopening legitimately closed delivery notes.', S['Body']))
    elements.append(Paragraph(
        '<font face="Courier" size="8"># BEFORE:<br/>'
        'if odoo_record[\'state\'] in [\'open\', \'checked\', \'laid\']:<br/>'
        '&nbsp;&nbsp;&nbsp;&nbsp;delivery_note.status = \'Open\'<br/><br/>'
        '# AFTER:<br/>'
        'if odoo_record[\'state\'] in [\'open\', \'checked\']:<br/>'
        '&nbsp;&nbsp;&nbsp;&nbsp;mapped_status = \'Open\'<br/>'
        'else:  # laid, closed, etc.<br/>'
        '&nbsp;&nbsp;&nbsp;&nbsp;mapped_status = \'Closed\'</font>', S['Body']))

    # Rec 4
    elements.append(Paragraph('REC-004: Add State Transition for Individual Bale Recall [MEDIUM]', S['RecTitle']))
    elements.append(Paragraph(
        'When individual bales are recalled from a "laid" delivery note, add logic to transition the delivery '
        'note state back to "checked" in Odoo. This enables the operator to rescan the recalled bale without '
        'needing a full delivery note recall.', S['Body']))
    elements.append(Paragraph(
        'Option A: Call the update-status API to set state="checked" when recalling from a "laid" dnote.<br/>'
        'Option B: Create a new Odoo API endpoint specifically for "reweigh mode" that partially reopens a dnote.', S['Body']))

    # Rec 5
    elements.append(Paragraph('REC-005: Use recall-bale API for Individual Recalls [LOW]', S['RecTitle']))
    elements.append(Paragraph(
        'Change recall_and_update_bale (ajax.py) to use /api/bales/recall-bale/ instead of '
        '/api/bales/update-mass/?mass=0.01. The recall-bale endpoint sets mass to exactly 0, which is '
        'semantically correct and doesn\'t leave stale mass values in the database.', S['Body']))

    # Rec 6
    elements.append(Paragraph('REC-006: Add Validation Guard to check_completed_delivery_notes [DEFENSE IN DEPTH]', S['RecTitle']))
    elements.append(Paragraph(
        'Add an additional check in the check_completed_delivery_notes task to verify that WeighingRecords '
        'actually exist for the scanned barcodes before setting the dnote to "laid". This provides a safety '
        'net against the race condition even before the sync task is fixed.', S['Body']))
    elements.append(Paragraph(
        '<font face="Courier" size="8"># In check_completed_delivery_notes(), before firing:<br/>'
        'actual_records = WeighingRecord.objects.filter(<br/>'
        '&nbsp;&nbsp;&nbsp;&nbsp;delivery_note=dnote, is_synced=True<br/>'
        ').count()<br/>'
        'if actual_records &lt; dnote.scanned_bales_count:<br/>'
        '&nbsp;&nbsp;&nbsp;&nbsp;logger.warning("Mismatch: scanned_count=%s but only %s records",<br/>'
        '&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;dnote.scanned_bales_count, actual_records)<br/>'
        '&nbsp;&nbsp;&nbsp;&nbsp;continue  # Skip - data may be corrupted by race condition</font>', S['Body']))

    elements.append(PageBreak())

    # ── SECTION 9: Files Affected ───────────────────────────────
    elements.append(Paragraph('9. Files Affected', S['SectionHead']))

    files_table = [
        ['File', 'Changes Required'],
        ['intelliscale/scale/tasks.py', 'sync_single_delivery_note: use update() instead of save()\ncheck_completed_delivery_notes: add WeighingRecord validation guard'],
        ['intelliscale/scale/views/delivery_note.py', 'recall_delivery_note: update local state after recall\nrecall_bale: add dnote state transition for "laid" dnotes'],
        ['intelliscale/scale/views/ajax.py', 'recall_and_update_bale: use recall-bale API\nAdd dnote state transition for "laid" dnotes'],
        ['intelliscale/scale/models/delivery_note.py', 'Consider removing QR generation from save() (move to explicit method)'],
        ['odoo-custom-apps/receiving/controllers/intelliscale_api.py', 'recall_bale: optionally transition dnote state when all bales have 0 mass'],
    ]
    ft = Table(files_table, colWidths=[2.5 * inch, 3.5 * inch])
    ft.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), C_PRIMARY),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 8.5),
        ('FONTNAME', (0, 1), (-1, -1), 'Helvetica'),
        ('GRID', (0, 0), (-1, -1), 0.5, C_BORDER),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('TOPPADDING', (0, 0), (-1, -1), 6),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
        ('LEFTPADDING', (0, 0), (-1, -1), 6),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, C_LIGHT_BG]),
    ]))
    elements.append(ft)

    elements.append(Spacer(1, 24))

    elements.append(Paragraph('Implementation Priority', S['SubHead']))
    priority_table = [
        ['Priority', 'Recommendation', 'Fixes Bugs', 'Effort'],
        ['1 (Do First)', 'REC-001: update() in sync task', 'BUG-001, BUG-007', 'Low (1-2 hours)'],
        ['2', 'REC-002: Update local state after recall', 'BUG-002, BUG-003', 'Low (30 min)'],
        ['3', 'REC-006: Validation guard in check task', 'BUG-001 (defense)', 'Low (30 min)'],
        ['4', 'REC-003: Fix laid->Open status mapping', 'BUG-004', 'Low (15 min)'],
        ['5', 'REC-004: State transition for individual recall', 'BUG-005', 'Medium (2-3 hours)'],
        ['6', 'REC-005: Use recall-bale API', 'BUG-006', 'Low (30 min)'],
    ]
    pt = Table(priority_table, colWidths=[0.9 * inch, 2.2 * inch, 1.3 * inch, 1.2 * inch])
    pt.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), C_PRIMARY),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 8.5),
        ('FONTNAME', (0, 1), (-1, -1), 'Helvetica'),
        ('GRID', (0, 0), (-1, -1), 0.5, C_BORDER),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('TOPPADDING', (0, 0), (-1, -1), 6),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
        ('LEFTPADDING', (0, 0), (-1, -1), 6),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, C_LIGHT_BG]),
    ]))
    elements.append(pt)

    # Footer
    elements.append(Spacer(1, 30))
    elements.append(HRFlowable(width='100%', thickness=1, color=C_BORDER, spaceAfter=8))
    elements.append(Paragraph(
        f'Generated on {datetime.now().strftime("%B %d, %Y at %I:%M %p")} | '
        'IntelliScale CTL Workflow Analysis | Claude Code', S['SmallNote']))

    # Build
    doc.build(elements)
    print(f'PDF generated: {output_path}')


if __name__ == '__main__':
    output = '/Users/marimo/Dev/Odoo18/intelliscale/ctl_workflow_analysis.pdf'
    build_pdf(output)
