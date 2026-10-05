# -*- coding: utf-8 -*-
"""隐盾 · 窗口几何计算（与界面框架无关）

从旧 Qt 界面的**极简闪发折叠模式**迁出：窗口折叠成一条浮条、展开时精确还原。
把这些算术放在这里（而不是散在界面代码里）的原因：
  · 纯函数、无框架依赖，可被回归测试直接锁住；
  · 折叠/展开是"窗口几何"这一件事的两半，必须成对演进，写在一起才不会各自漂移。

几何语义（与旧实现一致，用户已习惯）：
  · 折叠：**底边对齐**（浮条贴着原窗口底边）、**水平居中**、宽度取记忆值（不小于 280）；
  · 展开：优先回到折叠前保存的完整几何；没有保存值时保持当前位置、用默认展开尺寸。
"""
from __future__ import annotations

from typing import Optional, Sequence, Tuple

# 展开态默认尺寸（首次运行兜底；实际由用户拖动决定）
EXPANDED_W, EXPANDED_H = 1180, 780
# 折叠态：44px 浮条 + 16px 容器留白（沿用旧实现数值）
MINI_H = 60
MINI_W_DEFAULT = 420
MINI_W_MIN = 280
# 窗口最小尺寸：**必须按折叠态给**（见 webview_app 里的说明）——
# pywebview/WinForms 的 MinimumSize 只在创建窗口时生效，运行期改它不会作用到原生窗口，
# 若按展开态 (880,600) 设，折叠就会被悄悄卡回 880×600（表现为"点了折叠但窗口没变小"）。
MINI_MIN_SIZE = (MINI_W_MIN, MINI_H)
# 展开态的最小尺寸：无边框模式下由前端边缘热区 + 这里的 resize_edge 兜底执行
# （系统不会替我们拦，因为窗口没有原生边框；所以拖动改尺寸时由代码保证不小于这个值）。
EXPANDED_MIN_SIZE = (880, 600)


def collapse_to_mini(x: int, y: int, w: int, h: int,
                     mini_w: Optional[int] = None) -> Tuple[int, int, int, int]:
    """计算折叠后的 (x, y, w, h)。"""
    width = max(MINI_W_MIN, int(mini_w or MINI_W_DEFAULT))
    new_x = int(x + (w - width) // 2)
    new_y = int(y + h - MINI_H)
    return new_x, new_y, width, MINI_H


def restore_from_mini(saved: Optional[Sequence[int]],
                      x: int, y: int) -> Tuple[int, int, int, int]:
    """计算展开后的 (x, y, w, h)：优先精确还原折叠前的几何。"""
    if saved is not None and len(saved) == 4:
        sx, sy, sw, sh = (int(v) for v in saved)
        if sw > 0 and sh > 0:
            return sx, sy, sw, sh
    return int(x), int(y), EXPANDED_W, EXPANDED_H


def clamp_geometry(x: int, y: int, w: int, h: int) -> Tuple[int, int, int, int]:
    """兜底清洗：尺寸异常（0/负数）时退回默认展开尺寸，避免窗口"消失"。"""
    if w <= 0 or h <= 0:
        return int(x), int(y), EXPANDED_W, EXPANDED_H
    return int(x), int(y), int(w), int(h)


def resize_edge(edge: str, x: int, y: int, w: int, h: int,
                dx: int, dy: int,
                min_w: int = EXPANDED_MIN_SIZE[0],
                min_h: int = EXPANDED_MIN_SIZE[1]) -> Tuple[int, int, int, int]:
    """边缘拖拽改尺寸：在**拖拽起点**几何 (x,y,w,h) 上按方向边（n/s/w/e 及组合）
    应用累计位移 (dx,dy)，并保证不小于 min_w×min_h，且**对侧边保持钉住**
    （拖下边时顶边不动、拖左边时右边不动——与原生窗口手感一致）。

    以起点为基准（而不是"当前值+增量"）计算并应用，可避免 DPI 取整误差在连续
    拖帧中累积漂移；每次 pointermove 重发总位移，结果幂等。"""
    e = str(edge or "").lower()
    nx, ny, nw, nh = int(x), int(y), int(w), int(h)
    if "w" in e:
        nx += int(dx); nw -= int(dx)
    if "e" in e:
        nw += int(dx)
    if "n" in e:
        ny += int(dy); nh -= int(dy)
    if "s" in e:
        nh += int(dy)
    min_w, min_h = int(min_w), int(min_h)
    if nw < min_w:
        if "w" in e:
            nx = int(x) + int(w) - min_w   # 对侧锚定：左边收缩到下限后不再左移
        nw = min_w
    if nh < min_h:
        if "n" in e:
            ny = int(y) + int(h) - min_h
        nh = min_h
    return nx, ny, nw, nh
