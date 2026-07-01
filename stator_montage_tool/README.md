# 扁线定子 Montage Process 状态编号工具

用数字编码描述定子(hairpin/扁线定子)组装线上每一道工序的四个状态维度，并批量渲染成
一张"工序 - 图标"对照图面（黑底金/灰配色，风格参照产线图库）。

## 状态编码定义 (`states.py`)

| 维度 | 字段 | 取值 | 含义 |
|---|---|---|---|
| 产品旋转状态 | `rotation` | `0` | 正常状态（stator.svg 默认方向） |
| | | `1` | 旋转 180° |
| | | `0.5` | 翻转 / 倾斜 45° |
| | | `-0.5` | 倾斜 135° |
| 夹持状态 | `clamp` | `00` | 双爪外夹（横向，夹持方向与定子轴向垂直） |
| | | `11` | 三爪内夹（与定子轴向平行，且与中心线同轴） |
| | | `10` | 三爪外夹（与定子轴向平行，且与中心线同轴） |
| 温度状态 | `temperature` | 任意数值 (°C) | > 40°C 显示红色水银柱，其余显示蓝色 |
| 激光标记方向 | `laser_mark` | `0` | 标记于定子上部（顶部带横线的箭头指向定子顶面） |
| | | `1` | 标记于定子下部（箭头指向定子底面） |

## 图标风格

- 定子本体：金色顶盖 + 灰色叠片铁芯 + 金色引脚齿，黑色背景，与产线图库配色一致
- 夹持状态：蓝色线条爪指图标；`00` 为两爪侧向夹持图，`11`/`10` 为三爪顶视图
  （虚线圆代表定子内径/外径，`11` 爪在圆内侧，`10` 爪在圆外侧）
- 产品旋转状态：定子本体图标按角度旋转，非 0° 时附加蓝色旋转箭头
- 温度状态：白色描边温度计，水银柱按阈值变红/变蓝，下方标注实际数值
- 激光标记方向：灰色定子侧视剪影 + 顶部带横线的箭头，箭头位于上方指下（0）
  或位于下方指上（1）

## 使用方法

```bash
pip install matplotlib numpy
# 使用默认示例表 processes_example.csv
python3 -m stator_montage_tool.generate_sheet
# 或指定自己的工序表和输出路径
python3 -m stator_montage_tool.generate_sheet my_processes.csv out.png
```

`processes_example.csv` 列：`op,name,rotation,clamp,temperature,laser_mark`。
其中的工序名称沿用原图库素材命名（Paper insertion / Pin insertion / Trimming /
Twisting / Widening / Forming cutting / Straightening / Pin forming /
Laser Welding / Tig / Powder coating / preheating / Gel / Cure / Cooling /
Tricking / Press / PDIV / EOL），**具体的旋转 / 夹持 / 温度 / 激光标记数值均为
占位示例**，请根据实际工艺参数编辑 CSV 后重新生成。
