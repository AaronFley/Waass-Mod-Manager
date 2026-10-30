"""数据结构：一个 mod 在程序里长什么样。"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict


@dataclass
class ModRecord:
    # 身份
    key: str                      # 稳定标识，等于 mods.settings 里的段落名，如 modHoods
    name: str                     # 显示名，优先用 Nexus 上的标题
    folder: str                   # 磁盘上的实际文件夹名（禁用时可能带 ~ 前缀）
    enabled: bool = True
    priority: int | None = None   # mods.settings 里的 Priority，None 表示没写

    # Nexus
    nexus_id: str | None = None
    nexus_url: str = ""           # 页面地址（有 ID 就有）
    id_src: str = ""              # ID 从哪来：manual / legacy / readme / package
    author: str = ""
    category: str = ""            # Nexus 分类，如 Combat / User Interface
    version: str = ""             # 本地版本（能认出来就填）
    version_src: str = ""         # 版本从哪来：manual/info/manifest/legacy/archive/folder
    version_file: str = ""        # 扫描时磁盘上哪个文件给出的版本：info / txt / 空
    latest_version: str = ""      # Nexus 上主文件（MAIN）的版本 —— 「最新版」以它为准
    latest_at: str = ""           # 那个版本是什么时候的
    latest_src: str = ""          # 最新版本从哪来：nexus（刚查到）/ legacy（历史记录）
    latest_file: str = ""         # 主文件名，方便你去下载页对号
    page_version: str = ""        # Nexus 页面上作者手填的 version，仅作对照（常落后）
    name_nexus: str = ""          # Nexus 官方标题（可能比文件夹名长很多）
    summary: str = ""             # Nexus 简介
    downloads: int = 0            # Nexus 下载量，仅作参考
    update_available: bool = False
    track_state: str = ""         # new / current / unknown / unchecked
    thumbnail: str = ""           # 相对 data/ 的图片路径

    # 内容构成
    has_scripts: bool = False
    has_dlc: bool = False
    menu_xmls: list[str] = field(default_factory=list)      # 游戏 bin/config 下属于它的菜单 xml
    disabled_menu_xmls: list[str] = field(default_factory=list)  # 已被改名为 .xml.disabled 的
    bundled_menu_xmls: list[str] = field(default_factory=list)  # mod 文件夹内自带的菜单 xml
    bin_files: list[str] = field(default_factory=list)      # 写进 bin/x64_dx12 的文件
    w3strings: list[str] = field(default_factory=list)      # 本地化文件（汉化要用）
    dlc_dirs: list[str] = field(default_factory=list)

    # 统计
    size_bytes: int = 0
    file_count: int = 0
    mtime: float = 0.0

    # 归属
    source: str = "scan"          # scan / legacy / manual
    groups: list[str] = field(default_factory=list)   # 用户自定义分组（M5）
    notes: str = ""

    # AI 中文名（M5.5，存 data/names.json，不写进游戏目录）
    name_cn: str = ""
    note_cn: str = ""

    # Script Merger 的合并产物（如 mod0000_MergedFiles）。它不是用户装的 mod，
    # 列表里不显示；冲突判定仍然需要它（合订本是「裁判」）。
    is_merged: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Warning:
    """扫描过程中发现的不一致。这类信息会显示在界面上，不静默吞掉。"""
    level: str      # info / warn / error
    code: str       # 机器可读的短码
    message: str
    target: str = ""  # 相关的文件夹或文件名


@dataclass
class ScanResult:
    game_root: str
    user_config: str
    mods: list[ModRecord] = field(default_factory=list)
    warnings: list[Warning] = field(default_factory=list)
    orphan_dlc: list[str] = field(default_factory=list)   # 没有对应 mod 的 dlc 文件夹
    orphan_menu: list[str] = field(default_factory=list)  # 没有归属的菜单 xml
    settings_entries: int = 0
    scanned_at: str = ""
    # 合订本（Script Merger 的合并产物）。它不是用户装的 mod，但界面要能看见它：
    # merged_state 的结果 + 台账明细。见 core/merger.py 的 merged_state / detail。
    merged: dict = field(default_factory=dict)

    def visible_mods(self) -> list["ModRecord"]:
        """给界面看的 mod：合并产物不算 mod，不出现在「全部模组」里。

        合并产物单独走 `merged` 段，界面上有专属的「已合并」分类。
        """
        return [m for m in self.mods if not m.is_merged]

    def merged_mods(self) -> list["ModRecord"]:
        return [m for m in self.mods if m.is_merged]

    def to_dict(self) -> dict:
        return {
            "game_root": self.game_root,
            "user_config": self.user_config,
            "mods": [m.to_dict() for m in self.visible_mods()],
            "merged": self.merged,
            "warnings": [asdict(w) for w in self.warnings],
            "orphan_dlc": self.orphan_dlc,
            "orphan_menu": self.orphan_menu,
            "settings_entries": self.settings_entries,
            "scanned_at": self.scanned_at,
        }
