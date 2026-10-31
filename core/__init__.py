"""Waass 的 Python 包。

包入口只做一件事：**导入 core 下任何模块时，先把控制台切成 UTF-8**
（见 `core/console.py`）。放在这里是因为三个入口都从这里过 —— `run.py`、
`tests/*`、`tools/*` —— 一个地方修好，全部生效，不用每个文件记着调一次。

不这样做的话，英文版 Windows（cp1252 控制台）上源码启动会直接崩在第一条
中文日志上，而本机是中文系统，看不见。
"""
from __future__ import annotations

from .console import enable_utf8 as _enable_utf8

_enable_utf8()
