"""Transient 3D heat conduction: two 2 x 3.2 x 100 mm flat copper wires laser-welded side by side.

The wires touch on their 3.2 mm faces (combined end 4.0 x 3.2 mm, top 10 mm enamel stripped).
Process modelled on remote laser welding of copper hairpins / X-pins
(Shaikh, LUT Master's thesis 2026, Table 8; TRUMPF TruFiber 6000 P, 1070 nm, ring-core beam):

  * scanner moves the spot along an ellipse at 300 mm/s across both wires,
  * three cycles: C1 core 2000 W + ring 4000 W (3 loops), C2 500 + 3500 W (3 loops),
    C3 40 + 3500 W (2 loops); C2/C3 use a 0.4 mm larger ellipse,
  * absorptivity at 1 µm: solid Cu ~5 %, molten ~14 %, inside an open keyhole ~60 %
    (multiple reflections in the vapour capillary),
  * the 71 µm core is far below the 0.2 mm grid, so the keyhole is triggered by power density
    (core intensity > ~100 kW/mm²: C1 ≈ 505, C2 ≈ 126, C3 ≈ 10) instead of the smeared cell temperature;
    its depth follows the core power,
  * core energy is deposited along the keyhole (volumetric), ring energy on the surface.

Before melting the joint conducts through a contact conductance only; once either side of the
interface exceeds the solidus it is treated as fused. Explicit finite differences, apparent heat
capacity for melting, temperature capped at the boiling point (excess leaves as vapour).
Output: data/field.bin (uint16, 0.1 °C per unit, [frame, x, y, z]), data/meta.json.
"""
import json
import math
import os

import numpy as np

# --- geometry (m) -----------------------------------------------------------
WIRE_W, D, H = 2.0e-3, 3.2e-3, 100.0e-3
W = 2 * WIRE_W
STRIP = 10.0e-3
dx = dy = 0.2e-3
dz = 0.25e-3
nx, ny, nz = round(W / dx), round(D / dy), round(H / dz)   # 20, 16, 400
IA, IB = nx // 2 - 1, nx // 2

# --- copper (C11000) --------------------------------------------------------
k, rho, cp = 390.0, 8940.0, 385.0
T_SOL, T_LIQ, L = 1075.0, 1095.0, 205e3
T_VAP = 2562.0
T0 = 25.0
h_bare, h_enamel = 25.0, 15.0
H_CONTACT = 3.0e4

# --- laser process (thesis Table 5 / Table 8) --------------------------------
V_SCAN = 0.30                        # m/s
ETA_SOLID, ETA_LIQUID, ETA_KEYHOLE = 0.05, 0.14, 0.60
SIG_CORE, SIG_RING = 0.15e-3, 0.25e-3   # Gaussian widths after smearing onto the 0.2 mm grid
KH_MAX = 1.6e-3                      # keyhole depth at 2 kW core
R_CORE = 35.5e-6                     # focused core spot radius (71 µm)
I_KH = 100.0                         # kW/mm²: core intensity that drills a keyhole (deep-penetration threshold)
KH_TAU = 3e-3                        # keyhole open/close time constant
KH_R = 0.22e-3                       # radius of cells that receive keyhole energy
F_KH_VOL = 0.75                      # share of absorbed core power deposited along the keyhole
CYCLES = [  # core W, ring W, loops, ellipse semi-axes (x, y)
    dict(name="C1", core=2000.0, ring=4000.0, loops=3, a=1.7e-3, b=1.2e-3),
    dict(name="C2", core=500.0, ring=3500.0, loops=3, a=1.9e-3, b=1.4e-3),
    dict(name="C3", core=40.0, ring=3500.0, loops=2, a=1.9e-3, b=1.4e-3),
]
GAP = 5e-3                           # pause between cycles (scanner jump)
T_END = float(os.environ.get("T_END", 3.0))


def ellipse_perimeter(a, b):
    return math.pi * (3 * (a + b) - math.sqrt((3 * a + b) * (a + 3 * b)))


schedule, t_cursor = [], 0.0
for c in CYCLES:
    dur = c["loops"] * ellipse_perimeter(c["a"], c["b"]) / V_SCAN
    schedule.append(dict(c, t0=t_cursor, t1=t_cursor + dur))
    t_cursor += dur + GAP
T_WELD = schedule[-1]["t1"]


def beam_at(t):
    """Beam centre, cycle and powers at time t (constant-speed ellipse, angle-parametrised)."""
    for c in schedule:
        if c["t0"] <= t < c["t1"]:
            per = ellipse_perimeter(c["a"], c["b"])
            phase = 2 * math.pi * ((t - c["t0"]) * V_SCAN / per)
            return c, c["a"] * math.cos(phase), c["b"] * math.sin(phase)
    return None, 0.0, 0.0


alpha = k / (rho * cp)
dt = 0.4 / (alpha * (2 / dx**2 + 2 / dy**2 + 2 / dz**2))

xc = (np.arange(nx) + 0.5) * dx - W / 2
yc = (np.arange(ny) + 0.5) * dy - D / 2
zc = (np.arange(nz) + 0.5) * dz
X, Y = np.meshgrid(xc, yc, indexing="ij")
h_z = np.where(zc > H - STRIP, h_bare, h_enamel)
G_contact = 1 / (dx / k + 1 / H_CONTACT)

T = np.full((nx, ny, nz), T0)
fused = np.zeros((ny, nz), bool)
kh_depth = 0.0


def gauss(bx, by, sig):
    g = np.exp(-((X - bx) ** 2 + (Y - by) ** 2) / (2 * sig**2))
    return g / (g.sum() * dx * dy)


def cp_eff(T):
    c = np.full_like(T, cp)
    mush = (T > T_SOL) & (T < T_LIQ)
    c[mush] += L / (T_LIQ - T_SOL)
    return c


samples = np.concatenate([np.linspace(0, T_WELD, 84), np.linspace(T_WELD, T_END, 46)[1:]])
frames = []
KEYS = ("t", "Tmax", "T_edge", "T_80", "melt_depth_mm", "melt_width_mm", "fused_depth_mm", "keyhole_mm", "P_abs", "E_abs")
probes = {k_: [] for k_ in KEYS}
iz_edge, iz_80 = int((H - STRIP) / dz), int(80e-3 / dz)
fused_depth_max = 0.0
P_abs_now, E_abs = 0.0, 0.0

t, si = 0.0, 0
while si < len(samples):
    if t >= samples[si] - 1e-12:
        frames.append(np.clip(T * 10, 0, 65535).astype(np.uint16))
        molten = T > T_SOL
        cols = molten.any(axis=(0, 1))
        depth = (nz - np.where(cols)[0].min()) * dz * 1e3 if cols.any() else 0.0
        top = molten[:, :, -1].any(axis=1)
        width = top.sum() * dx * 1e3
        fz = np.where(fused.any(0))[0]
        fused_depth_max = max(fused_depth_max, (nz - fz.min()) * dz * 1e3 if len(fz) else 0.0)
        vals = (t, T.max(), T[:, :, iz_edge].max(), T[:, :, iz_80].max(), depth, width, fused_depth_max,
                kh_depth * 1e3, P_abs_now, E_abs)
        for key, val in zip(KEYS, vals):
            probes[key].append(round(float(val), 5 if key == "t" else 2))
        si += 1
        continue

    Tp = np.pad(T, 1, mode="edge")
    lap = ((Tp[2:, 1:-1, 1:-1] - 2 * T + Tp[:-2, 1:-1, 1:-1]) / dx**2
           + (Tp[1:-1, 2:, 1:-1] - 2 * T + Tp[1:-1, :-2, 1:-1]) / dy**2
           + (Tp[1:-1, 1:-1, 2:] - 2 * T + Tp[1:-1, 1:-1, :-2]) / dz**2)
    q = k * lap
    fused |= (T[IA] > T_SOL) | (T[IB] > T_SOL)
    G = np.where(fused, k / dx, G_contact)
    corr = (G - k / dx) * (T[IB] - T[IA]) / dx
    q[IA] += corr
    q[IB] -= corr
    q[0] -= h_z * (T[0] - T0) / dx
    q[-1] -= h_z * (T[-1] - T0) / dx
    q[:, 0] -= h_z * (T[:, 0] - T0) / dy
    q[:, -1] -= h_z * (T[:, -1] - T0) / dy
    q[:, :, -1] -= h_bare * (T[:, :, -1] - T0) / dz

    # --- moving ring-core laser ---------------------------------------------
    cyc, bx, by = beam_at(t)
    P_abs_now = 0.0
    if cyc is not None:
        ib = min(max(int((bx + W / 2) / dx), 0), nx - 1)
        jb = min(max(int((by + D / 2) / dy), 0), ny - 1)
        Tb = T[ib, jb, -1]
        i_core = cyc["core"] / (math.pi * (R_CORE * 1e3) ** 2) / 1e3     # kW/mm²
        kh_target = KH_MAX * (cyc["core"] / 2000.0) ** 0.7 if i_core >= I_KH else 0.0
        kh_depth += (kh_target - kh_depth) * min(1.0, dt / KH_TAU)
        molten_spot = Tb > T_SOL or kh_depth > 0.1e-3
        eta_ring = ETA_LIQUID if molten_spot else ETA_SOLID
        eta_core = ETA_KEYHOLE if kh_depth > 0.2e-3 else eta_ring
        p_ring, p_core = cyc["ring"] * eta_ring, cyc["core"] * eta_core
        vol_share = F_KH_VOL if kh_depth > 0.2e-3 else 0.0
        q[:, :, -1] += (p_ring * gauss(bx, by, SIG_RING) + p_core * (1 - vol_share) * gauss(bx, by, SIG_CORE)) / dz
        if vol_share > 0:
            col = (X - bx) ** 2 + (Y - by) ** 2 < KH_R**2
            if not col.any():
                col = np.zeros_like(col)
                col[ib, jb] = True
            nlay = max(1, int(round(kh_depth / dz)))
            q[col, -nlay:] += p_core * vol_share / (col.sum() * nlay * dx * dy * dz)
        P_abs_now = p_ring + p_core
        E_abs += P_abs_now * dt
    else:
        kh_depth += (0.0 - kh_depth) * min(1.0, dt / KH_TAU)

    T = np.minimum(T + dt * q / (rho * cp_eff(T)), T_VAP)
    T[:, :, 0] = T0
    t += dt

os.makedirs("data", exist_ok=True)
np.stack(frames).tofile("data/field.bin")
E_in = sum(c["loops"] * ellipse_perimeter(c["a"], c["b"]) / V_SCAN * (c["core"] + c["ring"]) for c in CYCLES)
meta = dict(nx=nx, ny=ny, nz=nz, dz_mm=dz * 1e3, W=W * 1e3, D=D * 1e3, H=H * 1e3, strip=STRIP * 1e3,
            v_scan=V_SCAN * 1e3, t_weld=T_WELD, dt=dt, h_contact=H_CONTACT, frames=len(frames),
            eta=[ETA_SOLID, ETA_LIQUID, ETA_KEYHOLE], E_in=E_in, E_abs=E_abs,
            schedule=[dict(name=c["name"], core=c["core"], ring=c["ring"], loops=c["loops"], a_mm=c["a"] * 1e3,
                           b_mm=c["b"] * 1e3, t0=c["t0"], t1=c["t1"], perim_mm=ellipse_perimeter(c["a"], c["b"]) * 1e3)
                      for c in schedule],
            Tmax_overall=max(probes["Tmax"]), T_edge_max=max(probes["T_edge"]), T_80_max=max(probes["T_80"]),
            t_edge_peak=probes["t"][int(np.argmax(probes["T_edge"]))],
            melt_depth_max=max(probes["melt_depth_mm"]), melt_width_max=max(probes["melt_width_mm"]),
            fused_depth=float(fused_depth_max), keyhole_max=max(probes["keyhole_mm"]), probes=probes)
json.dump(meta, open("data/meta.json", "w"))
print({k_: v for k_, v in meta.items() if k_ not in ("probes", "schedule")})
for c in meta["schedule"]:
    print(c)
