"""读 Script Merger 的合并台账 —— 只读，不改、不删、不替它合并。

巫师3 里两个 mod 改同一个 .ws，只有一份能生效。Script Merger 的解法是
把冲突的那份合成一个「合订本」，放到 mods/mod0000_MergedFiles 里：

    mods/mod0000_MergedFiles/content/scripts/game/player/playerWitcher.ws
        = modAutoLoot 的同名文件 + modMoreSkillSlots 的改动

巫师3 是「Priority 数字小的先加载、先加载的赢」，所以合订本必须排在第一位。
`0000` 前缀只在**没有 mods.settings 条目**时有效（那时按文件夹名字母序，`0000`
天然排第一）。一旦任何 mod 有了条目，Priority 就接管排序，没登记的 mod 会被
排到后面 —— 合订本的 `0000` 当场失效，源 mod 反把合订本盖掉，游戏报
「Could not find function XXX」。2026-10-27 的事故就是这么来的。

所以合订本必须在 mods.settings 里有条目，且 Priority 严格小于所有其他 mod
（社区标准写法是 Priority = 1，取值范围 1–9999）。本模块的
`merged_state()` / `ensure_registered()` 就是守这条规矩的。

它把每次合并记在 <游戏根目录>/Merger/MergeInventory.xml：

    <Merge>
      <RelativePath>game\\player\\playerWitcher.ws</RelativePath>
      <IncludedMod Hash="C147EDE5" ModIoId="0">modMoreSkillSlots</IncludedMod>
      <IncludedMod Hash="7C11A052" ModIoId="0">modAutoLoot</IncludedMod>
      <MergedModName>mod0000_MergedFiles</MergedModName>
    </Merge>

本模块的作用只有三件：
1. 让冲突检测知道「这几个同名脚本已经被合并过」，别再报成待处理；
2. 让界面能一键打开 Script Merger（真正的合并由它自己做，我们只启动）；
3. 台账里记着的 mod 如果已经被卸载，合订本里会留下引用已消失类的代码，
   游戏启动就报错 —— 这个坑没人盯着就发现不了，所以主动查出来。

这些都是只读操作。唯一会碰游戏目录的动作是「启动 Script Merger」，
而那是用户点按钮才发生的，改文件是它自己干的活。
"""
from __future__ import annotations

import os
import time
import xml.etree.ElementTree as ET
from pathlib import Path

from core import i18n

# Script Merger 的目录名是写死的
MERGER_DIR = "Merger"
EXE_NAMES = ("WitcherScriptMerger.exe", "ScriptMerger.exe")
INVENTORY = "MergeInventory.xml"
SETTINGS = "ScriptMergerRemastered.settings.xml"

# 找不到台账时的默认产物名（和 Script Merger 出厂设置一致）
DEFAULT_MERGED = "mod0000_MergedFiles"


def configured_exe() -> str:
    """配置里记着的路径（不管现在还在不在）。

    界面要靠它区分「从来没装过」和「你指定过、但那个文件没了」——
    后者只说「没检测到」，用户会以为程序把他的设置忘了。
    """
    try:
        from .paths import load_config
        return str(load_config().get("merger_exe") or "").strip()
    except (OSError, ValueError):
        return ""


def _custom_exe() -> Path | None:
    """用户手动指定过的 Script Merger 主程序（存在才认）。"""
    raw = configured_exe()
    if not raw:
        return None
    p = Path(raw)
    return p if p.is_file() else None


# ---------------- 自动发现 ----------------
# Script Merger 是绿色程序：官方和教程都是「解压到任意目录，运行 exe，
# 在它自己的界面里选游戏目录」。所以它装在哪没有任何注册表或固定路径可查。
# 1.3.x 及以前只查 `<游戏根目录>/Merger/`，等于假定用户按整合包的目录结构摆，
# 新用户照教程解压到桌面就永远「没检测到」。
#
# 现在的做法：在几个**一定会看的地方**扫一两层，扫不到就算了。
# 为什么不搜全盘：一分钟起步，而且盘上常年躺着几个废弃副本，
# 找到的那个未必是用户在用的那个 —— 与其猜错，不如让他指一次，指完就记住。
_SCAN_TTL = 120.0
_SCAN: dict[str, tuple[float, Path | None]] = {}

# 这些目录下面不可能放合并器，跳过能省掉大半时间（mods 里动辄上百个子目录）
_SKIP_DIRS = {
    "mods", "dlc", "content", "bin", "r4game", "textures", "movies",
    "node_modules", "__pycache__", "$recycle.bin", "system volume information",
}
# 候选目录封顶。上一层要是「所有游戏都放这儿」的大目录，两层全展开会到几百个，
# Windows 上几百次列目录就是一秒多。够用就行 —— 找不到还有「选择位置」。
_MAX_DIRS = 150


def _looks_like_merger(name: str) -> bool:
    """文件名像不像合并器。不做严格匹配 —— 汉化版、改名版都叫得乱七八糟。"""
    return _name_score(name) < 9


def _name_score(name: str) -> int:
    """越像正品分越低。用来在几个候选里挑，不是用来排除。

    `NotAMergerApp.exe` 这种也含 merger，但一个装在别处、叫得又不那么像的
    程序，不该盖过真正叫 ScriptMerger 的那个。
    """
    if name in EXE_NAMES:                    # WitcherScriptMerger.exe / ScriptMerger.exe
        return 0
    n = name.lower()
    if not n.endswith(".exe"):
        return 9
    if "scriptmerger" in n or "script_merger" in n:
        return 1
    if n.startswith("merger") or "merger" in n:
        return 2
    return 9


def _under(p: Path, root: Path) -> bool:
    try:
        Path(p).resolve().relative_to(Path(root).resolve())
        return True
    except (ValueError, OSError):
        return False


def _config_says(exe: Path, root: Path) -> bool:
    """这个副本是不是为当前这个游戏配置过。

    Script Merger 把游戏路径写在同目录的 `*.exe.config` / `*.dll.config` 里。
    读得到并且指向当前游戏，那基本就是用户在用的那个，而不是硬盘上的旧副本。
    读不出来只当没这条证据，不影响别的判断。
    """
    target = str(root).replace("/", "\\").rstrip("\\").lower()
    if not target:
        return False
    try:
        for f in exe.parent.glob("*.config"):
            try:
                if f.stat().st_size > 1024 * 1024:
                    continue
                raw = f.read_bytes()
            except OSError:
                continue
            for enc in ("utf-8", "utf-16", "mbcs"):
                try:
                    txt = raw.decode(enc, errors="ignore")
                except (LookupError, UnicodeError):
                    continue
                if txt and target in txt.replace("\\\\", "\\").replace("/", "\\").lower():
                    return True
    except OSError:
        pass
    return False


def _scan_dirs(root: Path) -> list[Path]:
    """要列哪几个目录。每个目录只看自己这一层，不往里递归。

    覆盖的摆放习惯：游戏目录里、游戏目录里自建的工具文件夹、和游戏并排的
    工具文件夹、桌面、下载。总量封顶，别让「上一层是个装满游戏的大目录」
    把扫描拖成几秒钟。
    """
    out: list[Path] = []

    def add(d: Path | None, depth: int) -> None:
        if d is None or len(out) >= _MAX_DIRS:
            return
        try:
            if not d.is_dir() or d in out:
                return
        except OSError:
            return
        out.append(d)
        if depth <= 0:
            return
        try:
            subs = sorted(d.iterdir(), key=lambda p: p.name.lower())
        except OSError:
            return
        for sub in subs:
            if len(out) >= _MAX_DIRS:
                return
            try:
                if not sub.is_dir() or sub.name.lower() in _SKIP_DIRS:
                    continue
            except OSError:
                continue
            add(sub, depth - 1)

    root = Path(root)
    add(root, 1)                         # 游戏根目录 + 它的子目录（Merger\ 在这）
    add(root.parent, 2)                  # 隔壁：Games\Merger、Games\Tools\xxx
    try:
        add(root.parent.parent, 0)       # 再上一层本身
    except (OSError, ValueError):
        pass

    home = Path.home()
    add(home / "Desktop", 1)
    add(home / "OneDrive" / "Desktop", 1)
    add(home / "Downloads", 1)
    return out


def _cache_key(root: Path) -> str:
    try:
        return str(Path(root).resolve()).lower()
    except OSError:
        return str(root).lower()


def forget_discovery() -> None:
    """记住/清除过路径之后叫一次，下次重新找。"""
    _SCAN.clear()


def _discover(root: Path) -> Path | None:
    """猜 Script Merger 在哪。找不到返回 None，永远不抛异常。"""
    key = _cache_key(root)
    now = time.time()
    hit = _SCAN.get(key)
    if hit and now - hit[0] < _SCAN_TTL:
        return hit[1]

    ranked: list[tuple[tuple[int, ...], Path]] = []
    for rank, d in enumerate(_scan_dirs(root)):
        try:
            entries = list(d.iterdir())
        except OSError:
            continue
        for p in entries:
            if not _looks_like_merger(p.name):
                continue
            try:
                if not p.is_file():
                    continue
            except OSError:
                continue
            # 排序依据（越小越优先）：先看是不是在游戏目录里，再看它的
            # config 认不认这个游戏，再看文件名像不像正品，最后看谁先被扫到。
            ranked.append(((0 if _under(p, root) else 1,
                            0 if _config_says(p, root) else 1,
                            _name_score(p.name),
                            rank), p))

    best: Path | None = None
    if ranked:
        ranked.sort(key=lambda x: x[0])
        best = ranked[0][1]
    _SCAN[key] = (now, best)
    return best


def set_exe(path: str | Path) -> Path:
    """记住「装在别处的 Script Merger」。下次启动直接用它，不用再选一遍。"""
    from .paths import load_config, save_config

    p = Path(str(path)).expanduser()
    if not p.is_file():
        raise FileNotFoundError(i18n.t("mrg.exe_missing", path=p))
    cfg = load_config()
    cfg["merger_exe"] = str(p)
    save_config(cfg)
    forget_discovery()
    return p


def clear_exe() -> None:
    from .paths import load_config, save_config

    cfg = load_config()
    if cfg.pop("merger_exe", None) is not None:
        save_config(cfg)
    forget_discovery()


def merger_dir(root: Path) -> Path:
    """台账（MergeInventory.xml / 设置文件）在哪。

    1.3.x 及以前：一律猜 `<游戏根目录>/Merger`，跟 exe 实际装在哪无关 ——
    所以一旦用户在别处指定了 exe，台账仍然在旧位置找，读出来是空的。

    现在跟着 exe 走，并且在它旁边两个位置都看一眼：新版把台账放在 exe
    同目录，老版本放在 exe 同级的 Merger\\ 下。谁有台账算谁的。
    """
    exe = exe_path(root)
    if exe is not None:
        base = exe.parent
        if (base / INVENTORY).is_file():
            return base
        sub = base / MERGER_DIR
        if (sub / INVENTORY).is_file():
            return sub
        # 都还没有台账（还没合并过）：按有没有设置文件判断它是哪种版本
        if sub.is_dir():
            return sub
        return base
    return Path(root) / MERGER_DIR


def exe_path(root: Path) -> Path | None:
    """Script Merger 的主程序。没装就返回 None，调用方据此显示引导。

    顺序：用户指定过的 → 自动发现。自动发现只扫几个常见位置，见 _scan_dirs。
    """
    custom = _custom_exe()
    if custom is not None:
        return custom
    return _discover(Path(root))


def installed(root: Path) -> bool:
    return exe_path(root) is not None


def _norm_rel(s: str) -> str:
    """台账里是 game\\player\\x.ws，统一成 content/scripts/game/player/x.ws 的小写形式。"""
    t = (s or "").strip().replace("\\", "/").lstrip("./")
    low = t.lower()
    if not low.startswith("content/scripts/"):
        low = "content/scripts/" + low
    return low


def _read_settings_name(root: Path) -> str:
    """Script Merger 允许改产物文件夹名，先问它的设置。读不到就用出厂默认。"""
    p = merger_dir(root) / SETTINGS
    if not p.is_file():
        return DEFAULT_MERGED
    try:
        tree = ET.parse(p)
    except (ET.ParseError, OSError):
        return DEFAULT_MERGED
    for node in tree.iter("add"):
        if (node.get("key") or "") == "MergedModName":
            v = (node.get("value") or "").strip()
            if v:
                return v
    return DEFAULT_MERGED


def inventory(root: Path) -> dict:
    """解析合并台账。永远返回一个可用的字典，坏了也不抛异常。

    {
      "found": bool,                # 有没有 Script Merger
      "dir": str | None,
      "exe": str | None,
      "merged_name": "mod0000_MergedFiles",
      "merges": {相对路径小写: {"rel": 原始写法, "sources": [mod名...], "out": 产物名}},
      "names": [所有出现过的产物文件夹名, 小写],
      "count": 合并条目数,
      "stale_sources": [台账记着但磁盘上已经没有的 mod 名],
      "mtime": float | None,
    }
    """
    d = merger_dir(root)
    exe = exe_path(root)
    inv_path = d / INVENTORY
    out = {
        "found": exe is not None or inv_path.is_file(),
        "dir": str(d) if d.is_dir() else None,
        "exe": str(exe) if exe else None,
        "merged_name": _read_settings_name(root),
        "merges": {},
        "names": [],
        "count": 0,
        "stale_sources": [],
        "mtime": None,
    }
    if not inv_path.is_file():
        if out["found"]:
            out["names"] = [out["merged_name"].lower()]
        return out

    try:
        out["mtime"] = inv_path.stat().st_mtime
        tree = ET.parse(inv_path)
    except (OSError, ET.ParseError):
        if out["found"]:
            out["names"] = [out["merged_name"].lower()]
        return out

    names: set[str] = {out["merged_name"].lower()}
    mods_root = Path(root) / "mods"
    on_disk = {p.name.lower() for p in mods_root.iterdir() if p.is_dir()} \
        if mods_root.is_dir() else set()
    stale: set[str] = set()

    for node in tree.iter("Merge"):
        rel_raw = ""
        for tag in ("RelativePath", "relativePath", "Path"):
            el = node.find(tag)
            if el is not None and (el.text or "").strip():
                rel_raw = (el.text or "").strip()
                break
        if not rel_raw:
            continue
        sources: list[str] = []
        for el in node.findall("IncludedMod"):
            name = (el.text or "").strip()
            if not name:
                continue
            sources.append(name)
            if on_disk and name.lower() not in on_disk:
                stale.add(name)
        prod = ""
        for tag in ("MergedModName", "mergedModName"):
            el = node.find(tag)
            if el is not None and (el.text or "").strip():
                prod = (el.text or "").strip()
                break
        prod = prod or out["merged_name"]
        names.add(prod.lower())
        out["merges"][_norm_rel(rel_raw)] = {
            "rel": rel_raw,
            "sources": sources,
            "out": prod,
        }

    out["names"] = sorted(names)
    out["count"] = len(out["merges"])
    out["stale_sources"] = sorted(stale, key=str.lower)
    return out


def product_names(inv: dict) -> set[str]:
    """所有合并产物的文件夹名（小写）。"""
    return {n.lower() for n in (inv.get("names") or [])}


def sources_for(inv: dict, rel: str) -> list[str] | None:
    """这个脚本路径是谁合并出来的。台账里没记就返回 None。"""
    rec = (inv.get("merges") or {}).get((rel or "").lower())
    if rec is None:
        return None
    return list(rec.get("sources") or [])


def script_files_of(root: Path, folder: str) -> list[str]:
    """产物文件夹里实际有哪些脚本（相对路径小写）。

    Script Merger 清掉旧合并之后台账可能还残留条目，所以要用磁盘内容兜底。
    """
    base = Path(root) / "mods" / folder / "content" / "scripts"
    out: list[str] = []
    if not base.is_dir():
        return out
    for cur, _dirs, files in os.walk(base):
        for f in files:
            if not f.lower().endswith(".ws"):
                continue
            full = Path(cur) / f
            try:
                # 相对 content/scripts 取路径，再拼回统一前缀。
                # 注意是 relative_to(base) 不是 base.parent —— 后者会拼出
                # content/scripts/scripts/... 这种对不上的路径（1.0.9 的老 bug）。
                out.append("content/scripts/" +
                           str(full.relative_to(base)).replace("\\", "/").lower())
            except ValueError:
                continue
    return out


def stale_report(root: Path, inv: dict, mods: list) -> list[str]:
    """台账记着、但磁盘上已经没有的 mod。返回去重后的名字列表。

    这类残留最麻烦的地方在于：合订本里还留着引用它内部类的代码，
    游戏一编译就报「找不到 XXX」，而用户完全看不出是卸 mod 卸出来的。
    """
    if not inv.get("found"):
        return []
    have = {m.folder.lower() for m in mods} | {m.key.lower() for m in mods}
    out: list[str] = []
    for name in inv.get("stale_sources") or []:
        if name.lower() not in have and name not in out:
            out.append(name)
    return out


# ---------- 合订本在 mods.settings 里的登记状态 ----------
# 2026-10-27 事故的根因就在这里：合订本文件夹一直在，但 mods.settings 里没有
# 它的条目，于是它被排到 19 个已登记 mod 之后，源 mod 反把合并结果盖掉。

def merged_folder(root: Path, inv: dict) -> str | None:
    """合订本文件夹在磁盘上的真实名字（大小写按磁盘为准）。找不到返回 None。

    只认台账里出现过的产物名，不按 `mod0000_` 前缀猜 —— 用户可以在
    Script Merger 设置里改 `MergedModName`。
    """
    mods_root = Path(root) / "mods"
    if not mods_root.is_dir():
        return None
    known = {n.lower() for n in (inv.get("names") or [])}
    if inv.get("merged_name"):
        known.add(str(inv["merged_name"]).lower())
    if not known:
        return None
    for e in os.scandir(mods_root):
        if e.is_dir(follow_symlinks=False) and e.name.lower() in known:
            return e.name
    return None


def merged_state(root: Path, inv: dict, settings) -> dict:
    """合订本现在「在不在、登记没登记、顺序对不对」。

    settings 是 SettingsFile 实例（或任何有 .sections / .get 的对象）。

    返回：
      {
        "name": "mod0000_MergedFiles",   # 磁盘上的真实文件夹名
        "exists": True,                  # 文件夹在，且里面有 .ws
        "files": 1,                      # 合订本里的脚本数
        "entry": True,                   # mods.settings 里有条目
        "enabled": True,
        "priority": 0,
        "min_other": 10,                 # 其他 mod 里最小的 Priority（None 表示没别的）
        "is_min": True,                  # 是否严格小于其他所有 mod
        "ok": True,                      # 三条都满足（或压根没有合订本）
        "problem": "",                   # 给界面看的一句话，ok 时为空
      }
    """
    name = merged_folder(root, inv)
    out = {
        "name": name or (inv.get("merged_name") or DEFAULT_MERGED),
        "exists": False,
        "files": 0,
        "entry": False,
        "enabled": False,
        "priority": None,
        "min_other": None,
        "is_min": False,
        "ok": True,
        "problem": "",
    }
    if name:
        out["files"] = len(script_files_of(root, name))
        out["exists"] = out["files"] > 0
    if not out["exists"]:
        return out              # 没有合订本，这条规矩不适用

    sec = settings.get(name) if settings is not None else None
    others = [s for s in (settings.sections if settings is not None else [])
              if s["name"].lower() != name.lower()]
    prios = [s["priority"] for s in others if s["priority"] is not None]
    out["min_other"] = min(prios) if prios else None

    if sec is not None:
        out["entry"] = True
        out["enabled"] = bool(sec.get("enabled"))
        out["priority"] = sec.get("priority")
        out["is_min"] = (out["priority"] is not None
                         and (out["min_other"] is None
                              or out["priority"] < out["min_other"]))

    if not out["entry"]:
        out["ok"] = False
        out["problem"] = i18n.t("mrg.not_registered", name=name)
    elif not out["enabled"]:
        out["ok"] = False
        out["problem"] = i18n.t("mrg.disabled", name=name)
    elif not out["is_min"]:
        out["ok"] = False
        out["problem"] = i18n.t("mrg.priority", name=name, prio=out["priority"],
                                minp=out["min_other"])
    return out


def ensure_registered(settings, inv: dict, root: Path) -> dict:
    """把合订本补进 mods.settings，并保证它是全表最靠前的一条。

    只在**用户主动写文件**时调用（改顺序、改优先级、启用禁用、装卸）。
    扫描永远只读，只报不写。返回 {'changed': bool, 'name': str|None}。
    """
    name = merged_folder(root, inv)
    if not name or not script_files_of(root, name):
        return {"changed": False, "name": None}

    others = [s for s in settings.sections if s["name"].lower() != name.lower()]
    prios = [s["priority"] for s in others if s["priority"] is not None]
    sec = settings.get(name)

    # 已经合规就一个字都不动 —— 用户的表里是 0 还是 1 都行，
    # 只要严格小于所有别的 mod（0 和 1 都合法，别去美化它）。
    if (sec is not None and sec.get("enabled")
            and sec.get("priority") is not None
            and (not prios or sec["priority"] < min(prios))):
        return {"changed": False, "name": name}

    want = 0 if not prios else min(prios) - 1
    if want < 0:                       # 已经有人占着 0，把 <=0 的整体往上挪一格
        for s in others:
            if s["priority"] is not None and s["priority"] <= 0:
                s["priority"] += 1
        want = 0

    if sec is None:
        settings.set_state(name, enabled=True, priority=want)
        return {"changed": True, "name": name}

    changed = False
    if not sec.get("enabled"):
        sec["enabled"] = True
        changed = True
    if sec.get("priority") != want:
        sec["priority"] = want
        changed = True
    return {"changed": changed, "name": name}


def detail(root: Path, inv: dict) -> list[dict]:
    """合订本里每一组合并的明细，给界面「已合并」分类用。

    [{"rel": "game\\player\\playerWitcher.ws",
      "rel_norm": "content/scripts/game/player/playerwitcher.ws",
      "out": "mod0000_MergedFiles",
      "sources": [{"name": "modAutoLoot", "present": True}, ...],
      "in_product": True}]      # 产物文件夹里确实有这个文件
    """
    if not inv.get("found"):
        return []
    name = merged_folder(root, inv)
    have = set()
    mods_root = Path(root) / "mods"
    if mods_root.is_dir():
        have = {e.name.lower() for e in os.scandir(mods_root) if e.is_dir()}
    prod_files = set(script_files_of(root, name)) if name else set()

    rows: list[dict] = []
    for rel_norm, rec in sorted((inv.get("merges") or {}).items()):
        rows.append({
            "rel": rec.get("rel") or rel_norm,
            "rel_norm": rel_norm,
            "out": rec.get("out") or name or "",
            "sources": [{"name": s, "present": s.lower() in have}
                        for s in (rec.get("sources") or [])],
            "in_product": rel_norm in prod_files,
        })
    return rows
