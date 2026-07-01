"""Assemble the full montage-process sheet: one column per process step,
one row per coded state category (clamp / process / rotation / temperature / laser mark)."""

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.font_manager as fm

for _path in ("/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",):
    try:
        fm.fontManager.addfont(_path)
        _cjk_name = fm.FontProperties(fname=_path).get_name()
        plt.rcParams["font.sans-serif"] = [_cjk_name, "DejaVu Sans"]
        plt.rcParams["axes.unicode_minus"] = False
        break
    except Exception:
        pass

from .states import ROTATION_ANGLES
from . import icons

ROW_LABELS = ["夹持状态", "工序", "产品旋转状态", "温度状态", "激光标记方向"]
ROW_HEIGHTS = [1.3, 1.1, 1.3, 1.3, 1.3]

CHEVRON_START = "#F2933C"
CHEVRON_END = "#9E9E9E"


def _chevron_color(i, n):
    t = i / max(1, n - 1)
    import matplotlib.colors as mcolors
    c0 = np.array(mcolors.to_rgb(CHEVRON_START))
    c1 = np.array(mcolors.to_rgb(CHEVRON_END))
    return tuple(c0 * (1 - t) + c1 * t)


def render_sheet(steps, out_path, col_width=1.9, dpi=150):
    n = len(steps)
    ncols = n + 1
    nrows = len(ROW_LABELS)
    fig = plt.figure(figsize=(col_width * ncols, sum(ROW_HEIGHTS) * 1.05 + 0.6),
                      facecolor="white")
    gs = fig.add_gridspec(nrows, ncols, height_ratios=ROW_HEIGHTS,
                           width_ratios=[0.9] + [1] * n,
                           wspace=0.06, hspace=0.12)

    for r, label in enumerate(ROW_LABELS):
        ax = fig.add_subplot(gs[r, 0])
        ax.axis("off")
        ax.set_facecolor("white")
        ax.text(0.5, 0.5, label, ha="center", va="center", fontsize=13,
                 fontweight="bold", color="#222222", transform=ax.transAxes)

    for c, step in enumerate(steps):
        col = c + 1
        ax = fig.add_subplot(gs[0, col])
        icons.draw_clamp_icon(ax, step.clamp)

        ax = fig.add_subplot(gs[1, col])
        icons.draw_chevron(ax, step.op, step.name, _chevron_color(c, n))

        ax = fig.add_subplot(gs[2, col])
        icons.draw_rotation_icon(ax, step.rotation, ROTATION_ANGLES)

        ax = fig.add_subplot(gs[3, col])
        icons.draw_temp_icon(ax, step.temperature)

        ax = fig.add_subplot(gs[4, col])
        icons.draw_laser_icon(ax, step.laser_mark)

    fig.suptitle("扁线定子 Montage Process — 状态编号定义图面", fontsize=16,
                  fontweight="bold", y=0.995)
    fig.savefig(out_path, dpi=dpi, bbox_inches="tight", facecolor="white")
    plt.close(fig)
