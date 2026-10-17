# 两根扁铜线激光焊接热仿真 · 竖屏短视频

成片（30 fps，42 s，带原创背景音乐）：
- `copper_weld_sim.mp4`：手机竖屏 1080×1920
- `copper_weld_sim_laptop.mp4`：电脑横屏 1920×1080，画面装在 ThinkPad 风格的笔记本外壳里（黑色磨砂 A 面、摄像头遮挡片、红色指点杆、红条三键；没有放品牌标志）

## 分镜

| 时间 | 画面 |
|---|---|
| 0–5 s | Claude Code 终端：一句话任务 → 推理强度拉到 **max**（彩虹动画）→ 思考 → 写模型、跑仿真 |
| 5–17 s | 仿 SolidWorks：上视基准面画两个 2×3.2 矩形 → 凸台-拉伸 100（PropertyManager）→ 材质铜 → 分割线剥漆 10 mm |
| 17–23 s | 仿 Mechanical：划网格 64,000 节点 → 热通量 / 对流 / 夹具温度边界 → 求解进度 → 分段彩色云图 |
| 23–31 s | 温度 · 环芯激光焊接：光束沿椭圆轨迹扫描（C1→C2→C3，显示芯/环功率和圈数）、钥匙孔开口跟随光斑、熔池波纹、火花与蒸汽羽流、焊帽长出、漆皮边界温度、温度曲线 |
| 31–36 s | 熔池 · 钥匙孔中截面（参照论文 Fig. 6）：激光束、金属蒸汽羽流、向后倾斜的钥匙孔（蒸汽毛细管）、熔池、熔合线 → 焊缝、扫描方向、熔深 / 熔宽 |
| 36–42 s | 焊好的铜线展示（焊帽完整盖住两根线端面和四个角）+ 结论卡片 |
| 全程 | Claude Code 像素小人：被拉伸顶上天、戴安全帽等求解、戴护目镜躲激光、热到冒汗、跳伞落到焊珠上撒花 |

## 仿真模型（`sim.py`）

工艺参数参考 Shaikh, *Remote laser welding of copper X-pins and busbar-to-pin joint*（LUT 硕士论文，2026）：TRUMPF TruFiber 6000 P，1070 nm 环芯光纤激光（芯 71 µm / 环 337 µm），振镜椭圆扫描。

- 几何：两根 2.0×3.2×100 mm 无氧铜线，3.2 mm 面贴合并排（端面 4.0×3.2），顶部 10 mm 剥漆
- 网格：20×16×400 = 128,000 节点（0.2×0.2×0.25 mm），显式差分，Δt = 27 μs，算到 3.0 s
- 移动热源：椭圆扫描 300 mm/s，三个周期（论文 Table 8）
  - C1：芯 2000 W + 环 4000 W，3 圈，椭圆 3.4×2.4 mm
  - C2：芯 500 W + 环 3500 W，3 圈，椭圆 3.8×2.8 mm
  - C3：芯 40 W + 环 3500 W，2 圈（导热焊，抚平表面）
- 吸收率（1 µm）：固态铜 5 %，液态 14 %，钥匙孔内多次反射约 60 %
- 钥匙孔：71 µm 的芯斑远小于网格，所以按芯部功率密度触发（> 100 kW/mm²：C1 ≈ 505，C2 ≈ 126，C3 ≈ 10 → 导热模式）；深度随芯部功率变化，芯部吸收能量的 75 % 沿钥匙孔体积沉积
- 熔化潜热（等效热容），温度上限为沸点 2562 ℃；两根线之间按接触热导 3×10⁴ W/m²K，熔化后视为熔合
- 底端夹具 25 ℃，侧面自然对流

结果：输入能量 1.21 kJ，吸收 0.28 kJ（约 23 %），焊接周期 0.28 s；钥匙孔 1.6 mm（C1）/ 0.6 mm（C2），熔深 1.5 mm，熔宽 4.0 mm（两根全熔合）；**漆皮边界（距顶 10 mm）峰值 346 ℃（t≈0.58 s）**，实际工艺要关注漆膜受热。

动画里的焊帽按论文对"良好焊点"的描述来做：对称的球状熔合区，略大于两根线的合成截面，盖住整个端面和四个角。

简化：不算熔池流体力学和蒸发反冲；熔池回流箭头是示意。截面视图里的钥匙孔是把当前光斑位置投影到中截面上画的。

## 复现

```bash
python3 sim.py            # → data/field.bin, data/meta.json（约 2 分钟）
python3 music.py          # → data/music.wav, data/spectrum.json
npm i                     # three@0.160.0
NODE_PATH=$(npm root -g) node record.cjs 9 25 33   # 预览单帧 → preview/
NODE_PATH=$(npm root -g) node record.cjs           # 逐帧渲染 → frames/
ffmpeg -framerate 30 -i frames/f%04d.jpg -i data/music.wav -c:v libx264 -pix_fmt yuv420p \
  -crf 19 -c:a aac -b:a 192k -shortest copper_weld_sim.mp4

# 电脑版：横屏 1600×1000 画面 → 套笔记本外壳
NODE_PATH=$(npm root -g) node frame.cjs                    # laptop.html → laptop.png + glare.png
MODE=pc NODE_PATH=$(npm root -g) node record.cjs           # → frames_pc/
ffmpeg -loop 1 -framerate 30 -i laptop.png -framerate 30 -i frames_pc/f%04d.jpg -loop 1 -framerate 30 -i glare.png -i data/music.wav \
  -filter_complex "[1]scale=1376:860:flags=lanczos[s];[0][s]overlay=272:44:shortest=1[a];[a][2]overlay=shortest=1,format=yuv420p[v]" \
  -map "[v]" -map 3:a -t 42 -c:v libx264 -crf 19 -c:a aac -b:a 192k copper_weld_sim_laptop.mp4
```

改参数：功率、光斑、钥匙孔深度、脉冲时长在 `sim.py` 顶部；分镜时间在 `scene.html` 的 `S`，镜头在 `KEYS`，小人台词在 `BUB`，小人动作在 `mascotPose()`。

## 背景音乐

`music.py` 用代码合成的原创曲：尤克里里扫弦 + 响指前奏，接口哨主旋律、鼓、贝斯、钢片琴，C–G–Am–F，120 BPM；转场处加了 whoosh，拉到 max 时有一串闪亮音。
风格接近常见的 "positive" 欢快尤克里里配乐，但不是任何现成曲目。想换成正版曲库里的原曲，替换 `data/music.wav` 后重跑最后一步 ffmpeg 即可。

界面只是"仿"SolidWorks / Mechanical 的布局风格，没有用任何官方标志。
