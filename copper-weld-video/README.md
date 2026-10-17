# 扁铜线剥漆顶面焊接热仿真 · 竖屏短视频

成片：`copper_weld_sim.mp4`（1080×1920，30 fps，32 s，带原创背景音乐）

## 分镜

| 时间 | 内容 |
|---|---|
| 0–8 s | 仿 Claude「Max · 深度思考」界面：输入需求 → 思考动画 → 思考要点逐行出现 |
| 8–17 s | 仿 CAD 建模：草图 2.0×3.2 → 拉伸 100 → 涂聚酰亚胺漆膜 → 顶部剥漆 10 mm |
| 17–28 s | 焊接热仿真：激光脉冲（慢放 ×33）→ 冷却 3 s；温度云图、熔池深度、漆皮边界温度、温度曲线 |
| 28–32 s | 仿真结论卡片 |
| 全程 | Claude 星芒小图标随节拍跳动 + 对白气泡，底部音频频谱条 |

## 仿真模型（`sim.py`）

- 3D 瞬态导热，显式有限差分，网格 10×16×200（0.2×0.2×0.5 mm）
- 无氧铜 C11000：k = 390 W/m·K，ρ = 8940 kg/m³，cp = 385 J/kg·K，熔化潜热 205 kJ/kg（等效热容法）
- 热源：剥漆顶面高斯面热源，吸收功率 850 W，σ = 0.55 mm，脉冲 0.12 s
- 边界：底端夹具 25 ℃，侧面自然对流 15–25 W/m²K
- 结果：顶面峰值 1411 ℃，熔池深约 1.0 mm，漆皮边界峰值 253 ℃（t ≈ 0.7 s），距顶 20 mm 处峰值 137 ℃
- 简化：未计熔池流动、蒸发、漆膜热阻，结果仅作示意

## 复现

```bash
python3 sim.py            # 生成 data/field.bin, data/meta.json（约 1 分钟）
python3 music.py          # 生成 data/music.wav, data/spectrum.json
npm i                     # three@0.160.0
NODE_PATH=$(npm root -g) node record.cjs 18.5 25   # 预览单帧 → preview/
NODE_PATH=$(npm root -g) node record.cjs           # 逐帧渲染 → frames/
ffmpeg -framerate 30 -i frames/f%04d.jpg -i data/music.wav -c:v libx264 -pix_fmt yuv420p \
  -crf 19 -c:a aac -b:a 192k -shortest copper_weld_sim.mp4
```

改参数：功率、光斑、脉冲时长在 `sim.py` 顶部；分镜时间、镜头关键帧、文案在 `scene.html` 的 `KEYS`、`BUBBLES`、`render()` 里。

## 背景音乐

`music.py` 用代码合成的原创曲：尤克里里扫弦 + 响指前奏，接口哨主旋律、鼓、贝斯，最后加钢片琴，C–G–Am–F，120 BPM。
风格接近常见的 "positive" 欢快尤克里里配乐，但不是任何现成曲目，没有版权问题。
如果想换成正版曲库里的原曲，替换 `data/music.wav` 后重新执行最后一步 ffmpeg 即可。
