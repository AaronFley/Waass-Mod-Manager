"""mods.settings 的读写。

格式极其苛刻，因为 Script Merger 的解析器不支持注释：
    [段落名]
    Enabled=1
    Priority=10
    空行
    [段落名]
    ...

硬规则（写错会让 Script Merger 崩 NullReferenceException）：
1. 文件第一个字符必须是 [
2. 每个段落只有三行：名字 / Enabled / Priority
3. 段落之间一个空行
4. 不允许出现任何注释、标题、说明文字
5. 不要 BOM
"""
from __future__ import annotations

import re
from pathlib import Path

_SECTION_RE = re.compile(r"^\[(.+?)\]\s*$")


class SettingsFile:
    """把 mods.settings 读成有序段落，写回时严格按游戏/Script Merger 能吃的格式。"""

    def __init__(self, path: Path):
        self.path = path
        self.sections: list[dict] = []   # [{"name":..., "enabled":bool, "priority":int|None}]

    # ---------- 读 ----------
    @classmethod
    def load(cls, path: Path) -> "SettingsFile":
        obj = cls(path)
        if not path.is_file():
            return obj
        raw = path.read_bytes()
        text = raw.decode("utf-8-sig", errors="replace")
        cur = None
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith(";"):
                continue
            m = _SECTION_RE.match(line)
            if m:
                cur = {"name": m.group(1).strip(), "enabled": True, "priority": None}
                obj.sections.append(cur)
                continue
            if cur is None or "=" not in line:
                continue
            k, _, v = line.partition("=")
            k, v = k.strip().lower(), v.strip()
            if k == "enabled":
                cur["enabled"] = v not in ("0", "false", "False", "")
            elif k == "priority":
                try:
                    cur["priority"] = int(v)
                except ValueError:
                    cur["priority"] = None
        return obj

    # ---------- 查 ----------
    def get(self, name: str) -> dict | None:
        low = name.lower()
        for s in self.sections:
            if s["name"].lower() == low:
                return s
        return None

    def __len__(self) -> int:
        return len(self.sections)

    # ---------- 写 ----------
    def render(self) -> str:
        blocks = []
        for s in self.sections:
            lines = [f"[{s['name']}]", f"Enabled={1 if s['enabled'] else 0}"]
            if s["priority"] is not None:
                lines.append(f"Priority={s['priority']}")
            blocks.append("\n".join(lines))
        return "\n\n".join(blocks) + ("\n" if blocks else "")

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        text = self.render()
        tmp = self.path.with_suffix(self.path.suffix + ".waass-tmp")
        tmp.write_text(text, encoding="utf-8", newline="\n")
        tmp.replace(self.path)

    # ---------- 改 ----------
    def set_state(self, name: str, enabled: bool | None = None,
                  priority: int | None = None, create: bool = True) -> dict | None:
        sec = self.get(name)
        if sec is None:
            if not create:
                return None
            sec = {"name": name, "enabled": True, "priority": None}
            self.sections.append(sec)
        if enabled is not None:
            sec["enabled"] = enabled
        if priority is not None:
            sec["priority"] = priority
        return sec

    def remove(self, name: str) -> bool:
        before = len(self.sections)
        low = name.lower()
        self.sections = [s for s in self.sections if s["name"].lower() != low]
        return len(self.sections) != before

    def bump_priority(self, name: str, value: int) -> None:
        """设置优先级，并把撞车的其他 mod 顺序后移，保证不重复。"""
        sec = self.set_state(name, priority=value)
        if sec is None:
            return
        for other in self.sections:
            if other is sec:
                continue
            if other["priority"] is not None and other["priority"] == value:
                other["priority"] = value + 1

    def next_priority(self) -> int:
        ps = [s["priority"] for s in self.sections if s["priority"] is not None]
        return (max(ps) + 10) if ps else 10
