"""安装 / 卸载 / 恢复：真正动文件的那一层。

所有写入都经过两道闸门：
  1. core.guard —— 只有显式授权的位置能写，漏授权当场报错
  2. core.safety —— 游戏在跑就拒绝；写前快照；进 journal 可撤销

卸载不是删除，是搬进隔离区（data/quarantine/<包id>/），原包路径也记在档案里，
所以「恢复」等于一键重装，不用重新下载。
"""
from __future__ import annotations

import shutil
from pathlib import Path

from . import config_merge, config_patches, guard, i18n, merger, registry, safety
from .archive import ArchiveError, build_plan, cleanup, extract
from .paths import DATA_DIR, game_layout, load_config
from .settings_ini import SettingsFile

QUARANTINE = DATA_DIR / "quarantine"


class InstallError(RuntimeError):
    pass


def _keep_merged_first(root: Path, settings: SettingsFile) -> None:
    """写 mods.settings 之前都要过这一道：合订本必须在表里且排最前。

    装卸、启停、改顺序都会写这个文件，任何一处漏了，合并结果就会被源 mod
    盖掉 —— 症状是游戏报「找不到函数」，但界面上看不出任何关联。
    """
    try:
        merger.ensure_registered(settings, merger.inventory(root), root)
    except OSError:
        pass


class NeedsConfirm(InstallError):
    """需要用户再点一次确认才能继续。带结构化信息给界面用。"""

    def __init__(self, message: str, detail: dict):
        super().__init__(message)
        self.detail = detail


def _game(root: str | Path) -> Path:
    return Path(root)


def _meta_for(info: dict, *, cover: bool = True) -> dict:
    """顺便从 Nexus 取一次这个 mod 的作者 / 封面 / 最新版。

    只在设置里打开了「安装时自动获取资料」才做；任何失败都返回 {}，
    绝不因为联网问题挡住安装。
    """
    nid = str(info.get("nexus_id") or "").strip()
    if not nid or not nid.isdigit():
        return {}
    cfg = load_config()
    if not cfg.get("nexus_auto_meta", True):
        return {}
    from . import nexus_meta
    try:
        nexus_meta.refresh([nid], with_cover=cover and cfg.get("nexus_auto_cover", True),
                           timeout=8.0)
    except Exception:                                # noqa: BLE001 抓不到就抓不到
        return {}
    return nexus_meta.cached(nid) or {}


def _abs(root: Path, rel: str) -> Path:
    return root / Path(rel.replace("/", "\\"))


def _plan_merge(plan, staging: str | Path, root: Path) -> dict[str, dict]:
    """算出「哪些游戏自带的配置文件要走合并、具体加/改哪几行」。

    只读：读当前文件、读包里的文件，比对，不写。
    返回 {相对路径: {cur, enc, changes}}，没有实际改动的不进结果。
    """
    out: dict[str, dict] = {}
    for e in plan.entries:
        if not getattr(e, "mergeable", False):
            continue
        target = _abs(root, e.dst)
        src = Path(staging) / e.src
        try:
            cur, enc = config_merge.load_text(target)
            pkg_text, _ = config_merge.load_text(src)
        except OSError:
            continue                          # 读不出来就走原来的整份替换
        changes = config_merge.diff(cur, pkg_text)
        if not any(c.op != "skip" for c in changes):
            # 包里写的和游戏里完全一致（或全是受保护字段）：这个文件不用碰
            out[e.dst] = {"cur": cur, "enc": enc, "changes": changes, "noop": True}
            continue
        out[e.dst] = {"cur": cur, "enc": enc, "changes": changes, "noop": False}
    return out


def _merged_of(pkg: dict) -> list[str]:
    """这个包按行合并过、且不在 paths 里的配置（属于游戏，不能搬走）。"""
    paths = set(pkg.get("paths") or [])
    return [r for r in (pkg.get("merged") or []) if r not in paths]


def _revert_merged(pkg_id: str, root: Path, op) -> tuple[list[dict], list[dict]]:
    """卸载时撤掉合并过的配置改动。返回 (撤销情况, 冲突列表)。

    不删文件：这些 .ini 是游戏自己的。只把本包加的键删掉、改过的键还原成
    安装前的值。如果发现值已被别人改过（另一个 mod 或你手工），跳过并报冲突。
    """
    reverted, conflicts = [], []
    for rel, rec in config_patches.for_pkg(pkg_id).items():
        target = _abs(root, rel)
        if not target.is_file():
            continue
        try:
            cur, enc = config_merge.load_text(target)
        except OSError:
            continue
        changes = [config_merge.Change.from_dict(d) for d in rec.get("changes", [])]
        new, n, conf = config_merge.revert(cur, changes)
        if new != cur:
            op.backup(target)
            config_merge.save_text(target, new, enc)
        reverted.append({"dst": rel, "reverted": n})
        conflicts += [{"dst": rel, **c} for c in conf]
    return reverted, conflicts


def _apply_merged(pkg_id: str, root: Path, op) -> list[dict]:
    """恢复时把合并过的改动重新打上（和安装时算出来的那几行完全一致）。"""
    applied = []
    for rel, rec in config_patches.for_pkg(pkg_id).items():
        target = _abs(root, rel)
        if not target.is_file():
            continue
        try:
            cur, enc = config_merge.load_text(target)
        except OSError:
            continue
        changes = [config_merge.Change.from_dict(d) for d in rec.get("changes", [])]
        new, n = config_merge.apply(cur, changes)
        if new != cur:
            op.backup(target)
            config_merge.save_text(target, new, enc)
        applied.append({"dst": rel, "applied": n})
    return applied


def preview(archive: str | Path, game_root: str | Path) -> dict:
    """预览：解压到临时区、识别结构、列出要写什么、标冲突。全程只读游戏目录。"""
    root = _game(game_root)
    info = registry.parse_archive_name(str(archive))
    nid = str(info["nexus_id"] or "")
    known = {}
    if nid:
        from . import nexus_meta
        known = nexus_meta.cached(nid) or {}    # 只读缓存，预览不联网
    staging, _ = extract(archive)
    try:
        plan = build_plan(staging, root)
        conflicts = [
            {"dst": e.dst, "kind": e.kind, "name": e.name}
            for e in plan.conflicts
        ]
        merge_plan = _plan_merge(plan, staging, root)
        merges = []
        for rel, m in merge_plan.items():
            if m["noop"]:
                continue
            s = config_merge.summarize(m["changes"])
            merges.append({"dst": rel, "kind": "bin_config", **s})
        return {
            "archive": str(archive),
            "name": info["name"],
            "nexus_id": info["nexus_id"],
            "version": info["version"],
            "meta": {k: known.get(k) for k in
                     ("mod_id", "name", "author", "version", "summary",
                      "downloads", "cover", "url")} if known else {},
            "wrapper": plan.wrapper,
            "summary": plan.summary,
            "risk": plan.risk,
            "mods": plan.mod_folders,
            "dlc": plan.dlc_folders,
            "menu_xmls": plan.menu_xmls,
            "bin_binaries": plan.bin_binaries,
            "bin_configs": plan.bin_configs,
            "merges": merges,
            "has_settings": plan.has_settings,
            "entries": [
                {"dst": e.dst, "kind": e.kind, "size": e.size,
                 "conflict": e.conflict, "name": e.name,
                 "mergeable": getattr(e, "mergeable", False)}
                for e in plan.entries
            ],
            "conflicts": conflicts,
            "warnings": plan.warnings,
            "ignored": plan.ignored,
            "ok": plan.ok,
        }
    finally:
        cleanup(staging)


def install(archive: str | Path, game_root: str | Path, *,
            allow_risk: bool = False, on_conflict: str = "abort",
            dry_run: bool = False) -> dict:
    """按包内容装进去。

    allow_risk   False 时，遇到要写 bin/ 二进制文件的包会拒绝（需二次确认）
    on_conflict  "abort" 遇到同名就停；"replace" 把旧的先移进隔离区再写
    dry_run      只算不写，返回将要发生的动作
    """
    root = _game(game_root)
    if not root.is_dir():
        raise InstallError(i18n.t("ins.game_dir_missing", path=root))

    info = registry.parse_archive_name(str(archive))
    staging, _ = extract(archive)
    try:
        plan = build_plan(staging, root)
        if not plan.ok:
            raise InstallError(
                i18n.t("ins.no_mod_structure",
                       warn="\n".join(plan.warnings[:2]))
            )

        if plan.risk == "high" and not allow_risk:
            raise NeedsConfirm(
                i18n.t("ins.risk_bin"),
                {"risk": plan.risk, "bin_binaries": plan.bin_binaries,
                 "bin_configs": plan.bin_configs,
                 "need": "allow_risk"},
            )

        conflicts = plan.conflicts
        if conflicts and on_conflict != "replace":
            raise NeedsConfirm(
                i18n.tn("ins.conflict_one", "ins.conflict_many", len(conflicts),
                        n=len(conflicts),
                        names=i18n.join((c.name or c.dst) for c in conflicts[:4])),
                {"conflicts": [{"dst": c.dst, "name": c.name, "kind": c.kind}
                               for c in conflicts],
                 "need": "on_conflict=replace"},
            )

        pkg_id = registry.make_id(info["name"])
        q_dir = QUARANTINE / pkg_id
        # 游戏自带的 .ini 走合并：先算清楚要加/改哪几行
        merge_plan = _plan_merge(plan, staging, root)
        merged_now = [rel for rel, m in merge_plan.items() if not m["noop"]]
        # 合并的文件属于游戏，卸载时只撤改动、不搬走，所以不进 paths
        top_paths = [e.dst for e in plan.entries
                     if e.kind != "settings" and e.dst not in merge_plan]

        actions = []
        for e in plan.entries:
            if e.kind == "settings":
                continue
            m = merge_plan.get(e.dst)
            if m is not None:
                s = config_merge.summarize(m["changes"])
                actions.append({
                    "dst": e.dst, "kind": e.kind,
                    "action": i18n.t("ins.act_noop") if m["noop"]
                    else i18n.t("ins.act_merge", text=s['text']),
                })
                continue
            actions.append({
                "dst": e.dst,
                "kind": e.kind,
                "action": i18n.t("ins.act_replace") if e.conflict
                else i18n.t("ins.act_add"),
            })

        merge_summary = [{"dst": rel,
                          **config_merge.summarize(m["changes"])}
                         for rel, m in merge_plan.items() if not m["noop"]]

        if dry_run:
            return {
                "ok": True, "dry_run": True, "package_id": pkg_id,
                "name": info["name"], "version": info["version"],
                "nexus_id": info["nexus_id"], "risk": plan.risk,
                "actions": actions, "mods": plan.mod_folders,
                "dlc": plan.dlc_folders, "menu_xmls": plan.menu_xmls,
                "bin_binaries": plan.bin_binaries,
                "bin_configs": plan.bin_configs,
                "merges": merge_summary,
                "has_settings": plan.has_settings,
            }

        # ---- 开始写 ----
        # 按实际要写的路径授权。不能只看 bin_binaries —— 只带配置文件的包
        # （如 SmartAutoLoot 的 bin/config/base/autoloot.ini）不属于二进制，
        # 却同样需要写 bin/ 下，漏掉就装不上。
        safety.authorize_rels(root, [e.dst for e in plan.entries])
        guard.allow(DATA_DIR, QUARANTINE)
        safety.assert_writable(*[_abs(root, e.dst) for e in plan.entries])

        op = safety.Operation(
            i18n.t("ins.op_install", name=info['name'], version=info['version']).strip())
        try:
            for e in plan.entries:
                if e.kind == "settings":
                    continue                      # mods.settings 单独合并，见下
                target = _abs(root, e.dst)
                src = Path(staging) / e.src

                # 游戏自带的 .ini：只把 mod 写的那几行并进去，绝不整份替换。
                # 整份替换会冲掉游戏自己的键、你手工改过的值，还可能把
                # ConfigVersion 改旧（Debug Console 包里是 5，游戏是 7）。
                m = merge_plan.get(e.dst)
                if m is not None:
                    if m["noop"]:
                        continue
                    new_text, _n = config_merge.apply(m["cur"], m["changes"])
                    if new_text != m["cur"]:
                        config_patches.ensure_baseline(e.dst, target)
                        op.backup(target)          # journal 里留一份，可撤销
                        config_merge.save_text(target, new_text, m["enc"])
                        config_patches.record(pkg_id, info["name"], e.dst,
                                              m["changes"])
                    continue

                if target.exists() or (target.is_dir()):
                    q = q_dir / e.dst
                    q.parent.mkdir(parents=True, exist_ok=True)
                    op.record_rename(target, q)
                    shutil.move(str(target), str(q))

                target.parent.mkdir(parents=True, exist_ok=True)
                if src.is_dir():
                    shutil.copytree(src, target, dirs_exist_ok=True)
                else:
                    shutil.copy2(src, target)
                op.record_create(target)

            # 包里带 mods.settings 的话，合并进现有配置（不覆盖）
            settings_delta: list[str] = []
            settings_entry = next((e for e in plan.entries if e.kind == "settings"), None)
            if plan.has_settings and settings_entry is not None:
                settings_path = game_layout(root)["user_config"] / "mods.settings"
                incoming = SettingsFile.load(Path(staging) / settings_entry.src)
                current = SettingsFile.load(settings_path)
                op.backup(settings_path)
                for sec in incoming.sections:
                    key = sec["name"]
                    if current.get(key) is None:
                        current.set_state(key, enabled=sec["enabled"],
                                          priority=sec["priority"])
                        settings_delta.append(key)
                _keep_merged_first(root, current)
                current.save()
        except Exception:
            # 装到一半失败：把已经写进去的清掉，把移走的放回来
            try:
                safety.rollback_op(op)
            finally:
                raise

        entry = registry.new_entry(
            name=info["name"], archive=str(archive), nexus_id=info["nexus_id"],
            version=info["version"], paths=top_paths, mods=plan.mod_folders,
            dlc=plan.dlc_folders, menu_xmls=plan.menu_xmls,
            files=[e.dst for e in plan.entries if e.kind != "settings"],
            risk=plan.risk, op=op.id, mods_settings_delta=settings_delta,
            quarantine=str(q_dir), merged=merged_now,
        )
        entry["id"] = pkg_id
        registry.add(entry)
        op.commit()

        # 装完顺手把作者 / 封面 / 最新版取回来（可在设置里关；失败不影响安装）
        meta = _meta_for(info)

        # 1.2.4 去掉了「安装后复制一份原包」：用户明确说这是小众需求。
        # 下载目录里本来就有原包，多存一份只是占空间。
        return {
            "ok": True, "package_id": pkg_id, "name": info["name"],
            "version": info["version"], "nexus_id": info["nexus_id"],
            "risk": plan.risk, "actions": actions,
            "mods": plan.mod_folders, "dlc": plan.dlc_folders,
            "menu_xmls": plan.menu_xmls, "bin_binaries": plan.bin_binaries,
            "bin_configs": plan.bin_configs,
            "merges": merge_summary,
            "op": op.id,
            "meta": {k: meta.get(k) for k in
                     ("mod_id", "name", "author", "version", "summary",
                      "downloads", "cover", "url")} if meta else {},
        }
    finally:
        cleanup(staging)


def uninstall(pkg_id: str, game_root: str | Path) -> dict:
    """卸载到隔离区。文件不会被删，随时可以恢复。"""
    root = _game(game_root)
    pkg = registry.get(pkg_id)
    if pkg is None:
        raise InstallError(i18n.t("ins.pkg_not_found", id=pkg_id))
    if pkg["state"] != "active":
        raise InstallError(i18n.t("ins.state_no_uninstall", state=pkg['state']))

    safety.authorize_rels(root, list(pkg["paths"]) + _merged_of(pkg))
    guard.allow(DATA_DIR, QUARANTINE)
    # 卸载也是往游戏目录里动文件，同样要过闸门（以前这里直接 move，绕过了检查）
    safety.assert_writable(*[_abs(root, rel)
                             for rel in list(pkg["paths"]) + _merged_of(pkg)])

    q_dir = QUARANTINE / pkg_id
    op = safety.Operation(i18n.t("ins.op_uninstall", name=pkg['name']))
    moved, missing = [], []

    for rel in pkg["paths"]:
        src = _abs(root, rel)
        if not src.exists():
            missing.append(rel)
            continue
        q = q_dir / rel
        q.parent.mkdir(parents=True, exist_ok=True)
        op.record_rename(src, q)
        shutil.move(str(src), str(q))
        moved.append(rel)

    # 游戏自带的配置文件：只撤掉本包加/改的那几行，文件本身留下
    reverted, merge_conflicts = _revert_merged(pkg_id, root, op)

    # 顺手在 mods.settings 里关掉它的条目，避免留下空壳
    disabled: list[str] = []
    if pkg.get("mods"):
        settings_path = game_layout(root)["user_config"] / "mods.settings"
        if settings_path.is_file():
            settings = SettingsFile.load(settings_path)
            op.backup(settings_path)
            changed = False
            for key in pkg["mods"]:
                if settings.get(key) is not None:
                    settings.get(key)["enabled"] = False
                    disabled.append(key)
                    changed = True
            if changed:
                _keep_merged_first(root, settings)
                settings.save()

    registry.update(pkg_id, state="removed", quarantine=str(q_dir),
                    removed_at=safety.now_str())
    op.commit()
    return {
        "ok": True, "package_id": pkg_id, "name": pkg["name"],
        "moved": moved, "missing": missing, "disabled": disabled,
        "reverted": reverted, "merge_conflicts": merge_conflicts,
        "quarantine": str(q_dir), "op": op.id,
    }


def restore(pkg_id: str, game_root: str | Path) -> dict:
    """从隔离区搬回来 —— 等于重装，不用重新下载。"""
    root = _game(game_root)
    pkg = registry.get(pkg_id)
    if pkg is None:
        raise InstallError(i18n.t("ins.pkg_not_found", id=pkg_id))
    if pkg["state"] != "removed":
        raise InstallError(i18n.t("ins.only_removed_restore", state=pkg['state']))

    q_dir = Path(pkg.get("quarantine") or (QUARANTINE / pkg_id))
    merged_rels = _merged_of(pkg)
    # 只合并配置、不带 mod 文件夹的包（如 Debug Console），卸载时隔离区是空的。
    # 这种包照样能恢复 —— 恢复就是「把改动重新打回游戏配置」，不依赖隔离区。
    if not q_dir.is_dir() and not merged_rels:
        raise InstallError(i18n.t("ins.quarantine_missing", path=q_dir))

    safety.authorize_rels(root, list(pkg["paths"]) + _merged_of(pkg))
    guard.allow(DATA_DIR, QUARANTINE)
    safety.assert_writable(*[_abs(root, rel)
                             for rel in list(pkg["paths"]) + _merged_of(pkg)])

    op = safety.Operation(i18n.t("ins.op_restore", name=pkg['name']))
    restored, blocked = [], []
    for rel in pkg["paths"]:
        src = q_dir / rel
        if not src.exists():
            continue
        dst = _abs(root, rel)
        if dst.exists():
            blocked.append(rel)
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        op.record_rename(src, dst)
        shutil.move(str(src), str(dst))
        restored.append(rel)

    # 合并过的配置：把改动重新打回游戏自己的文件里
    merge_applied = _apply_merged(pkg_id, root, op)
    merge_changed = sum(int(a.get("applied") or 0) for a in merge_applied)

    if not restored and not merge_changed:
        raise InstallError(
            i18n.t("ins.restore_empty_blocked", names=i18n.join(blocked[:3]))
            if blocked else i18n.t("ins.restore_empty"))

    enabled: list[str] = []
    if pkg.get("mods"):
        settings_path = game_layout(root)["user_config"] / "mods.settings"
        if settings_path.is_file():
            settings = SettingsFile.load(settings_path)
            op.backup(settings_path)
            changed = False
            for key in pkg["mods"]:
                if settings.get(key) is not None:
                    settings.get(key)["enabled"] = True
                    enabled.append(key)
                    changed = True
            if changed:
                _keep_merged_first(root, settings)
                settings.save()

    registry.update(pkg_id, state="active", restored_at=safety.now_str())
    op.commit()
    return {"ok": True, "package_id": pkg_id, "name": pkg["name"],
            "restored": restored, "blocked": blocked, "enabled": enabled,
            "merges": merge_applied, "op": op.id}


def unknown_mods(game_root: str | Path) -> list[str]:
    """游戏 mods/ 里存在、但档案里没有记录的 mod 文件夹（多半是以前手动装的）。

    Script Merger 的合订本不算 —— 它不是用户装的 mod，报「来路不明」只会吓人。
    """
    from . import merger
    root = _game(game_root)
    lay = game_layout(root)
    if not lay["mods"].is_dir():
        return []
    known = {m.lower().lstrip("~")
             for p in registry.load()["packages"]
             for m in p.get("mods", [])}
    merged = merger.product_names(merger.inventory(root))
    out = []
    for d in sorted(lay["mods"].iterdir(), key=lambda x: x.name.lower()):
        if not d.is_dir() or d.name.startswith("."):
            continue
        low = d.name.lower().lstrip("~")
        if low in known or low in merged:
            continue
        out.append(d.name)
    return out


def uninstall_all(game_root: str | Path) -> dict:
    """一键纯净恢复：把所有登记在册的 active 包搬进隔离区。

    不碰档案里没记录的东西 —— 那些可能是你手动装的，我不替你决定。
    """
    root = _game(game_root)
    pkgs = registry.by_state("active")
    done, failed = [], []
    for p in pkgs:
        try:
            r = uninstall(p["id"], root)
            done.append({"id": p["id"], "name": p["name"], "moved": len(r["moved"])})
        except Exception as exc:                       # 单个失败不影响其它
            failed.append({"id": p["id"], "name": p["name"], "error": str(exc)})
    return {
        "ok": not failed,
        "done": done,
        "failed": failed,
        "left_alone": unknown_mods(root),
    }


def list_packages() -> dict:
    data = registry.load()
    groups: dict[str, list[dict]] = {"active": [], "removed": [], "superseded": []}
    for p in data["packages"]:
        groups.setdefault(p["state"], []).append({
            "id": p["id"], "name": p["name"], "version": p.get("version", ""),
            "nexus_id": p.get("nexus_id"), "risk": p.get("risk", "normal"),
            "installed_at": p.get("installed_at", ""),
            "mods": p.get("mods", []), "dlc": p.get("dlc", []),
            "menu_xmls": p.get("menu_xmls", []),
            "file_count": len(p.get("files", [])),
            "archive": p.get("archive", ""), "state": p["state"],
        })
    for k in groups:
        groups[k].sort(key=lambda x: x.get("installed_at", ""), reverse=True)
    return groups
