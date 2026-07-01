"""State-code definitions for the flat-wire (hairpin) stator montage process table.

Each montage process step is described by four independent coded states:

1. rotation  (产品旋转状态)
     0    -> 0深   stator normal / upright
     1    -> 180深  stator rotated 180 degree
     0.5  -> 45深   stator flipped / tilted 45 degree
     -0.5 -> 135深  stator tilted 135 degree

2. clamp     (夹持状态)
     "00" -> 两指(双爪)外夹, 夹持方向与 stator 轴向垂直 (横向卡住外径)
     "11" -> 三爪内夹, 爪与 stator 轴向平行, 且与中心线同轴
     "10" -> 三爪外夹, 爪与 stator 轴向平行, 且与中心线同轴

3. temperature (温度状态)
     any number (deg C). > 40 -> rendered red, <= 40 -> rendered blue.

4. laser_mark  (激光打标方向, 顶部带横线的箭头符号)
     0 -> 标记在 stator 上部
     1 -> 标记在 stator 下部
"""

from dataclasses import dataclass

ROTATION_ANGLES = {0: 0, 0.5: 45, 1: 180, -0.5: 135}

ROTATION_LABELS = {
    0: "0 正常",
    0.5: "45 翻转倾斜",
    1: "180 旋转",
    -0.5: "135 倾斜",
}

CLAMP_LABELS = {
    "00": "双爪外夹\n(横向/垂直轴向)",
    "11": "三爪内夹\n(纵向/同轴)",
    "10": "三爪外夹\n(纵向/同轴)",
}

LASER_MARK_LABELS = {
    0: "标记于上部",
    1: "标记于下部",
}

TEMP_THRESHOLD = 40.0


@dataclass
class ProcessStep:
    op: str            # 工序编号, e.g. "OP010"
    name: str          # 工序名称, e.g. "上下料"
    rotation: float     # one of ROTATION_ANGLES keys
    clamp: str          # one of CLAMP_LABELS keys
    temperature: float  # deg C
    laser_mark: int     # 0 or 1

    def validate(self):
        if self.rotation not in ROTATION_ANGLES:
            raise ValueError(f"{self.op}: invalid rotation code {self.rotation!r}, "
                              f"must be one of {list(ROTATION_ANGLES)}")
        if self.clamp not in CLAMP_LABELS:
            raise ValueError(f"{self.op}: invalid clamp code {self.clamp!r}, "
                              f"must be one of {list(CLAMP_LABELS)}")
        if self.laser_mark not in (0, 1):
            raise ValueError(f"{self.op}: invalid laser_mark code {self.laser_mark!r}, "
                              f"must be 0 or 1")


def load_processes_csv(path):
    import csv
    steps = []
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            step = ProcessStep(
                op=row["op"].strip(),
                name=row["name"].strip(),
                rotation=float(row["rotation"]),
                clamp=row["clamp"].strip(),
                temperature=float(row["temperature"]),
                laser_mark=int(row["laser_mark"]),
            )
            step.validate()
            steps.append(step)
    return steps
