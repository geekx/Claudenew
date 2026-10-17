"""Transient 3D heat conduction: two 2 x 3.2 x 100 mm flat copper wires welded side by side.

The wires touch on their 3.2 mm faces (combined section 4.0 x 3.2 mm). Both have the top
10 mm of enamel stripped. A Gaussian laser spot centred on the joint line heats the top face.
Before melting the joint conducts through a contact conductance only; once either side of
the interface exceeds the solidus it is treated as fused (perfect conduction from then on).

Explicit finite differences, apparent-heat-capacity method for melting.
Output: data/field.bin (uint16, 0.1 °C per unit, [frame, x, y, z]), data/meta.json.
"""
import json
import os

import numpy as np

# --- geometry (m) -----------------------------------------------------------
WIRE_W, D, H = 2.0e-3, 3.2e-3, 100.0e-3
W = 2 * WIRE_W
STRIP = 10.0e-3
dx = dy = 0.2e-3
dz = 0.5e-3
nx, ny, nz = round(W / dx), round(D / dy), round(H / dz)   # 20, 16, 200
IA, IB = nx // 2 - 1, nx // 2                              # cells either side of the joint

# --- copper (C11000) --------------------------------------------------------
k, rho, cp = 390.0, 8940.0, 385.0
T_SOL, T_LIQ, L = 1075.0, 1095.0, 205e3
T0 = 25.0
h_bare, h_enamel = 25.0, 15.0
H_CONTACT = 3.0e4      # clamped Cu-Cu contact conductance, W/m²K

# --- weld source ------------------------------------------------------------
P_ABS = 2400.0         # absorbed power, W
SIG_X, SIG_Y = 0.85e-3, 0.7e-3
T_ON = 0.15
T_END = 3.0
F_KH = 0.55            # fraction of power deposited along the keyhole (conical volumetric source)
KH_DEPTH = 1.8e-3      # steady keyhole depth
KH_R0 = 0.35e-3        # keyhole mouth radius (tapers to 0 at the tip)
KH_RISE = 0.03         # keyhole drilling time, s
T_VAP = 2562.0         # boiling point: excess energy leaves as vapour

alpha = k / (rho * cp)
dt = 0.4 / (alpha * (2 / dx**2 + 2 / dy**2 + 2 / dz**2))

xc = (np.arange(nx) + 0.5) * dx - W / 2
yc = (np.arange(ny) + 0.5) * dy - D / 2
zc = (np.arange(nz) + 0.5) * dz
X, Y = np.meshgrid(xc, yc, indexing="ij")
g = np.exp(-X**2 / (2 * SIG_X**2) - Y**2 / (2 * SIG_Y**2))
q_top = (1 - F_KH) * P_ABS * g / (g.sum() * dx * dy)
XX, YY = np.meshgrid(xc, yc, indexing="ij")
R2 = XX**2 + YY**2
depth_from_top = H - zc


def keyhole_depth(t):
    return KH_DEPTH * min(1.0, t / KH_RISE) if t < T_ON else 0.0


def keyhole_source(d):
    """Volumetric heat (W/m³) spread evenly along a cone of depth d."""
    if d < dz:
        return None
    zs = depth_from_top < d
    r = KH_R0 * (1 - depth_from_top[zs] / d) + 0.12e-3
    mask = np.zeros((nx, ny, nz), bool)
    mask[:, :, zs] = R2[:, :, None] < r[None, None, :] ** 2
    if not mask.any():
        return None
    qv = np.zeros((nx, ny, nz))
    qv[mask] = F_KH * P_ABS / (mask.sum() * dx * dy * dz)
    return qv

h_z = np.where(zc > H - STRIP, h_bare, h_enamel)
G_contact = 1 / (dx / k + 1 / H_CONTACT)      # W/m²K across the joint before fusion

T = np.full((nx, ny, nz), T0)
fused = np.zeros((ny, nz), bool)


def cp_eff(T):
    c = np.full_like(T, cp)
    mush = (T > T_SOL) & (T < T_LIQ)
    c[mush] += L / (T_LIQ - T_SOL)
    return c


samples = np.concatenate([np.linspace(0, T_ON, 46), np.linspace(T_ON, T_END, 61)[1:]])
frames = []
probes = {k_: [] for k_ in ("t", "Tmax", "T_edge", "T_80", "melt_depth_mm", "melt_width_mm", "fused_depth_mm", "keyhole_mm")}
iz_edge, iz_80 = int((H - STRIP) / dz), int(80e-3 / dz)
fused_depth_max = 0.0

t, si = 0.0, 0
_kh_cache = {}
while si < len(samples):
    if t >= samples[si] - 1e-12:
        frames.append(np.clip(T * 10, 0, 65535).astype(np.uint16))
        molten = T > T_SOL
        col = molten[:, ny // 2, :]
        depth = (nz - np.where(col.any(0))[0].min()) * dz * 1e3 if col.any() else 0.0
        top = molten[:, ny // 2, -1]
        width = top.sum() * dx * 1e3
        fz = np.where(fused.any(0))[0]
        fused_depth_max = max(fused_depth_max, (nz - fz.min()) * dz * 1e3 if len(fz) else 0.0)
        for key, val in (("t", t), ("Tmax", T.max()), ("T_edge", T[:, :, iz_edge].max()), ("T_80", T[:, :, iz_80].max()),
                         ("melt_depth_mm", depth), ("melt_width_mm", width), ("fused_depth_mm", fused_depth_max),
                         ("keyhole_mm", keyhole_depth(t) * 1e3)):
            probes[key].append(round(float(val), 4 if key == "t" else 2))
        si += 1
        continue

    Tp = np.pad(T, 1, mode="edge")
    lap = ((Tp[2:, 1:-1, 1:-1] - 2 * T + Tp[:-2, 1:-1, 1:-1]) / dx**2
           + (Tp[1:-1, 2:, 1:-1] - 2 * T + Tp[1:-1, :-2, 1:-1]) / dy**2
           + (Tp[1:-1, 1:-1, 2:] - 2 * T + Tp[1:-1, 1:-1, :-2]) / dz**2)
    q = k * lap
    # joint: replace perfect-contact flux with contact conductance where not yet fused
    fused |= (T[IA] > T_SOL) | (T[IB] > T_SOL)
    G = np.where(fused, k / dx, G_contact)
    corr = (G - k / dx) * (T[IB] - T[IA]) / dx
    q[IA] += corr
    q[IB] -= corr
    # side convection
    q[0] -= h_z * (T[0] - T0) / dx
    q[-1] -= h_z * (T[-1] - T0) / dx
    q[:, 0] -= h_z * (T[:, 0] - T0) / dy
    q[:, -1] -= h_z * (T[:, -1] - T0) / dy
    if t < T_ON:
        q[:, :, -1] += q_top / dz
        d_kh = round(keyhole_depth(t) / dz)
        if d_kh not in _kh_cache:
            _kh_cache[d_kh] = keyhole_source(keyhole_depth(t))
        if _kh_cache[d_kh] is not None:
            q += _kh_cache[d_kh]
    q[:, :, -1] -= h_bare * (T[:, :, -1] - T0) / dz
    T = np.minimum(T + dt * q / (rho * cp_eff(T)), T_VAP)
    T[:, :, 0] = T0
    t += dt

os.makedirs("data", exist_ok=True)
np.stack(frames).tofile("data/field.bin")
meta = dict(nx=nx, ny=ny, nz=nz, W=W * 1e3, D=D * 1e3, H=H * 1e3, strip=STRIP * 1e3, P_abs=P_ABS,
            sig_mm=[SIG_X * 1e3, SIG_Y * 1e3], t_on=T_ON, dt=dt, h_contact=H_CONTACT, frames=len(frames),
            Tmax_overall=max(probes["Tmax"]), T_edge_max=max(probes["T_edge"]), T_80_max=max(probes["T_80"]),
            t_edge_peak=probes["t"][int(np.argmax(probes["T_edge"]))],
            melt_depth_max=max(probes["melt_depth_mm"]), melt_width_max=max(probes["melt_width_mm"]),
            fused_depth=float(fused_depth_max), keyhole_max=KH_DEPTH * 1e3, F_kh=F_KH, probes=probes)
json.dump(meta, open("data/meta.json", "w"))
print({k_: v for k_, v in meta.items() if k_ != "probes"})
