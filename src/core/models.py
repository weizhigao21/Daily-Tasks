# -*- coding: utf-8 -*-
"""任务数据模型。"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

TASK_DAILY = "daily"
TASK_WEEKLY = "weekly"
TASK_ONCE = "once"
TASK_TYPES = (TASK_DAILY, TASK_WEEKLY, TASK_ONCE)

VERIFY_MANUAL = "manual"
VERIFY_IMAGE = "image"
VERIFY_MODES = (VERIFY_MANUAL, VERIFY_IMAGE)

TASK_TYPE_NAMES = {
    TASK_DAILY: "每日",
    TASK_WEEKLY: "每周",
    TASK_ONCE: "一次性",
}


@dataclass
class Task:
    id: int | None = None
    name: str = ""
    task_type: str = TASK_DAILY
    verify_mode: str = VERIFY_MANUAL
    # 屏幕区域（物理像素）: (x, y, w, h)，manual 模式为 None
    region: tuple[int, int, int, int] | None = None
    template_path: str = ""          # 目标状态模板图路径
    threshold: float = 0.85          # 模板匹配相似度阈值
    time_start: str = ""             # "06:00"，空 = 不限
    time_end: str = ""               # "12:00"，空 = 不限
    enabled: bool = True
    created_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))

    @property
    def type_name(self) -> str:
        return TASK_TYPE_NAMES.get(self.task_type, self.task_type)

    @property
    def is_image_verify(self) -> bool:
        return self.verify_mode == VERIFY_IMAGE

    def validate(self) -> list[str]:
        """返回错误列表，空列表表示通过。"""
        errors: list[str] = []
        if not self.name.strip():
            errors.append("任务名称不能为空")
        if self.task_type not in TASK_TYPES:
            errors.append(f"未知任务类型: {self.task_type}")
        if self.verify_mode not in VERIFY_MODES:
            errors.append(f"未知验证方式: {self.verify_mode}")
        if self.is_image_verify:
            if not self.region or len(self.region) != 4:
                errors.append("图片验证必须设置屏幕区域")
            elif any(v <= 0 for v in self.region[2:]):
                errors.append("屏幕区域宽高必须大于 0")
            if not self.template_path:
                errors.append("图片验证必须截取目标模板图")
        if self.time_start and self.time_end and self.time_start >= self.time_end:
            errors.append("时间窗口起点必须早于终点")
        return errors
