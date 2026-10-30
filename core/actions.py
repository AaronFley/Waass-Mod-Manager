"""会改动游戏目录的操作。全部经过 safety 层。"""
from __future__ import annotations

from pathlib import Path

from . import merger, safety
from .paths import game_layout
from .settings_ini import SettingsFile


def _ensure_merged_entry(root: Path, settings: SettingsFile) -> bool:
    """护栏：合订本必须在 mods.settings 里，且 Priority 严格最小。

    2026-10-27 的事故就是这里缺了一环 —— 合订本文件夹在、条目不在，
    它被排到所有已登记 mod 后面，源 mod 反把合并结果盖掉，游戏启动报
    「Could not find function XXX」，而界面上一个字提示都没有。

    只在用户主动写文件的路径上调用；扫描永远只读。
    """
    try:
        inv = merger.inventory(root)
        return bool(merger.ensure_registered(settings, inv, root)["changed"])
    except OSError:
        return False


def _resolve_folder(mods_root: Path, key: str) -> Path | None:
    for cand in (key, "~" + key):
        p = mods_root / cand
        if p.is_dir():
            return p
    return None


def set_enabled(root: Path, key: str, enabled: bool) -> dict:
    """启用 / 禁用 —— 改文件夹名，不碰 `mods.settings`。

    禁用就是在文件夹名前面加一个 `~`（`modHoods` → `~modHoods`）。巫师3 的
    引擎只扫 `mods/` 下**以 `mod` 开头**的目录，`~modXxx` 它根本看不见，等于
    没装。三个好处：

    * **不写 `mods.settings`**。那个文件零注释、段与段之间恰好一个空行，
      Script Merger 的解析器不容忍任何多余字符。写得越少，出事的概率越低。
    * **一步到位、可回滚**。改回来就是启用，日志里记的是 rename，撤销直接搬回去。
    * **`Priority` 原样留着**。重新启用后加载顺序跟禁用前一模一样，
      不会因为「重新排到队尾」把冲突关系搞乱。

    唯一还会写 `mods.settings` 的情况：那条记录被标着 `Enabled=0`（旧版本程序
    或用户手动关的）。名字改回来了它仍然是 0，游戏照样不加载 —— 这种时候
    必须把它翻回来，否则用户会看到「点了启用还是灰的」。
    """
    safety.authorize_game(root)
    lay = game_layout(root)
    settings_path = lay["user_config"] / "mods.settings"
    folder = _resolve_folder(lay["mods"], key)
    if folder is None:
        raise FileNotFoundError(f"找不到 mod 文件夹：{key}")

    target = folder.with_name(key if enabled else "~" + key)
    safety.assert_writable(settings_path, folder, target)

    op = safety.Operation(f"{'启用' if enabled else '禁用'} {key}")

    renamed = False
    if folder.name != target.name:
        if target.exists():
            raise FileExistsError(
                f"『{target.name}』已经存在，没法把 {folder.name} 改过去，请手动处理")
        op.record_rename(folder, target)
        folder.rename(target)
        renamed = True

    # 只有「启用 + 记录被标了 Enabled=0」才需要动 mods.settings
    fixed = False
    merged_fixed = False
    if enabled:
        settings = SettingsFile.load(settings_path)
        sec = settings.get(key)
        if sec is not None and not sec["enabled"]:
            sec["enabled"] = True
            merged_fixed = _ensure_merged_entry(root, settings)
            op.backup(settings_path)
            settings.save()
            fixed = True

    entry = op.commit()
    return {
        "ok": True,
        "key": key,
        "enabled": enabled,
        "folder": target.name,
        "renamed": renamed,
        "settings_fixed": fixed,
        "merged_fixed": merged_fixed,
        "op": entry["id"],
    }


def set_enabled_many(root: Path, keys: list[str], enabled: bool) -> dict:
    """一次改多个模组的启用状态。

    和逐个调 `set_enabled` 的区别只有一个，但很要紧：**整个批量是一次 Operation**。
    用户点了「禁用 5 个」，撤销应该一次全回来；如果每个 key 各开一个 Operation，
    日志里会多出 5 条、撤销要点 5 次，那就等于没有撤销。

    校验全部前置：任何一个 key 找不到、或者 ~ 改名的目标已存在，整体不动。
    宁可一条都不改，也不要改到一半停下 —— 半成品状态最难解释。
    """
    keys = [str(k).strip() for k in keys if str(k).strip()]
    if not keys:
        raise ValueError("没有选中任何模组")
    seen: set[str] = set()
    uniq: list[str] = []
    for k in keys:
        if k not in seen:
            seen.add(k)
            uniq.append(k)

    safety.authorize_game(root)
    lay = game_layout(root)
    settings_path = lay["user_config"] / "mods.settings"

    plan: list[tuple[str, Path]] = []
    for key in uniq:
        folder = _resolve_folder(lay["mods"], key)
        if folder is None:
            raise FileNotFoundError(f"找不到 mod 文件夹：{key}")
        dst = folder.with_name(key if enabled else "~" + key)
        if folder.name != dst.name and dst.exists():
            raise FileExistsError(
                f"『{dst.name}』已经存在，没法把 {folder.name} 改过去，请手动处理")
        plan.append((key, folder))

    safety.assert_writable(
        settings_path,
        *[p for k, f in plan for p in (f, f.with_name(k if enabled else "~" + k))])

    op = safety.Operation(f"{'启用' if enabled else '禁用'} {len(plan)} 个模组")

    changed: list[str] = []
    renamed = 0
    for key, folder in plan:
        dst = folder.with_name(key if enabled else "~" + key)
        if folder.name != dst.name:
            op.record_rename(folder, dst)
            folder.rename(dst)
            renamed += 1
        changed.append(key)

    # 和单个启用一样：只有「启用 + 记录还是 Enabled=0」才需要写 mods.settings。
    fixed = 0
    merged_fixed = False
    if enabled:
        settings = SettingsFile.load(settings_path)
        for key in changed:
            sec = settings.get(key)
            if sec is not None and not sec["enabled"]:
                sec["enabled"] = True
                fixed += 1
        if fixed:
            merged_fixed = _ensure_merged_entry(root, settings)
            op.backup(settings_path)               # 先留底，再动手
            settings.save()

    entry = op.commit()

    return {
        "ok": True,
        "keys": changed,
        "count": len(changed),
        "enabled": enabled,
        "renamed": renamed,
        "settings_fixed": fixed,
        "merged_fixed": merged_fixed,
        "op": entry["id"],
    }


def set_priority(root: Path, key: str, priority: int) -> dict:
    """改加载顺序。数字越小越先加载。"""
    safety.authorize_game(root)
    lay = game_layout(root)
    settings_path = lay["user_config"] / "mods.settings"
    safety.assert_writable(settings_path)
    settings = SettingsFile.load(settings_path)
    op = safety.Operation(f"调整顺序 {key} → {priority}")
    settings.bump_priority(key, int(priority))
    merged_fixed = _ensure_merged_entry(root, settings)
    op.backup(settings_path)
    settings.save()
    entry = op.commit()
    return {"ok": True, "key": key, "priority": int(priority),
            "merged_fixed": merged_fixed, "op": entry["id"]}


def reorder(root: Path, keys: list[str], prios: list[int] | None = None) -> dict:
    """按给定顺序重排 mod，一次写清。

    默认给整表重新发号（10、20、30…）。但界面上「已禁用的模组钉在列表底部」
    是纯显示层的事，它们的 Priority 不该跟着变 —— 一变就等于「禁用一下，
    加载位置就丢了」。所以调用方可以传 prios 1:1 指定每个 key 的号，
    这样没进 keys 的模组一个数字都不会被动到。
    """
    safety.authorize_game(root)
    lay = game_layout(root)
    settings_path = lay["user_config"] / "mods.settings"
    safety.assert_writable(settings_path)

    # 每一把 key 都必须真有对应文件夹。set_state() 默认会新建段落，所以一个
    # 已经不存在的 key（另一个窗口刚把它删了、这边还拖着它的卡片）会凭空写出
    # 一条 [xxx] —— 游戏里就变成「找不到模组」的告警，而这边还返回 ok。
    # 静默成功是最坏的结果：用户以为排好了。
    missing = [k for k in keys if _resolve_folder(lay["mods"], k) is None]
    if missing:
        raise ValueError(
            "这些模组已经不在 mods 目录里了：" + "、".join(missing[:3])
            + ("…" if len(missing) > 3 else ""))

    if prios is None:
        nums = [(i + 1) * 10 for i in range(len(keys))]
    else:
        if len(prios) != len(keys):
            raise ValueError("prios 和 keys 数量对不上")
        nums = []
        for p in prios:
            try:
                n = int(p)
            except (TypeError, ValueError):
                raise ValueError(f"优先级不是整数：{p!r}") from None
            if n < 0:
                raise ValueError("优先级不能是负数")
            nums.append(n)
        if len(set(nums)) != len(nums):
            raise ValueError("优先级有重复，等于没排开")

    settings = SettingsFile.load(settings_path)
    op = safety.Operation(f"重排 {len(keys)} 个 mod 的加载顺序")
    for key, prio in zip(keys, nums):
        settings.set_state(key, priority=prio)
    # 重排只覆盖可见 mod 的顺序，合订本不在列表里 —— 必须单独补位，
    # 否则它要么丢条目、要么被人挤到后面（2026-10-27 就是这么崩的）。
    merged_fixed = _ensure_merged_entry(root, settings)
    op.backup(settings_path)
    settings.save()
    entry = op.commit()
    return {"ok": True, "count": len(keys), "merged_fixed": merged_fixed,
            "op": entry["id"]}
