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

Usage:
    python scripts/plot_satisfaction.py
    python scripts/plot_satisfaction.py --days 7 14 21 28 --theme dark --table
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
from collections import defaultdict
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.lines import Line2D  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CSV = REPO_ROOT / "form_answers" / "juvionhs01_satisfaction_form_answers.csv"
DEFAULT_OUT = REPO_ROOT / "form_answers" / "plots" / "satisfaction_by_section.png"

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


def section_score(row: dict, columns: list[str]) -> float | None:
    """Mean of the answered questions in a section, or None if none answered."""
    values = []
    for column in columns:
        raw = (row.get(column) or "").strip()
        if raw == "":
            continue
        try:
            values.append(float(raw))
        except ValueError:
            continue
    return sum(values) / len(values) if values else None


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
            score = section_score(row, columns)
            if score is None:
                continue
            points.append({
                "participant": pid,
                "day": day,
                "section": section_name,
                "score": score,
            })

    return points, sorted(participants, key=participant_sort_key)


def print_table(points: list[dict], days: list[int], participants: list[str]) -> None:
    """Text equivalent of the chart, so the values are readable without colour."""
    by_key = {(p["participant"], p["day"], p["section"]): p["score"] for p in points}
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
                score = by_key.get((pid, day, section_name))
                if score is None:
                    cells += "--".ljust(8)
                else:
                    cells += f"{score:+.2f}".ljust(8)
                    has_value = True
            if has_value:
                print(pid.ljust(width) + cells)


def build_figure(points: list[dict], days: list[int], participants: list[str],
                 theme_name: str):
    theme = THEMES[theme_name]
    hues = CATEGORICAL[theme_name]

    colors = {pid: hues[i % len(hues)] for i, pid in enumerate(participants)}
    markers = {pid: MARKERS[i % len(MARKERS)] for i, pid in enumerate(participants)}

    # A stable horizontal slot per participant inside each day group: position
    # reinforces identity and keeps equal answers from hiding each other.
    count = max(len(participants), 1)
    span = 0.78
    slots = {pid: (i - (count - 1) / 2) * (span / count)
             for i, pid in enumerate(participants)}

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
                pid = point["participant"]
                ax.scatter(
                    index + slots[pid], point["score"],
                    s=130 if markers[pid] == "*" else 78,
                    marker=markers[pid],
                    color=colors[pid],
                    edgecolors=theme["surface"],   # surface ring on overlap
                    linewidths=1.6,
                    zorder=3,
                )

    axes[0].set_yticks(sorted(SCALE_LABELS))
    axes[0].set_yticklabels([SCALE_LABELS[value] for value in sorted(SCALE_LABELS)])

    handles = [
        Line2D([], [], linestyle="none", marker=markers[pid], markersize=8,
               markerfacecolor=colors[pid], markeredgecolor=theme["surface"],
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


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--csv", type=Path, default=DEFAULT_CSV,
                        help=f"answers CSV (default: {DEFAULT_CSV.name})")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT,
                        help="output image path (.png, .svg, .pdf)")
    parser.add_argument("--days", type=int, nargs="+", default=DEFAULT_DAYS,
                        metavar="DAY", help="assessment days to show")
    parser.add_argument("--theme", choices=("light", "dark"), default="light")
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

    figure = build_figure(points, args.days, participants, args.theme)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(args.out, dpi=args.dpi, facecolor=figure.get_facecolor())
    plt.close(figure)

    print(f"\n{len(points)} section scores from {len(participants)} participants "
          f"-> {args.out}")


if __name__ == "__main__":
    main()
