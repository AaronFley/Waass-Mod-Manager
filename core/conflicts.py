"""冲突检测：在装东西之前，先把「会和谁打架」说清楚。

巫师3 的冲突和别的游戏不一样，真正会让人读档崩溃的只有几类：

1. **脚本同名** —— 两个 mod 都有 content/scripts/xxx.ws 的同一路径。
   游戏只会读到一个，另一个静默失效。这就是 Script Merger 存在的原因，
   所以我们把它标成「待合并」，而不是「错误」。
   已经被 Script Merger 合并过的（产物出现在组里）不算待处理，见核心
   的 `_judge_scripts`：合订本 mod0000_MergedFiles 会覆盖所有源 mod，
   把那组从「待合并」降级成「已合并」。
2. **菜单 xml 同名** —— 两个 mod 往 bin/config/.../pc 里放同名 xml，
   后者覆盖前者，于是前一个 mod 的设置项直接从菜单里消失。
3. **语言文件同名** —— 同一个 .w3strings 路径被两份提供，译文互相覆盖。
4. **Priority 相同** —— 加载顺序不确定，谁覆盖谁看运气。
5. **资源包内部同名文件** —— 两个 mod 的 `.bundle` 里有同一个资源路径
   （界面 `.redswf`、物品定义 `.xml`）。**包文件名全都一样（都叫 blob0.bundle），
   没有参考价值**，要看包里面。这类没法合并，只能谁优先谁生效。
   2026-10-28：UIRE / Hoods / HoodSlots 抢同一个 `panel_inventory.redswf`，
   脚本零重叠却让 HoodSlots 的三个槽位框整片不见 —— 就是这一类。
6. **注解挂同一个函数** —— 注解型 mod 文件名不撞，按文件名比永远「无冲突」，
   但它们可能包同一个函数。`@wrapMethod` 能链式共存（顺序影响结果），
   `@replaceMethod` 撞车会让一方的改动整段消失。
7. **脚本编码 / 语言表版本** —— 5.x 只读 UTF-8；w3strings 表版本落后会让
   文字显示成 `##` 或空白（功能正常，只是没字）。

不报的：贴图、模型这类资源同名。材质 mod 之间撞是常态，报出来全是噪音。

全部只读：这里只读文件路径和文件头，不打开改写、不删。
"""
from __future__ import annotations

import os
import time
from pathlib import Path

from .paths import game_layout
from .settings_ini import SettingsFile
from . import bundle_entries, merger, scripts_meta, w3strings

_cache: dict[str, tuple[float, dict]] = {}
_CACHE_TTL = 90.0

# 一个 mod 最多收这么多脚本路径，防止某个整合包里有几万个文件
_MAX_SCRIPTS = 6000


def _script_paths(mod_dir: Path) -> list[str]:
    """收集这个 mod 的脚本相对路径（相对 mod 文件夹）。"""
    scripts = mod_dir / "content" / "scripts"
    out: list[str] = []
    if not scripts.is_dir():
        return out
    for cur, _dirs, files in os.walk(scripts):
        for f in files:
            low = f.lower()
            if low.endswith((".backup", ".bak", ".orig", ".disabled")):
                continue
            if not low.endswith(".ws"):
                continue
            full = Path(cur) / f
            try:
                out.append(str(full.relative_to(mod_dir)).replace("\\", "/"))
            except ValueError:
                continue
            if len(out) >= _MAX_SCRIPTS:
                return out
    return out


def _w3strings_paths(mod_dir: Path) -> list[str]:
    content = mod_dir / "content"
    out: list[str] = []
    if not content.is_dir():
        return out
    for p in content.rglob("*.w3strings"):
        try:
            out.append(str(p.relative_to(mod_dir)).replace("\\", "/"))
        except ValueError:
            continue
        if len(out) >= 200:
            break
    return out


def analyze(root: Path, mods: list, force: bool = False) -> dict:
    """mods 传 ScanResult.mods。返回冲突报告。"""
    key = str(root).lower()
    now = time.time()
    if not force:
        hit = _cache.get(key)
        if hit and now - hit[0] < _CACHE_TTL:
            return hit[1]

    lay = game_layout(root)
    mods_root = lay["mods"]

    by_mod: dict[str, list[dict]] = {}
    items: list[dict] = []

    def add(code: str, level: str, title: str, detail: str,
            mods_involved: list[str], files: list[str] | None = None) -> None:
        it = {
            "code": code,
            "level": level,                 # warn / info / merge
            "title": title,
            "detail": detail,
            "mods": mods_involved,
            "files": (files or [])[:12],
        }
        items.append(it)
        for k in mods_involved:
            by_mod.setdefault(k, []).append(it)

    # 禁用的模组不参与任何冲突判定 —— 它根本不加载，不可能和别人抢东西。
    # 「~modSmoothUIRemastered 和 UIRE 抢 20 个界面文件」这种话对用户没有意义，
    # 只会把真正要看的那两条埋掉。
    active = [m for m in mods if m.enabled]

    # ---------- 1. 脚本同名 ----------
    inv = merger.inventory(root)
    prod_names = merger.product_names(inv)
    # 只读：判断合订本有没有登记，不写文件
    settings = SettingsFile.load(lay["user_config"] / "mods.settings")

    script_map: dict[str, list[str]] = {}
    script_total = 0
    anno_owner: dict[tuple[str, str, str], list[str]] = {}
    enc_bad: list[tuple[str, str, str]] = []
    for m in active:
        d = mods_root / m.folder
        if not d.is_dir():
            continue
        paths = _script_paths(d)
        script_total += len(paths)
        for rel in paths:
            script_map.setdefault(rel.lower(), []).append(m.key)
            # 顺手扫注解与编码：不额外走一遍目录，代价可以忽略
            meta = scripts_meta.scan(d / rel)
            for kind, cls, fn in meta["anno"]:
                anno_owner.setdefault((kind, cls, fn), []).append(m.key)
            if meta["enc"] in ("utf-16-le", "utf-16-be", "other"):
                enc_bad.append((m.key, rel, meta["enc"]))

    # 先把每条路径单独判定，再把「结论相同」的路径并成一张卡片。
    # 合并过的和没合并的必须分开算，否则一个已解决的文件会把整组拖回待处理。
    buckets: dict[tuple, list[str]] = {}
    for rel, keys in script_map.items():
        uniq = sorted(set(keys))
        if len(uniq) < 2:
            continue
        level, involved, covered = _judge_scripts(rel, uniq, inv, prod_names)
        buckets.setdefault((level, tuple(involved), tuple(covered)), []).append(rel)

    merged_count = 0
    for (level, involved, covered), files in sorted(
            buckets.items(), key=lambda x: (x[0][0] == "ok", -len(x[1]))):
        names = "、".join(f"『{_name_of(mods, k)}』" for k in involved)
        more = f"（还有 {len(files) - 3} 个同类文件）" if len(files) > 3 else ""
        if level == "ok":
            merged_count += len(files)
            srcs = "、".join(f"『{_name_of(mods, k)}』" for k in covered) or "来源模组"
            prod = next((k for k in involved
                         if k.lower() in prod_names), "")
            prod_name = _name_of(mods, prod) if prod else inv.get("merged_name", "")
            add("script_merged", "ok",
                f"{len(files)} 个脚本已合并，无需处理",
                f"{srcs} 的改动已经合进『{prod_name}』，这个文件夹排在所有模组之前加载，"
                f"会覆盖它们的原文件，所以 {len(involved)} 个同名不是冲突。{more}",
                list(involved), files)
            continue
        detail = ("需要跑一次 Script Merger 合并，否则只有排在后面的那份生效。"
                  if level == "merge" else
                  "合并文件夹里有这个文件，但合并记录里没有它，可能是手工拷进去的，"
                  "建议用 Script Merger 重跑一次确认。")
        add("script_overlap", "merge",
            f"{len(involved)} 个模组共有 {len(files)} 个同名脚本",
            f"{names} 会覆盖同一批脚本文件。{detail}{more}",
            list(involved), files)

    # ---------- 1b. 合并台账失效 ----------
    # 卸载了参与合并的 mod 之后，合订本里还留着引用它内部类的代码，
    # 游戏一编译就报「找不到 XXX」。用户完全看不出是卸 mod 卸出来的。
    if inv.get("found"):
        dead = merger.stale_report(root, inv, mods)
        if dead:
            names = "、".join(f"『{n}』" for n in dead[:5])
            add("merge_stale", "warn",
                f"合并记录里有 {len(dead)} 个模组已经不在硬盘上",
                f"{names} 参与过脚本合并，但文件夹已经没了。"
                f"『{inv.get('merged_name')}』里还留着它们的代码，游戏启动可能报"
                f"「找不到某个类」。打开 Script Merger 重跑一次会自动清掉这些残留。",
                [], [])
        prod = inv.get("merged_name") or ""
        if prod and inv.get("merges") and (mods_root / prod).is_dir():
            if not merger.script_files_of(root, prod):
                add("merge_empty", "warn",
                    f"『{prod}』是个空壳",
                    "合并记录说有脚本被合并过，但这个文件夹里找不到任何 .ws。"
                    "可能是 Script Merger 清理后没重新合并，游戏里那几处改动会失效。",
                    [])

    # ---------- 1c. 合订本不生效 ----------
    # 2026-10-27 的事故就是这一类：文件夹在、台账在，但 mods.settings 里没它的条目，
    # 于是被排到所有已登记 mod 后面，源 mod 反把合并结果盖掉。游戏报的是
    # 「找不到函数」，指向的却是源 mod —— 用户完全没有线索。必须主动说出来。
    mstate = merger.merged_state(root, inv, settings)
    if mstate["exists"] and not mstate["ok"]:
        add("merge_unregistered", "warn",
            f"合订本『{mstate['name']}』不生效",
            f"{mstate['problem']}。进游戏会报「Could not find function ...」，"
            f"但报错只会指向源 mod，看不出真正原因。"
            f"打开「加载顺序」点一次保存，程序会自动把它补到第一位。",
            [], [])

    # ---------- 1d. 注解挂在同一个函数上 ----------
    # 两个 mod 都 @wrapMethod 同一个函数是**支持**的（链式叠加），不算错。
    # 但叠加顺序由加载顺序决定，改了顺序结果就可能变 —— 排查「本来好好的，
    # 动过顺序之后某功能不对」时，这条是唯一线索。
    # @replaceMethod 是另一回事：整段替换，会把别人的包装一起丢掉，且毫无提示。
    by_target: dict[tuple[str, str], dict[str, list[str]]] = {}
    for (kind, cls, fn), keys in anno_owner.items():
        by_target.setdefault((cls, fn), {}).setdefault(kind, []).extend(keys)

    rep_groups: dict[tuple, list[str]] = {}
    wrap_groups: dict[tuple, list[str]] = {}
    for (cls, fn), kinds in by_target.items():
        allkeys = tuple(sorted({k for v in kinds.values() for k in v}))
        if len(allkeys) < 2:
            continue
        if kinds.get("replaceMethod"):
            who = tuple(sorted(set(kinds["replaceMethod"]) | set(allkeys)))
            rep_groups.setdefault(who, []).append(f"{cls}.{fn}")
        else:
            wrap_groups.setdefault(allkeys, []).append(f"{cls}.{fn}")

    for who, funcs in sorted(rep_groups.items(), key=lambda x: -len(x[1])):
        names = "、".join(f"『{_name_of(mods, k)}』" for k in who)
        more = f"（还有 {len(funcs) - 3} 个同类的没列出）" if len(funcs) > 3 else ""
        add("anno_replace", "warn",
            f"{len(funcs)} 个函数被整段替换了两次",
            f"{names} 都用 @replaceMethod 把 {funcs[0]} 这类函数整段换掉了。"
            f"游戏只保留最后生效的那一份，另一份的改动（连同它包在别处的逻辑）会一起消失，"
            f"而且不会有任何报错。这类没法自动合并 —— 要么调加载顺序决定谁生效，"
            f"要么请作者改成 @wrapMethod。{more}",
            list(who), funcs)

    for who, funcs in sorted(wrap_groups.items(), key=lambda x: -len(x[1])):
        names = "、".join(f"『{_name_of(mods, k)}』" for k in who)
        more = f"（还有 {len(funcs) - 3} 个同类的没列出）" if len(funcs) > 3 else ""
        add("anno_wrap", "info",
            f"{len(funcs)} 个函数被 {len(who)} 个模组分别加了逻辑",
            f"{names} 都在 {funcs[0]} 这类函数前后挂了自己的代码。"
            f"这是能共存的叠加机制，本身不是问题；但叠加顺序由加载顺序决定 —— "
            f"如果改过顺序之后某个功能表现变了，先回来对照这个列表。{more}",
            list(who), funcs)

    # ---------- 1e. 脚本编码 ----------
    # 5.00 把文本资源从 UTF-16 改成 UTF-8。4.x 时代的老包原样拷进来会编译失败，
    # 游戏只报文件名，用户完全看不出问题在编码上。
    if enc_bad:
        mods_in = sorted({k for k, _r, _e in enc_bad})
        names = "、".join(f"『{_name_of(mods, k)}』" for k in mods_in)
        add("ws_encoding", "warn",
            f"{len(enc_bad)} 个脚本不是 UTF-8",
            f"{names} 里有 {enc_bad[0][2]} 编码的脚本。重制版 5.00 只读 UTF-8，"
            f"这些文件会被当成乱码，整份脚本编译不过，进游戏就是一屏红字。"
            f"这类 mod 是 4.x 时代的老包，需要作者用新工具重新导出；"
            f"自己转码不一定能救回来。",
            mods_in, [f"{rel}（{enc}）" for _k, rel, enc in enc_bad])

    # ---------- 2. 菜单 xml 同名 ----------
    menu_owner: dict[str, list[str]] = {}
    for m in active:
        for xml in list(m.menu_xmls) + list(m.disabled_menu_xmls):
            menu_owner.setdefault(xml.lower(), []).append(m.key)
        d = mods_root / m.folder
        if (d / "bin").is_dir():
            pcdir = d / "bin" / "config" / "r4game" / "user_config_matrix" / "pc"
            if pcdir.is_dir():
                for xml in pcdir.glob("*.xml"):
                    menu_owner.setdefault(xml.name.lower(), []).append(m.key)

    for xml, keys in sorted(menu_owner.items()):
        uniq = sorted(set(keys))
        if len(uniq) < 2:
            continue
        names = "、".join(f"『{_name_of(mods, k)}』" for k in uniq)
        add("menu_xml_clash", "warn",
            f"菜单文件重名：{xml}",
            f"{names} 都想写这个菜单文件。游戏只认最后写入的那一份，"
            f"另一个 mod 的设置项会从菜单里消失。装之前先确认它们是不是同一个 mod 的不同版本。",
            uniq, [xml])

    # ---------- 3. 语言文件 ----------
    # 注意：每个 mod 的 content/xx.w3strings 都待在自己的文件夹里，游戏分别加载，
    # 不会互相覆盖 —— 这不是冲突。只有同一个「mod 内相对路径」重复才有问题
    # （那是包本身做错了）。这里只统计有多少 mod 自带中文，供界面上显示。
    w3_map: dict[str, list[str]] = {}
    cn_mods = 0
    for m in active:
        d = mods_root / m.folder
        if not d.is_dir():
            continue
        paths = _w3strings_paths(d)
        if any(p.lower().endswith("cn.w3strings") for p in paths):
            cn_mods += 1
        for rel in paths:
            w3_map.setdefault(rel.lower(), []).append(m.key)

    for rel, keys in w3_map.items():
        uniq = sorted(set(keys))
        # 一个 mod 自己提供了两份同名语言文件才是真问题
        if len(uniq) < 2 or _count_same_mod(keys) == 0:
            continue
        names = "、".join(f"『{_name_of(mods, k)}』" for k in uniq)
        add("w3strings_clash", "warn",
            f"语言文件重名：{rel}",
            f"{names} 在同一路径下都有这份语言文件。同目录同名只会读到其中一份。",
            uniq, [rel])

    # ---------- 3b. 资源包内部的同名资源 ----------
    # 包文件名全都一样（都叫 blob0.bundle），按文件比永远「全撞」，没有信息量。
    # 真正会出事的是**包里面**同一个资源路径被两份提供：界面资源没法合并，
    # 只有一份能生效，另一份的改动整片消失，而功能脚本可能还在正常跑。
    be_owner: dict[str, list[str]] = {}
    bundle_mods = 0
    for m in active:
        d = mods_root / m.folder
        if not d.is_dir():
            continue
        packs = bundle_entries.scan_mod(d)
        if packs:
            bundle_mods += 1
        for names in packs.values():
            for n in names:
                be_owner.setdefault(n, []).append(m.key)

    ui_owner: dict[tuple, list[str]] = {}
    data_owner: dict[tuple, list[str]] = {}
    for n, keys in be_owner.items():
        uniq = tuple(sorted(set(keys)))
        if len(uniq) < 2:
            continue
        kind = bundle_entries.classify(n)
        if kind == "ui":
            ui_owner.setdefault(uniq, []).append(n)
        elif kind == "data":
            data_owner.setdefault(uniq, []).append(n)
        # asset（贴图/模型/声音）不报：材质 mod 之间撞是常态，报出来全是噪音

    for uniq, files in sorted(ui_owner.items(), key=lambda x: -len(x[1])):
        names = "、".join(f"『{_name_of(mods, k)}』" for k in uniq)
        leaves = [_leaf(f) for f in files]
        head = "、".join(leaves[:3])
        if len(files) > 3:
            what = f"{head} 这些文件（还有 {len(files) - 3} 个）"
        elif len(files) > 1:
            what = f"{head} 这些文件"
        else:
            what = head
        add("bundle_ui", "warn",
            f"{len(files)} 个界面文件被 {len(uniq)} 个模组同时提供",
            f"{names} 的包里都有 {what}。游戏只会加载其中一份，"
            f"另一份的界面改动会整片消失 —— 常见特征是「功能还在，但界面不对/看不见」。"
            f"这类资源没法像脚本那样合并。先别急着卸载：有时上游模组已经把对方的改动"
            f"整合进去了，要看实际效果再决定。",
            list(uniq), leaves)

    for uniq, files in sorted(data_owner.items(), key=lambda x: -len(x[1])):
        names = "、".join(f"『{_name_of(mods, k)}』" for k in uniq)
        leaves = [_leaf(f) for f in files]
        head = "、".join(leaves[:3])
        more = f"（还有 {len(files) - 3} 个）" if len(files) > 3 else ""
        add("bundle_data", "info",
            f"{len(files)} 个定义文件被 {len(uniq)} 个模组同时提供",
            f"{names} 的包里都有 {head}{more}。这类是物品/数值定义，"
            f"同样是只有一份生效，另一方的改动会失效，且不会报错。",
            list(uniq), leaves)

    # ---------- 3c. 语言表版本落后 ----------
    # 游戏读 w3strings 是按版本走的。表比游戏旧时，新增的文字查不到，
    # 界面显示成 ## 或空白 —— 功能本身可能完全正常，只是「没字」。
    # 2026-10-28：HoodSlots 用 v162 的表，游戏是 v164，槽位名推成空字符串，
    # 面板那边按「空就隐藏」处理，三个框整片不显示。
    stale_w3: dict[str, tuple[str, int]] = {}
    base_ver = w3strings.game_baseline(root)
    if base_ver:
        for m in active:
            d = mods_root / m.folder
            for rel in m.w3strings:
                low = rel.lower()
                if not (low.endswith("cn.w3strings") or low.endswith("zh.w3strings")):
                    continue
                v = w3strings.version(d / rel)
                if v is None or v >= base_ver:
                    continue
                prev = stale_w3.get(m.key)
                if prev is None or v < prev[1]:
                    stale_w3[m.key] = (rel, v)

    if stale_w3:
        pairs = "、".join(f"『{_name_of(mods, k)}』(v{v})" for k, (_r, v) in stale_w3.items())
        add("w3strings_stale", "warn",
            f"{len(stale_w3)} 个模组的语言表比游戏旧",
            f"游戏本体是 v{base_ver}，{pairs} 的表版本落后。"
            f"游戏读不出它们新增的文字，界面上这类地方会显示成 ## 或空白 —— "
            f"功能通常还在，只是没字（比如某个该出现的框没出现）。"
            f"这是作者打包时用的旧工具，只能等模组更新。",
            list(stale_w3))

    # ---------- 4. Priority 相同 ----------
    prio: dict[int, list[str]] = {}
    for m in active:
        if m.priority is not None:
            prio.setdefault(m.priority, []).append(m.key)
    for p, keys in sorted(prio.items()):
        if len(keys) < 2:
            continue
        names = "、".join(f"『{_name_of(mods, k)}』" for k in keys)
        add("dup_priority", "warn",
            f"加载顺序重复：{p}",
            f"{names} 的 Priority 都是 {p}。谁先加载不确定，同名文件谁覆盖谁看运气。"
            f"给它们改成不同的数字就好。",
            keys)

    # ---------- 5. 结构可疑 ----------
    for m in active:
        d = mods_root / m.folder
        if not d.is_dir():
            continue
        has_content = (d / "content").is_dir()
        has_bin = (d / "bin").is_dir()
        if not has_content and not has_bin:
            add("empty_mod", "warn",
                f"『{m.name}』里没有 content 文件夹",
                "巫师3 的 mod 至少要有 content 目录（里面有 bundle 或 scripts）。"
                "这个文件夹可能是解压层级错了，游戏不会加载它。",
                [m.key])
        elif has_content:
            bundles = 0
            for _cur, _dirs, files in os.walk(d / "content"):
                bundles += sum(1 for f in files if f.lower().endswith(".bundle"))
                if bundles:
                    break
            if bundles == 0 and not m.has_scripts and not m.w3strings:
                add("no_payload", "warn",
                    f"『{m.name}』里找不到 .bundle 或脚本",
                    "有 content 目录但里面没有游戏能读的内容，可能是没装全。",
                    [m.key])

    stats = {
        "script_files": script_total,
        "script_groups": sum(1 for b in buckets if b[0] == "merge"),
        "merged_groups": merged_count,
        "items": len(items),
        "merge": sum(1 for i in items if i["level"] == "merge"),
        "warn": sum(1 for i in items if i["level"] == "warn"),
        "info": sum(1 for i in items if i["level"] == "info"),
        "ok": sum(1 for i in items if i["level"] == "ok"),
        "cn_mods": cn_mods,
        # 1.1.1 新增的三层检测
        "bundle_mods": bundle_mods,
        "bundle_ui": sum(len(v) for v in ui_owner.values()),
        "bundle_data": sum(len(v) for v in data_owner.values()),
        "anno_targets": len(by_target),
        "anno_wrap": sum(len(v) for v in wrap_groups.values()),
        "anno_replace": sum(len(v) for v in rep_groups.values()),
        "ws_bad": len(enc_bad),
        "w3_stale": len(stale_w3),
        "w3_base": base_ver,
        "disabled": len(mods) - len(active),
        "merger": {
            "found": bool(inv.get("found")),
            "exe": inv.get("exe"),
            "dir": inv.get("dir"),
            "merged_name": inv.get("merged_name"),
            "count": int(inv.get("count") or 0),
            "stale": len(inv.get("stale_sources") or []),
            # 合订本在 mods.settings 里的登记状态（2026-10-27 事故后加的）
            "registered": bool(mstate.get("entry")),
            "priority": mstate.get("priority"),
            "is_min": bool(mstate.get("is_min")),
            "applies": bool(mstate.get("exists")),
            "problem": mstate.get("problem") or "",
        },
    }

    res = {
        "items": items,
        "by_mod": by_mod,
        "stats": stats,
        "checked_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    _cache[key] = (now, res)
    return res


def _judge_scripts(rel: str, keys: list[str], inv: dict,
                   prod_names: set[str]) -> tuple[str, list[str], list[str]]:
    """一条同名脚本路径，到底是「待合并」「已合并」还是「台账没记」。

    返回 (level, 参与者, 已覆盖的来源 mod)。

    关键点：合并产物（mod0000_MergedFiles）也算一个「提供者」，因为它文件夹里
    确实有同名文件。但它不是新冲突 —— 它是裁判。只有组里出现了它没合并进去
    的第三方，才还算待合并。
    """
    if not prod_names:
        return "merge", keys, []

    prods = [k for k in keys if k.lower() in prod_names]
    if not prods:
        return "merge", keys, []

    srcs = merger.sources_for(inv, rel)
    if srcs is None:
        # 产物里有这个文件，台账里却没这条记录：可能是手工拷的，或者
        # Script Merger 清过帐但没清文件。两种都得让它重跑一次确认。
        return "unrecorded", keys, []

    src_low = {s.lower() for s in srcs}
    left = [k for k in keys if k.lower() not in prod_names and k.lower() not in src_low]
    if not left:
        # 全是已合并的源，产物覆盖它们，收工
        return "ok", list(keys), sorted(srcs, key=str.lower)
    # 还有没被合并进来的 mod，它和产物仍抢同一个文件
    return "merge", left + prods, sorted(srcs, key=str.lower)


def _count_same_mod(keys: list[str]) -> int:
    """同一个 mod 出现了两次以上 → 它自己包里有同名文件，属于打包问题。"""
    from collections import Counter
    return sum(1 for v in Counter(keys).values() if v > 1)


def _name_of(mods: list, key: str) -> str:
    for m in mods:
        if m.key == key:
            return m.name
    return key


def _leaf(path: str) -> str:
    """bundle 内部路径 `a\\b\\c.redswf` → `c.redswf`。

    界面上写全路径会把卡片撑爆，而真正要认得的就是文件名。
    """
    return path.replace("/", "\\").split("\\")[-1]


def drop_cache() -> None:
    _cache.clear()
    bundle_entries.drop_cache()
    scripts_meta.drop_cache()


def for_mod(report: dict, key: str) -> list[dict]:
    return report.get("by_mod", {}).get(key, [])
