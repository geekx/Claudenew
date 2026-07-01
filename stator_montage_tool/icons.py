"""Icon drawing primitives, matplotlib-based, matching the reference gallery style:
black cells, gold/grey stator glyph, blue jaw brackets, outline thermometer.
"""

import numpy as np
import matplotlib.patches as mpatches
import matplotlib.path as mpath
import matplotlib.transforms as mtransforms
from matplotlib.patches import FancyArrowPatch, Circle, Rectangle, FancyBboxPatch

GOLD = "#F5A623"
GOLD_DARK = "#C97F0E"
GREY = "#9E9E9E"
GREY_DARK = "#6E6E6E"
BLUE = "#2E6DB4"
RED = "#E8352C"
WHITE = "#FFFFFF"
BLACK = "#000000"


def _cell(ax, bg=BLACK):
    ax.set_xlim(-1, 1)
    ax.set_ylim(-1, 1)
    ax.set_aspect("equal")
    ax.set_xticks([])
    ax.set_yticks([])
    for spine in ax.spines.values():
        spine.set_visible(False)
    ax.set_facecolor(bg)


# ---------------------------------------------------------------------------
# Stator glyph: gold cap + grey laminated stack + gold pin teeth
# ---------------------------------------------------------------------------

def _stator_glyph_patches(cx=0.0, cy=0.0, w=0.9, h=1.2):
    """Return list of (patch_kind, kwargs) describing the stator glyph
    centered at (cx, cy), before rotation."""
    patches = []
    cap_w, cap_h = w * 0.62, h * 0.10
    cap_y = cy + h * 0.42
    patches.append(("fancybbox", dict(
        xy=(cx - cap_w / 2, cap_y - cap_h / 2), width=cap_w, height=cap_h,
        boxstyle="round,pad=0,rounding_size=" + str(cap_h * 0.5),
        facecolor=GOLD, edgecolor="none")))

    n_bars = 4
    bar_h = h * 0.09
    gap = h * 0.03
    top_y = cap_y - cap_h / 2 - gap
    for i in range(n_bars):
        bw = w * (1.0 - 0.06 * i)
        by = top_y - i * (bar_h + gap) - bar_h / 2
        patches.append(("rect", dict(
            xy=(cx - bw / 2, by - bar_h / 2), width=bw, height=bar_h,
            facecolor=GREY, edgecolor="none")))
    stack_bottom = top_y - (n_bars - 1) * (bar_h + gap) - bar_h - gap / 2

    n_teeth = 9
    tooth_h = h * 0.22
    tooth_w = w * 0.7 / n_teeth * 0.55
    span = w * 0.72
    xs = np.linspace(cx - span / 2, cx + span / 2, n_teeth)
    for x in xs:
        patches.append(("rect", dict(
            xy=(x - tooth_w / 2, stack_bottom - tooth_h), width=tooth_w, height=tooth_h,
            facecolor=GOLD, edgecolor="none")))
    return patches


def draw_stator_glyph(ax, cx=0.0, cy=0.0, w=0.9, h=1.2, angle=0, alpha=1.0):
    """Draw the stator glyph rotated by `angle` degrees about (cx, cy)."""
    t = mtransforms.Affine2D().rotate_deg_around(cx, cy, angle) + ax.transData
    for kind, kw in _stator_glyph_patches(cx, cy, w, h):
        kw = dict(kw)
        kw["alpha"] = alpha
        if kind == "rect":
            p = Rectangle(kw.pop("xy"), kw.pop("width"), kw.pop("height"), **kw)
        else:
            p = FancyBboxPatch(kw.pop("xy"), kw.pop("width"), kw.pop("height"),
                                boxstyle=kw.pop("boxstyle"), **kw)
        p.set_transform(t)
        ax.add_patch(p)


def _rotation_arrow(ax, cx, cy, r, theta1, theta2, color=BLUE):
    arc = mpatches.Arc((cx, cy), 2 * r, 2 * r, angle=0,
                        theta1=theta1, theta2=theta2, color=color, lw=3)
    ax.add_patch(arc)
    end_ang = np.radians(theta2)
    ex, ey = cx + r * np.cos(end_ang), cy + r * np.sin(end_ang)
    tang = np.radians(theta2 + 90)
    dx, dy = np.cos(tang) * 0.12, np.sin(tang) * 0.12
    ax.add_patch(mpatches.FancyArrow(ex - dx, ey - dy, dx, dy,
                                      width=0.001, head_width=0.14, head_length=0.14,
                                      color=color, length_includes_head=True))


def draw_rotation_icon(ax, code, angle_map):
    _cell(ax)
    angle = angle_map[code]
    draw_stator_glyph(ax, 0, -0.05, w=0.85, h=1.05, angle=angle)
    if code != 0:
        _rotation_arrow(ax, 0, -0.05, 0.95, 20, 250, color=BLUE)
    ax.text(0, -1.18, f"R = {code}  ({angle}°)", color=WHITE, ha="center", va="top",
             fontsize=10, transform=ax.transData, clip_on=False)


# ---------------------------------------------------------------------------
# Clamp icons
# ---------------------------------------------------------------------------

def _jaw_finger(ax, x0, y0, length=0.6, foot=0.18, bend="in", color=BLUE, lw=4):
    """A single vertical jaw finger with a small foot bend at the bottom.
    bend='in' -> foot bends toward x=0 (center); 'out' -> away from center."""
    sign = 1 if x0 < 0 else -1
    if bend == "out":
        sign = -sign
    xs = [x0, x0, x0 + sign * foot]
    ys = [y0 + length, y0, y0]
    ax.plot(xs, ys, color=color, lw=lw, solid_capstyle="round", solid_joinstyle="round")


def draw_clamp_icon(ax, code):
    _cell(ax)
    if code == "00":
        # two-jaw external, perpendicular to axis: side view, jaws squeeze from left/right
        ax.add_patch(Rectangle((-0.28, -0.5), 0.56, 1.0, facecolor=GREY, edgecolor="none"))
        _jaw_finger(ax, -0.75, -0.35, length=0.7, foot=0.18, bend="in", color=BLUE)
        _jaw_finger(ax, 0.75, -0.35, length=0.7, foot=0.18, bend="in", color=BLUE)
        ax.add_patch(mpatches.FancyArrow(-0.55, 0, 0.18, 0, width=0.02, head_width=0.12,
                                          head_length=0.12, color=BLUE))
        ax.add_patch(mpatches.FancyArrow(0.55, 0, -0.18, 0, width=0.02, head_width=0.12,
                                          head_length=0.12, color=BLUE))
    else:
        # top view: dashed circle = stator OD (10) or bore/ID (11)
        r = 0.62
        circ = Circle((0, 0), r, fill=False, edgecolor=GREY, lw=2, linestyle=(0, (4, 3)))
        ax.add_patch(circ)
        n = 3
        for k in range(n):
            ang = np.radians(90 + k * 360 / n)
            if code == "10":  # external: fingers outside circle, hooking inward
                fx, fy = (r + 0.32) * np.cos(ang), (r + 0.32) * np.sin(ang)
                tip_x, tip_y = r * np.cos(ang), r * np.sin(ang)
            else:  # "11" internal: fingers inside circle (bore), hooking outward
                fx, fy = (r - 0.42) * np.cos(ang), (r - 0.42) * np.sin(ang)
                tip_x, tip_y = (r - 0.12) * np.cos(ang), (r - 0.12) * np.sin(ang)
            ax.plot([fx, tip_x], [fy, tip_y], color=BLUE, lw=4,
                     solid_capstyle="round")
            ax.plot(fx, fy, marker="o", color=BLUE, markersize=5)
    label = {"00": "00 双爪外夹", "11": "11 三爪内夹", "10": "10 三爪外夹"}[code]
    ax.text(0, -1.18, label, color=WHITE, ha="center", va="top", fontsize=10,
             clip_on=False)


# ---------------------------------------------------------------------------
# Temperature icon
# ---------------------------------------------------------------------------

def draw_temp_icon(ax, value, threshold=40.0):
    _cell(ax)
    hot = value > threshold
    fill = RED if hot else BLUE
    stem_w = 0.22
    stem_bottom, stem_top = -0.35, 0.75
    bulb_r = 0.26
    outline = mpatches.FancyBboxPatch(
        (-stem_w / 2, stem_bottom), stem_w, stem_top - stem_bottom,
        boxstyle=f"round,pad=0,rounding_size={stem_w/2}",
        facecolor="none", edgecolor=WHITE, lw=2.2)
    ax.add_patch(outline)
    bulb = Circle((0, stem_bottom - bulb_r * 0.55), bulb_r, facecolor="none",
                   edgecolor=WHITE, lw=2.2)
    ax.add_patch(bulb)
    frac = np.clip((value) / 100.0, 0.08, 0.92)
    fill_bottom = stem_bottom
    fill_top = stem_bottom + (stem_top - stem_bottom) * frac
    inner_w = stem_w * 0.45
    ax.add_patch(Rectangle((-inner_w / 2, fill_bottom), inner_w, fill_top - fill_bottom,
                             facecolor=fill, edgecolor="none"))
    ax.add_patch(Circle((0, stem_bottom - bulb_r * 0.55), bulb_r * 0.62,
                          facecolor=fill, edgecolor="none"))
    for i, ty in enumerate(np.linspace(stem_bottom + 0.08, stem_top - 0.1, 5)):
        ax.plot([stem_w / 2, stem_w / 2 + 0.14], [ty, ty], color=WHITE, lw=1.4)
    ax.text(0, -1.18, f"T = {value:g}°C", color=WHITE, ha="center", va="top",
             fontsize=10, clip_on=False)


# ---------------------------------------------------------------------------
# Laser mark direction icon (arrow with a top bar), relative to stator top/bottom
# ---------------------------------------------------------------------------

def _bar_arrow(ax, x, y_from, y_to, color=GOLD, lw=4):
    ax.plot([x - 0.16, x + 0.16], [y_from, y_from], color=color, lw=lw,
             solid_capstyle="round")
    ax.annotate("", xy=(x, y_to), xytext=(x, y_from),
                arrowprops=dict(arrowstyle="-|>", color=color, lw=lw,
                                 mutation_scale=18))


def draw_laser_icon(ax, code):
    _cell(ax)
    # simplified grey stack silhouette (no cap/teeth) as the stator reference
    ax.add_patch(Rectangle((-0.4, -0.45), 0.8, 0.9, facecolor=GREY_DARK, edgecolor="none"))
    for gy in np.linspace(-0.35, 0.35, 4):
        ax.plot([-0.4, 0.4], [gy, gy], color=BLACK, lw=1.5, alpha=0.5)
    if code == 0:
        _bar_arrow(ax, 0, y_from=0.95, y_to=0.5)
        label = "0 · 标记于上部"
    else:
        _bar_arrow(ax, 0, y_from=-0.95, y_to=-0.5)
        label = "1 · 标记于下部"
    ax.text(0, -1.18, label, color=WHITE, ha="center", va="top", fontsize=10,
             clip_on=False)


# ---------------------------------------------------------------------------
# Process chevron
# ---------------------------------------------------------------------------

def _wrap_name(name, max_chars=9):
    words = name.split(" ")
    if len(words) == 1:
        return name
    line1, line2 = "", ""
    for w in words:
        if len((line1 + " " + w).strip()) <= max_chars or not line1:
            line1 = (line1 + " " + w).strip()
        else:
            line2 = (line2 + " " + w).strip()
    return line1 if not line2 else f"{line1}\n{line2}"


def draw_chevron(ax, op, name, color):
    _cell(ax, bg="white")
    notch = 0.28
    xs = [-0.95, 0.6, 0.98, 0.6, -0.95, -0.95 + notch]
    ys = [0.55, 0.55, 0, -0.55, -0.55, 0]
    ax.add_patch(mpatches.Polygon(list(zip(xs, ys)), closed=True,
                                    facecolor=color, edgecolor="none"))
    fontsize = 9 if len(name) <= 9 else 7.5
    ax.text(-0.2, 0.24, op, color=WHITE, ha="center", va="center",
             fontsize=11, fontweight="bold")
    ax.text(-0.2, -0.24, _wrap_name(name), color=WHITE, ha="center", va="center",
             fontsize=fontsize, linespacing=1.2)
