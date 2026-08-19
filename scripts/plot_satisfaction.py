#!/usr/bin/env python3
"""Plot per-participant satisfaction change over time, faceted by form section.

Reads the exported form answers CSV and draws one panel per main form section
("Movement & Mobility", "Energy & Fatigue", "Strength & Muscle Endurance",
"Mood & Well-being"). Each panel is split into the assessment points (day 14,
28, 42, 56) and every individual submission is drawn as one dot, coloured and
shaped per participant.

The vertical scale is the questionnaire's change scale, with a reference line
at 0 ("no change"):

    +2 much better / +1 slightly better / 0 no change / -1 slightly worse / -2 much worse

A section with several questions is summarised as the mean of its questions for
that submission, so a section dot can land between the integer steps.

Two renderers share the same data and layout:

  * a static image (PNG/SVG/PDF) via matplotlib, for reports and slides;
  * a self-contained, accessible HTML page with hover/focus tooltips, a
    per-participant filter, light & dark themes and a full data table.

Usage:
    python scripts/plot_satisfaction.py                     # image + HTML page
    python scripts/plot_satisfaction.py --format html
    python scripts/plot_satisfaction.py --days 7 14 21 28 --theme dark --table
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import html
import math
import re
import sys
from collections import defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CSV = REPO_ROOT / "form_answers" / "juvionhs01_satisfaction_form_answers.csv"
DEFAULT_OUT = REPO_ROOT / "form_answers" / "plots" / "satisfaction_by_section.png"
DEFAULT_OUT_HTML = REPO_ROOT / "form_answers" / "plots" / "satisfaction_by_section.html"

# Main form sections -> the CSV columns that make them up (see index.html
# SCALE_SECTIONS). Sections 3 (sleep) and 5 (coordination) are intentionally
# not plotted here.
SECTIONS: list[tuple[str, list[str]]] = [
    ("Movement & Mobility", ["section1_q0", "section1_q1", "section1_q2"]),
    ("Energy & Fatigue", ["section2_q0"]),
    ("Strength & Muscle Endurance", ["section4_q0", "section4_q1"]),
    ("Mood & Well-being", ["section6_q0", "section6_q1"]),
]

DEFAULT_DAYS = [14, 28, 42, 56]

SCALE_LABELS = {
    2: "+2  much better",
    1: "+1  slightly better",
    0: "0  no change",
    -1: "-1  slightly worse",
    -2: "-2  much worse",
}

SCALE_WORDS = {
    2: "much better",
    1: "slightly better",
    0: "no change",
    -1: "slightly worse",
    -2: "much worse",
}

# Categorical hues in fixed slot order, light and dark steps of the same eight
# hues. Colour follows the participant, never their rank, so a participant keeps
# their hue whichever assessment points they happen to have answered.
CATEGORICAL = {
    "light": ["#2a78d6", "#eb6834", "#1baf7a", "#eda100",
              "#e87ba4", "#008300", "#4a3aa7", "#e34948"],
    "dark": ["#3987e5", "#d95926", "#199e70", "#c98500",
             "#d55181", "#008300", "#9085e9", "#e66767"],
}

# Beyond a handful of series no eight-hue set separates reliably for every
# viewer once any pair can end up side by side, so identity is carried by marker
# shape and by a stable horizontal slot as well as by colour.
MARKERS = ["o", "s", "^", "D", "v", "P", "X", "*", "<", ">", "h", "p"]

# The same shape order, named for the SVG renderer.
SHAPES = ["circle", "square", "triangle", "diamond", "triangle-down", "plus",
          "x", "star", "triangle-left", "triangle-right", "hexagon", "pentagon"]

THEMES = {
    "light": {
        "surface": "#fcfcfb",
        "text_primary": "#0b0b0b",
        "text_secondary": "#52514e",
        "text_muted": "#78766f",
        "grid": "#e4e3df",
        "zero": "#a9a7a0",
        "panel_edge": "#eceae6",
    },
    "dark": {
        "surface": "#1a1a19",
        "text_primary": "#ffffff",
        "text_secondary": "#c3c2b7",
        "text_muted": "#8f8e85",
        "grid": "#2f2f2c",
        "zero": "#5e5d57",
        "panel_edge": "#262625",
    },
}

# Horizontal share of a day group used to spread the participant slots.
SLOT_SPAN = 0.82


def participant_sort_key(pid: str):
    """Natural sort: P2 before P12, non-numeric ids last but stable."""
    match = re.fullmatch(r"[A-Za-z]*?(\d+)", pid)
    if match:
        return (0, int(match.group(1)), pid)
    return (1, 0, pid)


def parse_day(assessment_point: str) -> int | None:
    """'D14' -> 14, 'J0' -> 0. Anything unrecognised -> None."""
    match = re.fullmatch(r"[A-Za-z](\d+)", (assessment_point or "").strip())
    return int(match.group(1)) if match else None


def section_score(row: dict, columns: list[str]) -> tuple[float, int] | None:
    """Mean of the answered questions in a section, plus how many were answered."""
    values = []
    for column in columns:
        raw = (row.get(column) or "").strip()
        if raw == "":
            continue
        try:
            values.append(float(raw))
        except ValueError:
            continue
    if not values:
        return None
    return sum(values) / len(values), len(values)


def score_text(score: float, answered: int) -> str:
    """'+1 slightly better' for a plain answer, '+1.67 (mean of 3)' for a mean."""
    if abs(score - round(score)) < 1e-9:
        step = int(round(score))
        return f"{step:+d} {SCALE_WORDS[step]}" if step else f"0 {SCALE_WORDS[0]}"
    return f"{score:+.2f} (mean of {answered} answers)"


def load_points(csv_path: Path, days: list[int]) -> tuple[list[dict], list[str]]:
    """Return (points, participants) for the requested assessment days."""
    with csv_path.open(newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))

    if rows:
        missing = {c for _, cols in SECTIONS for c in cols} - set(rows[0])
        if missing:
            sys.exit(f"CSV is missing expected columns: {', '.join(sorted(missing))}")

    wanted = set(days)
    points: list[dict] = []
    participants: set[str] = set()

    for row in rows:
        day = parse_day(row.get("assessment_point", ""))
        if day is None or day not in wanted:
            continue
        pid = (row.get("participant_id") or "").strip()
        if not pid:
            continue
        participants.add(pid)
        for section_name, columns in SECTIONS:
            scored = section_score(row, columns)
            if scored is None:
                continue
            score, answered = scored
            points.append({
                "participant": pid,
                "day": day,
                "section": section_name,
                "score": score,
                "answered": answered,
                "date": (row.get("date") or "").strip(),
            })

    return points, sorted(participants, key=participant_sort_key)


def index_points(points: list[dict]) -> dict[tuple[str, int, str], dict]:
    """(participant, day, section) -> point, for table lookups."""
    return {(p["participant"], p["day"], p["section"]): p for p in points}


def print_table(points: list[dict], days: list[int], participants: list[str]) -> None:
    """Text equivalent of the chart, so the values are readable without colour."""
    by_key = index_points(points)
    width = max([len("participant")] + [len(p) for p in participants]) + 2
    for section_name, _ in SECTIONS:
        print(f"\n{section_name}")
        header = "participant".ljust(width) + "".join(f"D{d:<7}" for d in days)
        print(header)
        print("-" * len(header))
        for pid in participants:
            cells = ""
            has_value = False
            for day in days:
                point = by_key.get((pid, day, section_name))
                if point is None:
                    cells += "--".ljust(8)
                else:
                    cells += f"{point['score']:+.2f}".ljust(8)
                    has_value = True
            if has_value:
                print(pid.ljust(width) + cells)


def assign_series(participants: list[str], theme_name: str) -> dict[str, dict]:
    """Colour, marker and horizontal slot per participant, in fixed slot order."""
    hues = CATEGORICAL[theme_name]
    count = max(len(participants), 1)
    return {
        pid: {
            "color": hues[index % len(hues)],
            "css_color": f"var({participant_var(index)})",
            "marker": MARKERS[index % len(MARKERS)],
            "shape": SHAPES[index % len(SHAPES)],
            "slot": (index - (count - 1) / 2) * (SLOT_SPAN / count),
        }
        for index, pid in enumerate(participants)
    }


# ---------------------------------------------------------------- static image

def build_figure(points: list[dict], days: list[int], participants: list[str],
                 theme_name: str):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.lines import Line2D

    theme = THEMES[theme_name]
    series = assign_series(participants, theme_name)

    # Keep the marks from overlapping when many participants share a day group.
    # ~61pt of drawing width per group at the default figure size.
    spacing_pt = (SLOT_SPAN / max(len(participants), 1)) * 61.0
    diameter = max(5.0, min(9.0, spacing_pt - 1.0))
    area = diameter ** 2

    by_panel: dict[tuple[str, int], list[dict]] = defaultdict(list)
    for point in points:
        by_panel[(point["section"], point["day"])].append(point)

    fig, axes = plt.subplots(
        1, len(SECTIONS), figsize=(4.1 * len(SECTIONS), 6.4), sharey=True,
    )
    fig.patch.set_facecolor(theme["surface"])
    if len(SECTIONS) == 1:
        axes = [axes]

    for ax, (section_name, _) in zip(axes, SECTIONS):
        ax.set_facecolor(theme["surface"])
        ax.set_xlim(-0.62, len(days) - 0.38)
        ax.set_ylim(-2.45, 2.45)

        ax.set_axisbelow(True)
        ax.yaxis.grid(True, color=theme["grid"], linewidth=1, zorder=0)
        ax.xaxis.grid(False)
        ax.axhline(0, color=theme["zero"], linewidth=1.6, zorder=1)

        # Faint separators between the day sub-sections.
        for index in range(1, len(days)):
            ax.axvline(index - 0.5, color=theme["panel_edge"], linewidth=1, zorder=0)

        for spine in ("top", "right", "left"):
            ax.spines[spine].set_visible(False)
        ax.spines["bottom"].set_color(theme["grid"])

        ax.set_xticks(range(len(days)))
        ax.set_xticklabels([f"Day {d}" for d in days])
        ax.tick_params(axis="x", colors=theme["text_secondary"], length=0, pad=8)
        ax.tick_params(axis="y", colors=theme["text_secondary"], length=0, pad=6)
        ax.set_title(section_name, color=theme["text_primary"], fontsize=12,
                     fontweight="bold", pad=14)

        for index, day in enumerate(days):
            group = by_panel.get((section_name, day), [])
            if not group:
                ax.text(index, -2.25, "no data yet", ha="center", va="center",
                        fontsize=8.5, style="italic",
                        color=theme["text_muted"], zorder=4)
                continue
            for point in group:
                style = series[point["participant"]]
                ax.scatter(
                    index + style["slot"], point["score"],
                    s=area * 1.7 if style["marker"] == "*" else area,
                    marker=style["marker"],
                    color=style["color"],
                    edgecolors=theme["surface"],   # surface ring on overlap
                    linewidths=1.6,
                    zorder=3,
                )

    axes[0].set_yticks(sorted(SCALE_LABELS))
    axes[0].set_yticklabels([SCALE_LABELS[value] for value in sorted(SCALE_LABELS)])

    handles = [
        Line2D([], [], linestyle="none", marker=series[pid]["marker"], markersize=8,
               markerfacecolor=series[pid]["color"], markeredgecolor=theme["surface"],
               markeredgewidth=1.2, label=pid)
        for pid in participants
    ]
    legend = fig.legend(
        handles=handles, loc="lower center", ncol=min(len(participants), 9),
        frameon=False, title="Participant",
        bbox_to_anchor=(0.5, 0.005), handletextpad=0.4, columnspacing=1.6,
    )
    for text in legend.get_texts():
        text.set_color(theme["text_secondary"])
    legend.get_title().set_color(theme["text_muted"])
    legend.get_title().set_fontsize(9)

    fig.suptitle("Change vs. before JuvionHS-001, by form section",
                 color=theme["text_primary"], fontsize=15, fontweight="bold",
                 x=0.5, y=0.985)
    fig.text(0.5, 0.937,
             "One dot per participant per assessment point. "
             "Sections with several questions are shown as their mean.",
             ha="center", color=theme["text_secondary"], fontsize=10)

    fig.tight_layout(rect=(0.005, 0.07, 0.995, 0.915))
    return fig


# ------------------------------------------------------------------ HTML page

# Panel geometry, in SVG user units (the panels scale to their container).
# A panel is drawn at one of two widths: the narrow one for the four-across
# layout, the wide one when few columns carry many participants. Drawing wider
# rather than scaling up keeps the type and the marks at their intended size.
VB_W, VB_W_WIDE, VB_H = 330.0, 660.0, 296.0
PAD_L, PAD_R, PAD_T, PAD_B = 34.0, 14.0, 12.0, 34.0
PLOT_H = VB_H - PAD_T - PAD_B


def _y(value: float) -> float:
    return PAD_T + (2.0 - value) / 4.0 * PLOT_H


def marker_path(shape: str, cx: float, cy: float, r: float) -> str:
    """SVG geometry for one marker, as an element without presentation attrs."""
    def polygon(points: list[tuple[float, float]]) -> str:
        coords = " ".join(f"{x:.2f},{y:.2f}" for x, y in points)
        return f'<polygon points="{coords}"'

    def star_points(spikes: int, outer: float, inner: float,
                    rotation: float) -> list[tuple[float, float]]:
        result = []
        for step in range(spikes * 2):
            radius = outer if step % 2 == 0 else inner
            angle = rotation + step * math.pi / spikes
            result.append((cx + radius * math.sin(angle),
                           cy - radius * math.cos(angle)))
        return result

    def regular(sides: int, rotation: float) -> list[tuple[float, float]]:
        return [(cx + r * math.sin(rotation + i * 2 * math.pi / sides),
                 cy - r * math.cos(rotation + i * 2 * math.pi / sides))
                for i in range(sides)]

    def cross(rotation: float) -> list[tuple[float, float]]:
        arm, thick = r * 1.15, r * 0.42
        base = [(-thick, -arm), (thick, -arm), (thick, -thick), (arm, -thick),
                (arm, thick), (thick, thick), (thick, arm), (-thick, arm),
                (-thick, thick), (-arm, thick), (-arm, -thick), (-thick, -thick)]
        cos, sin = math.cos(rotation), math.sin(rotation)
        return [(cx + x * cos - y * sin, cy + x * sin + y * cos) for x, y in base]

    if shape == "circle":
        return f'<circle cx="{cx:.2f}" cy="{cy:.2f}" r="{r:.2f}"'
    if shape == "square":
        side = r * 1.8
        return (f'<rect x="{cx - side / 2:.2f}" y="{cy - side / 2:.2f}" '
                f'width="{side:.2f}" height="{side:.2f}" rx="1.2"')
    if shape == "triangle":
        return polygon(regular(3, 0))
    if shape == "triangle-down":
        return polygon(regular(3, math.pi))
    if shape == "triangle-left":
        return polygon(regular(3, -math.pi / 2))
    if shape == "triangle-right":
        return polygon(regular(3, math.pi / 2))
    if shape == "diamond":
        return polygon(regular(4, 0))
    if shape == "pentagon":
        return polygon(regular(5, 0))
    if shape == "hexagon":
        return polygon(regular(6, 0))
    if shape == "plus":
        return polygon(cross(0))
    if shape == "x":
        return polygon(cross(math.pi / 4))
    if shape == "star":
        return polygon(star_points(5, r * 1.25, r * 0.55, 0))
    return f'<circle cx="{cx:.2f}" cy="{cy:.2f}" r="{r:.2f}"'


def participant_var(index: int) -> str:
    """CSS custom property holding one participant's hue in the active theme."""
    return f"--participant-{index}"


def participant_color_css(participants: list[str]) -> str:
    """Light and dark steps of each participant's hue, under all three scopes.

    The dark column is the same hues re-stepped for the dark surface, so a mark
    keeps its identity when the page switches theme instead of being flipped.
    """
    def declarations(theme_name: str, indent: str = "  ") -> str:
        hues = CATEGORICAL[theme_name]
        return "\n".join(
            f"{indent}{participant_var(index)}: {hues[index % len(hues)]};"
            for index in range(len(participants))
        )

    light = declarations("light")
    dark = declarations("dark")
    dark_nested = declarations("dark", "    ")
    return "\n".join([
        ":root {", light, "}",
        '@media (prefers-color-scheme: dark) {',
        '  :root:not([data-theme="light"]) {', dark_nested, "  }", "}",
        ':root[data-theme="dark"] {', dark, "}",
    ])


def legend_swatch(shape: str, color: str) -> str:
    """A 16x16 inline SVG of one participant's mark, for legend chips and table."""
    body = marker_path(shape, 8, 8, 5)
    return (f'<svg class="swatch" viewBox="0 0 16 16" width="16" height="16" '
            f'aria-hidden="true" focusable="false">{body} fill="{color}" '
            f'stroke="var(--surface-1)" stroke-width="1.2"/></svg>')


def render_panel_svg(section_name: str, days: list[int], series: dict[str, dict],
                     by_panel: dict[tuple[str, int], list[dict]],
                     participant_count: int, vb_w: float = VB_W) -> str:
    plot_w = vb_w - PAD_L - PAD_R
    group_width = plot_w / len(days)
    spacing = (SLOT_SPAN / max(participant_count, 1)) * group_width
    # Never shrink a mark below readability: past that point the slots overlap
    # slightly and the surface ring plus the shape difference keep them apart.
    radius = max(3.5, min(5.0, spacing / 2 - 0.4))

    parts: list[str] = []

    # Recessive grid, then the emphasised zero reference line.
    for value in (2, 1, -1, -2):
        parts.append(
            f'<line class="grid" x1="{PAD_L:.1f}" x2="{PAD_L + plot_w:.1f}" '
            f'y1="{_y(value):.2f}" y2="{_y(value):.2f}"/>'
        )
    for index in range(1, len(days)):
        x = PAD_L + index * group_width
        parts.append(f'<line class="divider" x1="{x:.2f}" x2="{x:.2f}" '
                     f'y1="{PAD_T:.1f}" y2="{PAD_T + PLOT_H:.1f}"/>')
    parts.append(
        f'<line class="zero" x1="{PAD_L:.1f}" x2="{PAD_L + plot_w:.1f}" '
        f'y1="{_y(0):.2f}" y2="{_y(0):.2f}"/>'
    )

    for value in (2, 1, 0, -1, -2):
        label = {2: "+2", 1: "+1", 0: "0", -1: "−1", -2: "−2"}[value]
        parts.append(
            f'<text class="y-tick" x="{PAD_L - 8:.1f}" y="{_y(value) + 3.4:.2f}" '
            f'text-anchor="end">{label}</text>'
        )

    for index, day in enumerate(days):
        cx = PAD_L + (index + 0.5) * group_width
        parts.append(
            f'<text class="x-tick" x="{cx:.2f}" y="{VB_H - 14:.1f}" '
            f'text-anchor="middle">Day {day}</text>'
        )
        group = by_panel.get((section_name, day), [])
        if not group:
            parts.append(
                f'<text class="empty" x="{cx:.2f}" y="{_y(-2) - 4:.2f}" '
                f'text-anchor="middle">no data yet</text>'
            )
            continue
        for point in sorted(group, key=lambda p: p["participant"]):
            pid = point["participant"]
            style = series[pid]
            x = cx + style["slot"] * group_width
            y = _y(point["score"])
            label = (f'{pid} · {section_name} · Day {day}: '
                     f'{score_text(point["score"], point["answered"])}')
            body = marker_path(style["shape"], x, y, radius)
            parts.append(
                f'{body} class="dot" fill="{style["css_color"]}" '
                f'data-participant="{html.escape(pid, quote=True)}" '
                f'data-tip="{html.escape(label, quote=True)}" '
                f'tabindex="0" role="img" '
                f'aria-label="{html.escape(label, quote=True)}"/>'
            )

    return (
        f'<svg class="panel-svg" viewBox="0 0 {vb_w:.0f} {VB_H:.0f}" '
        f'preserveAspectRatio="xMidYMid meet" role="group" '
        f'aria-label="{html.escape(section_name, quote=True)} plot">'
        + "".join(parts) + "</svg>"
    )


def render_tables(points: list[dict], days: list[int], participants: list[str],
                  series: dict[str, dict]) -> str:
    by_key = index_points(points)
    blocks: list[str] = []
    for section_name, columns in SECTIONS:
        rows: list[str] = []
        for pid in participants:
            cells = []
            has_value = False
            for day in days:
                point = by_key.get((pid, day, section_name))
                if point is None:
                    cells.append('<td class="na"><span aria-hidden="true">–</span>'
                                 '<span class="sr-only">no answer</span></td>')
                else:
                    has_value = True
                    cells.append(
                        f'<td>{html.escape(score_text(point["score"], point["answered"]))}</td>'
                    )
            if has_value:
                swatch = legend_swatch(series[pid]["shape"], series[pid]["css_color"])
                rows.append(
                    f'<tr data-participant="{html.escape(pid, quote=True)}">'
                    f'<th scope="row">{swatch}{html.escape(pid)}</th>'
                    + "".join(cells) + "</tr>"
                )
        headers = "".join(f'<th scope="col">Day {day}</th>' for day in days)
        question_note = (f"mean of {len(columns)} questions" if len(columns) > 1
                         else "1 question")
        blocks.append(
            '<figure class="table-block">'
            f'<figcaption>{html.escape(section_name)} '
            f'<span class="note">({question_note})</span></figcaption>'
            '<div class="table-scroll"><table>'
            f'<thead><tr><th scope="col">Participant</th>{headers}</tr></thead>'
            f'<tbody>{"".join(rows)}</tbody></table></div></figure>'
        )
    return "".join(blocks)


HTML_STYLE = """
:root {
  color-scheme: light;
  --surface-1: #fcfcfb;
  --surface-2: #f4f3f0;
  --text-primary: #0b0b0b;
  --text-secondary: #52514e;
  --text-muted: #78766f;
  --grid: #e4e3df;
  --zero: #a9a7a0;
  --divider: #eceae6;
  --focus: #2a78d6;
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    color-scheme: dark;
    --surface-1: #1a1a19;
    --surface-2: #242422;
    --text-primary: #ffffff;
    --text-secondary: #c3c2b7;
    --text-muted: #8f8e85;
    --grid: #2f2f2c;
    --zero: #5e5d57;
    --divider: #262625;
    --focus: #3987e5;
  }
}
:root[data-theme="dark"] {
  color-scheme: dark;
  --surface-1: #1a1a19;
  --surface-2: #242422;
  --text-primary: #ffffff;
  --text-secondary: #c3c2b7;
  --text-muted: #8f8e85;
  --grid: #2f2f2c;
  --zero: #5e5d57;
  --divider: #262625;
  --focus: #3987e5;
}

* { box-sizing: border-box; }
body {
  margin: 0;
  padding: 32px 24px 56px;
  background: var(--surface-1);
  color: var(--text-primary);
  font: 15px/1.5 ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto,
        "Helvetica Neue", Arial, sans-serif;
  -webkit-text-size-adjust: 100%;
}
.wrap { max-width: 1240px; margin: 0 auto; }

.skip {
  position: absolute; left: -9999px; top: 0;
  background: var(--surface-2); color: var(--text-primary);
  padding: 10px 14px; border-radius: 8px; z-index: 10;
}
.skip:focus { left: 16px; top: 16px; }
.sr-only {
  position: absolute; width: 1px; height: 1px; padding: 0; margin: -1px;
  overflow: hidden; clip: rect(0 0 0 0); white-space: nowrap; border: 0;
}

h1 { font-size: 22px; line-height: 1.25; margin: 0 0 6px; font-weight: 650; }
.sub { margin: 0 0 4px; color: var(--text-secondary); max-width: 70ch; }
.meta { margin: 0; color: var(--text-muted); font-size: 13px; }
h2 { font-size: 16px; margin: 40px 0 12px; font-weight: 650; }

.controls {
  display: flex; flex-wrap: wrap; gap: 12px; align-items: center;
  justify-content: space-between; margin: 22px 0 10px;
}
.legend { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; }
.legend-label { color: var(--text-muted); font-size: 13px; margin-right: 2px; }
.chip {
  display: inline-flex; align-items: center; gap: 6px;
  padding: 5px 11px 5px 8px; border-radius: 999px;
  border: 1px solid var(--grid); background: var(--surface-1);
  color: var(--text-secondary); font: inherit; font-size: 13px; cursor: pointer;
}
.chip:hover { background: var(--surface-2); }
.chip[aria-pressed="false"] { opacity: 0.45; }
.chip.plain { padding: 5px 11px; }
:focus-visible { outline: 2px solid var(--focus); outline-offset: 2px; }

.panels {
  display: grid; gap: 20px 16px;
  grid-template-columns: repeat(4, minmax(0, 1fr));
}
.panels.dense { grid-template-columns: repeat(2, minmax(0, 1fr)); }
@media (max-width: 1080px) { .panels { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
@media (max-width: 620px)  { .panels { grid-template-columns: minmax(0, 1fr); } }

.panel { margin: 0; }
.panel figcaption {
  font-weight: 650; font-size: 14px; margin: 0 0 6px; color: var(--text-primary);
}
.panel-svg { width: 100%; height: auto; display: block; overflow: visible; }
.grid { stroke: var(--grid); stroke-width: 1; }
.divider { stroke: var(--divider); stroke-width: 1; }
.zero { stroke: var(--zero); stroke-width: 1.6; }
.y-tick, .x-tick { fill: var(--text-secondary); font-size: 10px; }
.empty { fill: var(--text-muted); font-size: 9.5px; font-style: italic; }
.dot {
  stroke: var(--surface-1); stroke-width: 1.6; cursor: pointer;
  transition: opacity 120ms ease;
}
.dot.muted { opacity: 0.12; pointer-events: none; }
tr.muted { opacity: 0.35; }
@media (prefers-reduced-motion: reduce) { .dot { transition: none; } }

.tooltip {
  position: fixed; z-index: 20; pointer-events: none; opacity: 0;
  transform: translate(-50%, -120%);
  background: var(--surface-1); color: var(--text-primary);
  border: 1px solid var(--grid); border-radius: 8px;
  padding: 7px 10px; font-size: 13px; line-height: 1.35; max-width: 260px;
  box-shadow: 0 6px 18px rgb(0 0 0 / 0.14);
}
.tooltip[data-open="true"] { opacity: 1; }

.scale-key {
  margin: 18px 0 0; padding: 0; list-style: none; display: flex; flex-wrap: wrap;
  gap: 4px 18px; color: var(--text-secondary); font-size: 13px;
}
.scale-key b { font-weight: 650; color: var(--text-primary); }

.tables { display: grid; gap: 20px; grid-template-columns: repeat(2, minmax(0, 1fr)); }
@media (max-width: 820px) { .tables { grid-template-columns: minmax(0, 1fr); } }
.table-block { margin: 0; }
.table-block figcaption { font-weight: 650; font-size: 14px; margin-bottom: 6px; }
.table-block .note { font-weight: 400; color: var(--text-muted); }
.table-scroll { overflow-x: auto; }
table { border-collapse: collapse; width: 100%; font-size: 13.5px; }
th, td { text-align: left; padding: 7px 10px; border-bottom: 1px solid var(--grid); }
thead th { color: var(--text-muted); font-weight: 500; white-space: nowrap; }
tbody th { font-weight: 550; white-space: nowrap; }
td { color: var(--text-secondary); white-space: nowrap; }
td.na { color: var(--text-muted); }
.swatch { vertical-align: -3px; margin-right: 7px; }

footer { margin-top: 40px; color: var(--text-muted); font-size: 13px; }
"""

HTML_SCRIPT = """
(function () {
  var root = document.documentElement;
  var tooltip = document.querySelector('.tooltip');
  var toggle = document.querySelector('.theme-toggle');

  function showTip(target) {
    var box = target.getBoundingClientRect();
    tooltip.textContent = target.getAttribute('data-tip');
    tooltip.style.left = (box.left + box.width / 2) + 'px';
    tooltip.style.top = box.top + 'px';
    tooltip.setAttribute('data-open', 'true');
  }
  function hideTip() { tooltip.setAttribute('data-open', 'false'); }

  document.querySelectorAll('.dot').forEach(function (dot) {
    dot.addEventListener('pointerenter', function () { showTip(dot); });
    dot.addEventListener('pointerleave', hideTip);
    dot.addEventListener('focus', function () { showTip(dot); });
    dot.addEventListener('blur', hideTip);
  });
  window.addEventListener('scroll', hideTip, true);
  document.addEventListener('keydown', function (event) {
    if (event.key === 'Escape') { hideTip(); }
  });

  var hidden = Object.create(null);
  function applyFilter() {
    document.querySelectorAll('.dot, tbody tr[data-participant]')
      .forEach(function (node) {
        var off = hidden[node.getAttribute('data-participant')] === true;
        node.classList.toggle('muted', off);
        if (node.classList.contains('dot')) {
          node.setAttribute('tabindex', off ? '-1' : '0');
        }
      });
    hideTip();
  }
  document.querySelectorAll('.chip[data-participant]').forEach(function (chip) {
    chip.addEventListener('click', function () {
      var pid = chip.getAttribute('data-participant');
      hidden[pid] = !hidden[pid];
      chip.setAttribute('aria-pressed', hidden[pid] ? 'false' : 'true');
      applyFilter();
    });
  });
  var reset = document.querySelector('.chip-reset');
  if (reset) {
    reset.addEventListener('click', function () {
      hidden = Object.create(null);
      document.querySelectorAll('.chip[data-participant]').forEach(function (chip) {
        chip.setAttribute('aria-pressed', 'true');
      });
      applyFilter();
    });
  }

  if (toggle) {
    toggle.addEventListener('click', function () {
      var dark = root.getAttribute('data-theme') === 'dark';
      if (!root.getAttribute('data-theme')) {
        dark = window.matchMedia('(prefers-color-scheme: dark)').matches;
      }
      root.setAttribute('data-theme', dark ? 'light' : 'dark');
      toggle.setAttribute('aria-pressed', dark ? 'false' : 'true');
    });
  }
})();
"""


def render_html(points: list[dict], days: list[int], participants: list[str],
                csv_path: Path) -> str:
    """A self-contained page: same chart, plus tooltips, filter and a data table."""
    series = assign_series(participants, "light")

    by_panel: dict[tuple[str, int], list[dict]] = defaultdict(list)
    for point in points:
        by_panel[(point["section"], point["day"])].append(point)

    # Two columns instead of four keeps the marks readable once the slots in a
    # day group get tight; the panels are drawn wider to match.
    dense = " dense" if len(participants) > 6 else ""

    panels = "".join(
        '<figure class="panel">'
        f'<figcaption>{html.escape(name)}</figcaption>'
        + render_panel_svg(name, days, series, by_panel, len(participants),
                           VB_W_WIDE if dense else VB_W)
        + "</figure>"
        for name, _ in SECTIONS
    )

    chips = "".join(
        f'<button type="button" class="chip" aria-pressed="true" '
        f'data-participant="{html.escape(pid, quote=True)}">'
        f'{legend_swatch(series[pid]["shape"], series[pid]["css_color"])}'
        f'{html.escape(pid)}</button>'
        for pid in participants
    )

    scale_key = "".join(
        f'<li><b>{html.escape(SCALE_LABELS[value].split("  ")[0])}</b> '
        f'{html.escape(SCALE_WORDS[value])}</li>'
        for value in (2, 1, 0, -1, -2)
    )

    generated = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    empty_days = [day for day in days
                  if not any(p["day"] == day for p in points)]
    empty_note = ""
    if empty_days:
        listed = ", ".join(f"day {day}" for day in empty_days)
        empty_note = (f' No submissions yet for {html.escape(listed)}; '
                      "those columns are kept so the timeline stays comparable.")

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Satisfaction by form section — JuvionHS-001</title>
<style>{HTML_STYLE}
{participant_color_css(participants)}</style>
</head>
<body>
<a class="skip" href="#data-table">Skip to the data table</a>
<div class="wrap">
<header>
  <h1>Change vs. before JuvionHS-001, by form section</h1>
  <p class="sub">One dot per participant per assessment point, on the
  questionnaire's change scale with a reference line at 0 (no change). Sections
  built from several questions are shown as the mean of the answers given.{empty_note}</p>
  <p class="meta">{len(points)} section scores from {len(participants)}
  participants · source <code>{html.escape(csv_path.name)}</code>
  · generated {generated}</p>
</header>

<div class="controls">
  <div class="legend" role="group" aria-label="Show or hide participants">
    <span class="legend-label" id="legend-label">Participant</span>
    {chips}
    <button type="button" class="chip chip-reset plain">Show all</button>
  </div>
  <button type="button" class="chip plain theme-toggle" aria-pressed="false">
    Toggle dark mode
  </button>
</div>

<div class="panels{dense}">{panels}</div>

<ul class="scale-key" aria-label="Scale">{scale_key}</ul>

<h2 id="data-table">Data table</h2>
<p class="meta">The same values in text, so nothing depends on colour alone.</p>
<div class="tables">{render_tables(points, days, participants, series)}</div>

<footer>
  Hover or tab through a dot for its value. Each participant keeps one colour,
  one marker shape and one horizontal slot across all four sections.
</footer>
</div>
<div class="tooltip" aria-hidden="true" data-open="false"></div>
<script>{HTML_SCRIPT}</script>
</body>
</html>
"""


# ----------------------------------------------------------------------- main

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--csv", type=Path, default=DEFAULT_CSV,
                        help=f"answers CSV (default: {DEFAULT_CSV.name})")
    parser.add_argument("--format", choices=("png", "html", "both"), default="both",
                        help="what to write (default: both)")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT,
                        help="image output path (.png, .svg, .pdf)")
    parser.add_argument("--out-html", type=Path, default=DEFAULT_OUT_HTML,
                        help="HTML page output path")
    parser.add_argument("--days", type=int, nargs="+", default=DEFAULT_DAYS,
                        metavar="DAY", help="assessment days to show")
    parser.add_argument("--theme", choices=("light", "dark"), default="light",
                        help="theme for the static image (the page has both)")
    parser.add_argument("--table", action="store_true",
                        help="also print the plotted values as a text table")
    parser.add_argument("--dpi", type=int, default=200)
    args = parser.parse_args()

    if not args.csv.exists():
        sys.exit(f"CSV not found: {args.csv}")

    points, participants = load_points(args.csv, args.days)
    if not points:
        sys.exit("No answers found for days " + ", ".join(str(d) for d in args.days))

    if args.table:
        print_table(points, args.days, participants)

    written: list[Path] = []

    if args.format in ("png", "both"):
        figure = build_figure(points, args.days, participants, args.theme)
        import matplotlib.pyplot as plt  # after build_figure has selected Agg

        args.out.parent.mkdir(parents=True, exist_ok=True)
        figure.savefig(args.out, dpi=args.dpi, facecolor=figure.get_facecolor())
        plt.close(figure)
        written.append(args.out)

    if args.format in ("html", "both"):
        args.out_html.parent.mkdir(parents=True, exist_ok=True)
        args.out_html.write_text(
            render_html(points, args.days, participants, args.csv), encoding="utf-8"
        )
        written.append(args.out_html)

    print(f"\n{len(points)} section scores from {len(participants)} participants")
    for path in written:
        print(f"  -> {path}")


if __name__ == "__main__":
    main()
