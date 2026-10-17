"""Transient 3D heat conduction in a 2 x 3.2 x 100 mm flat copper wire.

Weld heat source: Gaussian surface flux on the stripped top face (z = 100 mm).
Explicit finite differences, apparent-heat-capacity method for melting.
Output: data/field.bin (uint16, 0.1 °C per unit), data/meta.json (grid, probes).
"""
import json
import os

import numpy as np

# --- geometry (m) -----------------------------------------------------------
W, D, H = 2.0e-3, 3.2e-3, 100.0e-3
STRIP = 10.0e-3                       # enamel stripped length at top
dx = dy = 0.2e-3
dz = 0.5e-3
nx, ny, nz = round(W / dx), round(D / dy), round(H / dz)   # 10, 16, 200

# --- copper (C11000) --------------------------------------------------------
k, rho, cp = 390.0, 8940.0, 385.0
T_SOL, T_LIQ, L = 1075.0, 1095.0, 205e3   # mushy range for latent heat
T0 = 25.0
h_bare, h_enamel = 25.0, 15.0             # natural convection W/m²K

# --- weld source ------------------------------------------------------------
P_ABS = 850.0         # absorbed power, W (e.g. ~2.5 kW fibre laser, ~35 % absorbed)
SIGMA = 0.55e-3       # Gaussian radius
T_ON = 0.12           # pulse duration, s
T_END = 3.0           # total simulated time, s

alpha = k / (rho * cp)
dt = 0.4 / (alpha * (2 / dx**2 + 2 / dy**2 + 2 / dz**2))

xc = (np.arange(nx) + 0.5) * dx - W / 2
yc = (np.arange(ny) + 0.5) * dy - D / 2
zc = (np.arange(nz) + 0.5) * dz
X, Y = np.meshgrid(xc, yc, indexing="ij")
g = np.exp(-(X**2 + Y**2) / (2 * SIGMA**2))
q_top = P_ABS * g / (g.sum() * dx * dy)            # W/m², integrates to P_ABS

h_z = np.where(zc > H - STRIP, h_bare, h_enamel)   # side convection by zone

T = np.full((nx, ny, nz), T0)


def cp_eff(T):
    c = np.full_like(T, cp)
    mush = (T > T_SOL) & (T < T_LIQ)
    c[mush] += L / (T_LIQ - T_SOL)
    return c


# sample times: dense during the pulse, sparser while cooling
samples = np.concatenate([np.linspace(0, T_ON, 37), np.linspace(T_ON, T_END, 61)[1:]])
frames, probes = [], {"t": [], "Tmax": [], "T_edge": [], "T_80": [], "melt_depth_mm": []}
iz_edge = int((H - STRIP) / dz)
iz_80 = int(80e-3 / dz)

t, si = 0.0, 0
while si < len(samples):
    if t >= samples[si] - 1e-12:
        frames.append(np.clip(T * 10, 0, 65535).astype(np.uint16))
        probes["t"].append(round(t, 4))
        probes["Tmax"].append(round(float(T.max()), 1))
        probes["T_edge"].append(round(float(T[:, :, iz_edge].max()), 1))
        probes["T_80"].append(round(float(T[:, :, iz_80].max()), 1))
        molten = np.where((T[nx // 2, ny // 2, :] > T_SOL))[0]
        probes["melt_depth_mm"].append(round(float((nz - molten.min()) * dz * 1e3), 2) if len(molten) else 0.0)
        si += 1
        continue

    Tp = np.pad(T, 1, mode="edge")                 # zero-gradient ghost cells
    lap = ((Tp[2:, 1:-1, 1:-1] - 2 * T + Tp[:-2, 1:-1, 1:-1]) / dx**2
           + (Tp[1:-1, 2:, 1:-1] - 2 * T + Tp[1:-1, :-2, 1:-1]) / dy**2
           + (Tp[1:-1, 1:-1, 2:] - 2 * T + Tp[1:-1, 1:-1, :-2]) / dz**2)
    src = np.zeros_like(T)
    # side convection
    src[0, :, :] -= h_z * (T[0, :, :] - T0) / dx
    src[-1, :, :] -= h_z * (T[-1, :, :] - T0) / dx
    src[:, 0, :] -= h_z * (T[:, 0, :] - T0) / dy
    src[:, -1, :] -= h_z * (T[:, -1, :] - T0) / dy
    # top face: weld flux while on, else convection
    if t < T_ON:
        src[:, :, -1] += q_top / dz
    src[:, :, -1] -= h_bare * (T[:, :, -1] - T0) / dz
    T = T + dt * (k * lap + src) / (rho * cp_eff(T))
    T[:, :, 0] = T0                                # bottom end clamped in fixture
    t += dt

os.makedirs("data", exist_ok=True)
np.stack(frames).tofile("data/field.bin")
meta = dict(nx=nx, ny=ny, nz=nz, W=W * 1e3, D=D * 1e3, H=H * 1e3, strip=STRIP * 1e3,
            P_abs=P_ABS, sigma_mm=SIGMA * 1e3, t_on=T_ON, dt=dt, frames=len(frames),
            Tmax_overall=max(probes["Tmax"]), T_edge_max=max(probes["T_edge"]),
            T_80_max=max(probes["T_80"]), melt_depth_max=max(probes["melt_depth_mm"]),
            probes=probes)
json.dump(meta, open("data/meta.json", "w"))
print({k_: v for k_, v in meta.items() if k_ != "probes"})
