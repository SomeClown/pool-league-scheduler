"""
Excel export for pool league seasons.
Produces a three-sheet workbook: Schedule, Teams, Bars.
"""

from io import BytesIO
from openpyxl import Workbook
from openpyxl.styles import PatternFill, Font, Border, Side, Alignment
from openpyxl.utils import get_column_letter


# ---------------------------------------------------------------------------
# Colour palette
# ---------------------------------------------------------------------------

# Backgrounds
BG_TITLE      = "0A2540"   # very dark navy — sheet title bar
BG_SUBTITLE   = "D6E8F7"   # pale blue — info band
BG_HDR_MAIN   = "1B3A5C"   # dark navy — Wk / Date / Bar headers
BG_HDR_HOME   = "7B5200"   # dark amber — Home # / Home Team headers
BG_HDR_AWAY   = "1A4A7A"   # dark steel blue — Away # / Away Team headers
BG_HDR_BAR    = "2D4A2D"   # dark forest green — Bar header
BG_ROW_ODD    = "EAF4FC"   # light sky blue — odd weeks
BG_ROW_EVEN   = "FFFFFF"   # white — even weeks
BG_BYE        = "EBEBEB"   # light grey — bye rows
BG_TEAMS_HDR  = "1B3A5C"   # same navy for Teams / Bars sheets

# Text
FG_WHITE  = "FFFFFF"
FG_DARK   = "111111"
FG_MUTED  = "555555"


# ---------------------------------------------------------------------------
# Style helpers
# ---------------------------------------------------------------------------

def _fill(hex_color):
    return PatternFill(fill_type="solid", fgColor=hex_color)


def _font(bold=False, italic=False, color=FG_DARK, size=11):
    return Font(bold=bold, italic=italic, color=color, size=size)


def _align(h="left", v="center", wrap=False):
    return Alignment(horizontal=h, vertical=v, wrap_text=wrap)


_THICK = Side(border_style="medium", color="222222")
_THIN  = Side(border_style="thin",   color="BBBBBB")
_NONE  = Side(border_style=None)


def _border(top=_NONE, right=_NONE, bottom=_NONE, left=_NONE):
    return Border(top=top, right=right, bottom=bottom, left=left)


def _outline_range(ws, r1, r2, c1, c2):
    """
    Apply a bold (medium) outer border with a thin interior grid
    to a rectangular range of cells.
    """
    for row in range(r1, r2 + 1):
        for col in range(c1, c2 + 1):
            top    = _THICK if row == r1 else _THIN
            bottom = _THICK if row == r2 else _THIN
            left   = _THICK if col == c1 else _THIN
            right  = _THICK if col == c2 else _THIN
            ws.cell(row=row, column=col).border = _border(top, right, bottom, left)


# ---------------------------------------------------------------------------
# Sheet 1 — Schedule
# ---------------------------------------------------------------------------
# Columns:  A=Wk  B=Date  C=Home#  D=Home Team  E=Away#  F=Away Team  G=Bar
# ---------------------------------------------------------------------------

_SCHED_COLS = 7


def _sched_header_cell(ws, row, col, value, bg, align_h="left"):
    c = ws.cell(row=row, column=col, value=value)
    c.fill  = _fill(bg)
    c.font  = _font(bold=True, color=FG_WHITE, size=10)
    c.alignment = _align(h=align_h)
    c.border = _border(_THICK, _THICK, _THICK, _THICK)
    return c


def _build_schedule_sheet(ws, season, rounds):
    # ── Row 1 — season title ──────────────────────────────────────────────
    ws.merge_cells(f"A1:{get_column_letter(_SCHED_COLS)}1")
    c = ws["A1"]
    c.value     = season.name.upper()
    c.fill      = _fill(BG_TITLE)
    c.font      = _font(bold=True, color=FG_WHITE, size=16)
    c.alignment = _align(h="center")
    ws.row_dimensions[1].height = 32

    # ── Row 2 — subtitle band ─────────────────────────────────────────────
    ws.merge_cells(f"A2:{get_column_letter(_SCHED_COLS)}2")
    start = season.start_date.strftime("%B %-d, %Y")
    end   = (f" – {season.end_date.strftime('%B %-d, %Y')}"
             if season.end_date else "")
    sub = (f"{start}{end}   ·   {season.frequency.title()}   ·   "
           f"{len(season.teams)} teams   ·   {len(rounds)} rounds")
    c = ws["A2"]
    c.value     = sub
    c.fill      = _fill(BG_SUBTITLE)
    c.font      = _font(color=FG_DARK, size=10)
    c.alignment = _align(h="center")
    ws.row_dimensions[2].height = 18

    # ── Row 3 — column headers ────────────────────────────────────────────
    hdr_defs = [
        ("Wk",        BG_HDR_MAIN, "center"),
        ("Date",      BG_HDR_MAIN, "center"),
        ("Home #",    BG_HDR_HOME, "center"),
        ("Home Team", BG_HDR_HOME, "left"),
        ("Away #",    BG_HDR_AWAY, "center"),
        ("Away Team", BG_HDR_AWAY, "left"),
        ("Bar",       BG_HDR_BAR,  "left"),
    ]
    for col, (label, bg, align_h) in enumerate(hdr_defs, start=1):
        _sched_header_cell(ws, 3, col, label, bg, align_h)
    ws.row_dimensions[3].height = 20

    # ── Data rows ─────────────────────────────────────────────────────────
    cur_row = 4
    for week_idx, (round_num, round_data) in enumerate(rounds.items()):
        row_fill = _fill(BG_ROW_ODD if week_idx % 2 == 0 else BG_ROW_EVEN)
        group_start = cur_row

        for match_idx, match in enumerate(round_data["matches"]):
            first = match_idx == 0
            home  = match.home_team
            away  = match.away_team

            def _rc(col, value, bold=False, italic=False,
                    color=FG_DARK, size=11, align_h="left"):
                cell = ws.cell(row=cur_row, column=col, value=value)
                cell.fill      = row_fill
                cell.font      = _font(bold=bold, italic=italic,
                                       color=color, size=size)
                cell.alignment = _align(h=align_h)
                return cell

            _rc(1, round_num if first else None,
                bold=True, size=10, align_h="center")
            _rc(2, round_data["date"].strftime("%b %-d") if first else None,
                size=10, align_h="center")
            _rc(3, home.number if home and home.number is not None else "",
                bold=True, size=12, align_h="center")
            _rc(4, home.name   if home else "", size=11)
            _rc(5, away.number if away and away.number is not None else "",
                size=12, align_h="center")
            _rc(6, away.name   if away else "", size=11)
            _rc(7, match.bar.name if match.bar else "",
                italic=True, color=FG_MUTED, size=10)

            ws.row_dimensions[cur_row].height = 18
            cur_row += 1

        # Bye row
        if round_data.get("bye") and round_data["bye"].team:
            team = round_data["bye"].team
            bye_fill = _fill(BG_BYE)

            for col in range(1, _SCHED_COLS + 1):
                c = ws.cell(row=cur_row, column=col, value=None)
                c.fill = bye_fill

            ws.cell(row=cur_row, column=3, value="Bye:").font = _font(
                italic=True, color=FG_MUTED, size=10)
            ws.cell(row=cur_row, column=3).alignment = _align(h="right")
            ws.cell(row=cur_row, column=3).fill = bye_fill

            num_val = team.number if team.number is not None else ""
            ws.cell(row=cur_row, column=4, value=num_val).font = _font(
                bold=True, italic=True, color=FG_MUTED, size=10)
            ws.cell(row=cur_row, column=4).alignment = _align(h="center")
            ws.cell(row=cur_row, column=4).fill = bye_fill

            ws.cell(row=cur_row, column=5, value=team.name).font = _font(
                italic=True, color=FG_MUTED, size=10)
            ws.cell(row=cur_row, column=5).alignment = _align(h="left")
            ws.cell(row=cur_row, column=5).fill = bye_fill

            ws.row_dimensions[cur_row].height = 15
            cur_row += 1

        _outline_range(ws, group_start, cur_row - 1, 1, _SCHED_COLS)

    # ── Column widths ─────────────────────────────────────────────────────
    for col, width in enumerate([5, 10, 7, 24, 7, 24, 22], start=1):
        ws.column_dimensions[get_column_letter(col)].width = width

    ws.freeze_panes = "A4"


# ---------------------------------------------------------------------------
# Sheet 2 — Teams
# ---------------------------------------------------------------------------

def _build_teams_sheet(ws, season):
    sorted_teams = sorted(
        season.teams,
        key=lambda t: (t.number is None, t.number or 0, t.name)
    )
    NUM_COLS = 3

    # Title
    ws.merge_cells(f"A1:{get_column_letter(NUM_COLS)}1")
    c = ws["A1"]
    c.value     = "TEAMS"
    c.fill      = _fill(BG_TITLE)
    c.font      = _font(bold=True, color=FG_WHITE, size=13)
    c.alignment = _align(h="center")
    ws.row_dimensions[1].height = 26

    # Column headers
    for col, (label, align_h) in enumerate(
        [("#", "center"), ("Team Name", "left"), ("Home Bar", "left")],
        start=1
    ):
        c = ws.cell(row=2, column=col, value=label)
        c.fill      = _fill(BG_TEAMS_HDR)
        c.font      = _font(bold=True, color=FG_WHITE, size=10)
        c.alignment = _align(h=align_h)
        c.border    = _border(_THICK, _THICK, _THICK, _THICK)
    ws.row_dimensions[2].height = 18

    # Data
    for i, team in enumerate(sorted_teams):
        row  = i + 3
        fill = _fill(BG_ROW_ODD if i % 2 == 0 else BG_ROW_EVEN)

        c = ws.cell(row=row, column=1,
                    value=team.number if team.number is not None else "—")
        c.fill = fill
        c.font = _font(bold=True, size=12)
        c.alignment = _align(h="center")

        c = ws.cell(row=row, column=2, value=team.name)
        c.fill = fill
        c.font = _font(size=11)
        c.alignment = _align(h="left")

        c = ws.cell(row=row, column=3,
                    value=team.bar.name if team.bar else "")
        c.fill = fill
        c.font = _font(italic=True, color=FG_MUTED, size=10)
        c.alignment = _align(h="left")

        ws.row_dimensions[row].height = 18

    if sorted_teams:
        _outline_range(ws, 3, 2 + len(sorted_teams), 1, NUM_COLS)

    ws.column_dimensions["A"].width = 6
    ws.column_dimensions["B"].width = 26
    ws.column_dimensions["C"].width = 22
    ws.freeze_panes = "A3"


# ---------------------------------------------------------------------------
# Sheet 3 — Bars
# ---------------------------------------------------------------------------

def _build_bars_sheet(ws, season):
    bars = sorted(
        {team.bar for team in season.teams if team.bar},
        key=lambda b: b.name
    )
    NUM_COLS = 2

    # Title
    ws.merge_cells("A1:B1")
    c = ws["A1"]
    c.value     = "BARS / VENUES"
    c.fill      = _fill(BG_TITLE)
    c.font      = _font(bold=True, color=FG_WHITE, size=13)
    c.alignment = _align(h="center")
    ws.row_dimensions[1].height = 26

    # Column headers
    for col, (label, align_h) in enumerate(
        [("Bar / Venue", "left"), ("Tables", "center")], start=1
    ):
        c = ws.cell(row=2, column=col, value=label)
        c.fill      = _fill(BG_TEAMS_HDR)
        c.font      = _font(bold=True, color=FG_WHITE, size=10)
        c.alignment = _align(h=align_h)
        c.border    = _border(_THICK, _THICK, _THICK, _THICK)
    ws.row_dimensions[2].height = 18

    # Data
    for i, bar in enumerate(bars):
        row  = i + 3
        fill = _fill(BG_ROW_ODD if i % 2 == 0 else BG_ROW_EVEN)

        c = ws.cell(row=row, column=1, value=bar.name)
        c.fill = fill
        c.font = _font(size=11)
        c.alignment = _align(h="left")

        c = ws.cell(row=row, column=2, value=bar.tables)
        c.fill = fill
        c.font = _font(size=11)
        c.alignment = _align(h="center")

        ws.row_dimensions[row].height = 18

    if bars:
        _outline_range(ws, 3, 2 + len(bars), 1, NUM_COLS)

    ws.column_dimensions["A"].width = 26
    ws.column_dimensions["B"].width = 10
    ws.freeze_panes = "A3"


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def build_season_excel(season, rounds):
    """Return a BytesIO containing the formatted .xlsx workbook."""
    wb = Workbook()

    ws_sched = wb.active
    ws_sched.title = "Schedule"
    _build_schedule_sheet(ws_sched, season, rounds)

    ws_teams = wb.create_sheet("Teams")
    _build_teams_sheet(ws_teams, season)

    ws_bars = wb.create_sheet("Bars")
    _build_bars_sheet(ws_bars, season)

    output = BytesIO()
    wb.save(output)
    output.seek(0)
    return output
