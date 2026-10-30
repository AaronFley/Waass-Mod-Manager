"""本地 HTTP 服务。

刻意只用标准库：不装 Flask / FastAPI，打包出来小、没有依赖冲突。
界面就是一个本地网页，跑在 127.0.0.1 的随机端口上，外部访问不到。
"""
from __future__ import annotations

import json
import mimetypes
import os
import re
import secrets
import subprocess
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from core.paths import (DATA_DIR, RES_DIR, detect_game_root, forget_detected,
                        game_layout, load_config, save_config)
from core.scanner import scan
from core import (actions, appinfo, conflicts, downloads, gamever, guard, i18n, identify,
                  library, localize, localize_backup, localize_job, merger,
                  nexus_meta, picker, registry, safety,
                  saves, storage, track, translate, ui as ui_store, winwatch)
from core import install as installer

APP_DIR = Path(__file__).resolve().parent
WEB = RES_DIR / "web"
STATIC = WEB / "static"

TOKEN = secrets.token_urlsafe(24)

# 界面窗口关掉之后，进程要自己退出，否则会留下一个看不见的服务在后台，
# 下次双击还会撞上单实例锁。
#
# 两条判据，缺一不可（1.2.7 补的）：
#   1. 快路径：页面 pagehide 时打一声招呼（/api/bye），等几秒没有新页面接手就退。
#      刷新也会触发，但新页面会立刻请求页面本身，把倒计时取消掉。
#   2. 慢路径：直接问系统还有没有我们的可见窗口（core/winwatch.py）。
#      原因：那一枪不可靠 —— 关窗瞬间渲染进程被收掉、或内核已在拆网络服务时，
#      beacon 根本发不出去，服务端就会一直等一个永远不来的招呼。
#      2026-10-30 实测：窗口关了，进程在后台躺了 40 多分钟。
LIFECYCLE = {
    "bye_at": 0.0,        # 页面说再见的时间（0 = 没说）
    "watch": False,       # 是否开着「关窗就退出」的看守
    "seen_page": False,   # 有没有真的加载过页面（没加载过就别乱退）
    "first_page": 0.0,    # 第一次加载页面的时间
    "last_seen": 0.0,     # 最后一次收到任何请求的时间
    "app_window": False,  # 是不是我们自己的 --app 窗口（浏览器标签页模式不看窗口）
    "started_at": 0.0,    # 窗口是什么时候叫起来的
    "reason": "",         # 退出原因，写进 data/lifecycle.log 方便事后查
}
CLOSE_GRACE = 6.0        # 说了再见之后等几秒，留给刷新
WINDOW_WARMUP = 15.0     # 页面加载后这段时间内不看窗口（窗口可能还没弹出来）
WINDOW_QUIET = 8.0       # 窗口没了的同时，还得安静这么久才认
WINDOW_MISSES = 3        # 连续几次看不到窗口才算数（每次间隔 1 秒）
WINDOW_NEVER = 60.0      # 开了独立窗口却一直没人加载页面：这么久就别等了


def keep_alive() -> bool:
    """调试用：WAASS_KEEP=1 时不许自己退出（跑接口、看窗口都在用）。"""
    return os.environ.get("WAASS_KEEP") == "1"


def mark_app_window() -> None:
    """run.py 用 --app 开出独立窗口之后调一次，慢路径才启用。

    浏览器标签页模式（webbrowser.open 的兜底）不能看窗口：用户切到别的标签，
    标题就变了，会被误判成「窗口没了」。
    """
    LIFECYCLE["app_window"] = True
    LIFECYCLE["started_at"] = time.time()


def touch_page(life: dict | None = None, now: float | None = None) -> None:
    """任何请求都算「还有人在用」。"""
    life = LIFECYCLE if life is None else life
    now = time.time() if now is None else now
    life["last_seen"] = now
    if not life["seen_page"]:
        life["seen_page"] = True
        life["first_page"] = now


def exit_reason(now: float, life: dict, win_count: int,
                grace: float = CLOSE_GRACE,
                warmup: float = WINDOW_WARMUP,
                quiet: float = WINDOW_QUIET,
                never: float = WINDOW_NEVER) -> str:
    """该不该退出。返回 'bye' / 'no-window' / 'no-page' / ''（继续跑）。

    纯函数：时间、窗口数全从参数进，测试直接喂假数据。
    """
    bye = life.get("bye_at") or 0.0
    if bye and now - bye >= grace:
        return "bye"
    if win_count is None or win_count < 0:            # 平台查不了 → 只认 bye
        return ""
    if not life.get("app_window"):
        return ""
    if not life.get("seen_page"):
        # 窗口叫起来了却一直没人加载页面：外壳可能压根没起来，别在这儿躺一整天
        started = life.get("started_at") or 0.0
        if started and now - started >= never:
            return "no-page"
        return ""
    if now - (life.get("first_page") or 0.0) < warmup:
        return ""
    if win_count > 0:
        return ""
    if now - (life.get("last_seen") or 0.0) < quiet:  # 刚还有人说活，可能窗口在重开
        return ""
    return "no-window"


def _log_lifecycle(reason: str) -> None:
    """退出原因留一行痕迹，只留最近 8KB。出问题时有据可查。"""
    try:
        from core.paths import DATA_DIR
        from core import guard
        p = DATA_DIR / "lifecycle.log"
        guard.allow(DATA_DIR)
        guard.assert_can_write(p, "退出日志")
        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        line = f"{stamp}  退出（{reason}）\n"
        old = ""
        if p.is_file():
            old = p.read_text(encoding="utf-8", errors="replace")[-8000:]
        p.write_text(old + line, encoding="utf-8")
    except Exception:                                 # noqa: BLE001 写不了日志也得退
        pass

mimetypes.add_type("text/css", ".css")
mimetypes.add_type("application/javascript", ".js")
mimetypes.add_type("image/svg+xml", ".svg")


class State:
    """进程内的全部可变状态。单线程锁保护，避免并发写坏。"""

    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.scan_result = None
        self.scanning = False
        self.last_error = ""
        self._dl: dict | None = None
        self._dl_at = 0.0

    def game_root(self) -> Path | None:
        return detect_game_root()

    def downloads_summary(self, max_age: float = 20.0) -> dict:
        """下载目录概览。扫目录有点开销，缓存 20 秒。"""
        now = time.time()
        with self.lock:
            if self._dl is not None and now - self._dl_at < max_age:
                return self._dl
        try:
            info = downloads.summary()
        except Exception as exc:                     # noqa: BLE001 别让扫描拖垮界面
            info = {"dirs": [], "total": 0, "new": 0, "error": str(exc)}
        with self.lock:
            self._dl, self._dl_at = info, now
        return info

    def drop_downloads_cache(self) -> None:
        with self.lock:
            self._dl, self._dl_at = None, 0.0

    def conflicts_report(self, force: bool = False) -> dict:
        with self.lock:
            res = self.scan_result
        root = self.game_root()
        if res is None or root is None:
            return {"items": [], "by_mod": {}, "stats": {}, "checked_at": ""}
        try:
            return conflicts.analyze(root, res.mods, force=force)
        except Exception as exc:                     # noqa: BLE001 单项失败不影响主界面
            return {"items": [], "by_mod": {}, "stats": {}, "error": str(exc)}

    def refresh(self) -> None:
        root = self.game_root()
        self.drop_downloads_cache()
        conflicts.drop_cache()
        track.drop_cache()
        if root is None:
            self.scan_result = None
            return
        self.scanning = True
        try:
            self.scan_result = scan(root, DATA_DIR)
            # 1.2.5 起「中文名」那套彻底下线（names.py / nexus.py 已删）：
            # 卡片和详情一律显示游戏/模组自己的名字，和 Nexus、游戏里看到的一致。
            # ID / 页面地址 / 本地版本 / 是否落后 —— 全部本地算，不联网
            track.apply(self.scan_result.mods)
            self.last_error = ""
        except Exception as exc:                     # 扫描失败不能把界面搞崩
            self.last_error = f"{type(exc).__name__}: {exc}"
            self.scan_result = None
        finally:
            self.scanning = False

    def patch_mod(self, key: str, **fields) -> None:
        """改完立刻同步内存里的那条记录，省一次全量重扫。"""
        with self.lock:
            if not self.scan_result:
                return
            for m in self.scan_result.mods:
                if m.key == key:
                    for k, v in fields.items():
                        setattr(m, k, v)
                    return


STATE = State()


def _i18n_script(lang: str) -> str:
    """把当前语言的整份键表塞进首页，写成 JSON 字面量交给前端。

    `</` 必须转义：文案里只要出现 `</script>` 这一段，浏览器就会当场收口，
    后面全部变成页面上的乱码。这种 bug 只在某条文案恰好含标签时出现，
    测不到、也想不到，转义是唯一稳的做法。
    """
    payload = {"lang": lang, "strings": i18n.strings()}
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return raw.replace("</", "<\\/")


def _json(data, status=200):
    return status, "application/json; charset=utf-8", json.dumps(
        data, ensure_ascii=False).encode("utf-8")


def _engine_rows() -> tuple[list[dict], dict]:
    """翻译引擎的现状：能不能用、为什么不能用、配了几个 Key。

    这里不发网络请求 —— 只做本地判断（有没有配 Key、端点是否填了）。
    真连通性由「测一下」按钮单独触发，别让打开面板就等 3 秒超时。
    """
    cfg = translate.config()
    rows: list[dict] = []
    for eid, cls in translate.ENGINES.items():
        ecfg = (cfg.get("engines") or {}).get(eid) or {}
        eng = cls(ecfg)
        ok, why = eng.ready()
        rows.append({
            "id": eid,
            "label": eng.label,
            "on": bool(ecfg.get("on", True)),
            "ready": ok,
            "why": why,
            "keys": len([k for k in (ecfg.get("keys") or []) if str(k).strip()]),
            "keys_list": [str(k) for k in (ecfg.get("keys") or []) if str(k).strip()],
            "needs_key": bool(getattr(eng, "needs_key", False)),
        })
    # 排在前面、且真能用的那个算「当前默认」——和 actual 的引擎链顺序一致
    order = cfg.get("order") or []
    live = [r["id"] for r in rows if r["on"] and r["ready"]]
    live.sort(key=lambda x: order.index(x) if x in order else 99)
    top = live[0] if live else ""
    for r in rows:
        r["default"] = (r["id"] == top)
    return rows, cfg


def _tk_dialog(kind: str) -> str:
    """弹系统选择框（原生优先，见 core/picker.py）。"""
    return picker.pick(kind)


_WARN_KEY_MAX = 200


def _read_settings(root: Path):
    """读游戏侧的 mods.settings，只读。读不到就给 None，调用方要能容忍。"""
    try:
        from core.settings_ini import SettingsFile
        p = game_layout(root)["user_config"] / "mods.settings"
        return SettingsFile.load(p) if p.is_file() else None
    except Exception:                                # noqa: BLE001
        return None


def _banner_state() -> dict:
    """界面上那条警告提示的「该不该出现」。

    警告只是状态播报，不能永远亮着。这里把「用户忽略过的」减掉，只把真正
    需要看的部分交给界面；内容一变指纹就变，提示自动回来。
    """
    with STATE.lock:
        res = STATE.scan_result
    st = ui_store.load()
    ignored = set(st["dismissed_warns"])

    warns: list[dict] = []
    hidden = 0
    for w in (res.warnings if res else []):
        if w.level == "info":
            continue
        key = f"{w.code}|{w.target or ''}"[:_WARN_KEY_MAX]
        if key in ignored:
            hidden += 1
            continue
        warns.append({"key": key, "message": w.message})

    # 1.0.7 起不再下发「下载目录有新包」那段：包的数量不代表任何需要处理的事，
    # 真要装的时候从侧边栏「导入压缩包」进去（那个入口一直在）。
    # 账本里的 dismissed_dl 先留着不动，将来想恢复这个提示不用重写。

    # 「有 N 个模组可更新」：启动自动同步跑完之后，界面上要不要亮那条提示条。
    # 判「是不是同一批」只看名单，不看时间 —— 早前踩过含 mtime 的坑。
    upd = sorted(m.key for m in (res.mods if res else []) if getattr(m, "update_available", False))
    sig = ui_store.digest(upd) if upd else ""
    return {
        "warns": warns,
        "warns_hidden": hidden,
        "updates": {
            "count": len(upd),
            "sig": sig,
            "dismissed": bool(sig) and st.get("dismissed_upd") == sig,
            "names": [m.name for m in (res.mods if res else [])
                      if getattr(m, "update_available", False)][:6],
        },
        # 没对上 Nexus 的模组有几个。1.2.6 之前这里什么都不说，于是新用户
        # （没有任何 ID）看到的是「同步一片安静」，以为功能没实现。
        # 合订本本来就不在 Nexus 上，不算在这里。
        "ids": _id_state(res),
    }


def _id_state(res) -> dict:
    """「还有几个没对上 Nexus」+「其中几个已经找到候选了」。"""
    mods = list(res.mods) if res else []
    # 用户明确标过「这个不在 Nexus 上」的（自制/移植）不算，否则提示条永远消不掉
    muted = track.load_ignored()
    unknown = [m for m in mods
               if not getattr(m, "nexus_id", None)
               and not getattr(m, "is_merged", False)
               and m.key not in muted]
    guessed = 0
    for m in unknown:
        try:
            if (identify.cached_guess(m.folder) or {}).get("items"):
                guessed += 1
        except Exception:                    # noqa: BLE001 读到坏文件不该让界面出错
            pass
    return {"unknown": len(unknown), "guessed": guessed}


class Handler(BaseHTTPRequestHandler):
    server_version = f"Waass/{appinfo.APP_VERSION}"
    protocol_version = "HTTP/1.1"

    # ---------- 基础 ----------
    def log_message(self, fmt, *args):        # 静音，避免刷屏
        pass

    # 只认本机主机名。token 是直接写进首页 HTML 的，而首页本身不鉴权 —— 恶意
    # 网页把自己的域名 rebind 到 127.0.0.1 之后，浏览器会以「同源」身份请求 /，
    # 顺手把 token 读走，后面 install / uninstall 就全通了。校验 Host 是最便宜
    # 的一道锁：正常用法下浏览器只会填 127.0.0.1 或 localhost，不会误伤。
    _LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}

    def _host_ok(self) -> bool:
        raw = (self.headers.get("Host") or "").strip().lower()
        if raw.startswith("["):                       # IPv6 字面量：[::1]:8080
            host = raw.split("]")[0].lstrip("[")
        else:
            host = raw.split(":")[0]
        return host in self._LOCAL_HOSTS

    def _site_ok(self) -> bool:
        """跨站发起的请求一律拒。浏览器会自动带这个头；curl / 本机脚本不带，放行。"""
        return (self.headers.get("Sec-Fetch-Site") or "").lower() != "cross-site"

    def _forbidden(self) -> bool:
        """返回 True 表示这次请求不该继续（已经回了 403）。"""
        if self._host_ok() and self._site_ok():
            return False
        self._send(*_json({"error": "forbidden"}, 403))
        return True

    def _send(self, status: int, ctype: str, body: bytes, extra: dict | None = None):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _api_guard(self) -> bool:
        if self.headers.get("X-Waass-Token") != TOKEN:
            self._send(*_json({"error": "unauthorized"}, 401))
            return False
        return True

    # ---------- 路由 ----------
    def do_GET(self):
        if self._forbidden():
            return
        touch_page()
        u = urllib.parse.urlparse(self.path)
        path = urllib.parse.unquote(u.path)

        if path in ("/", "/index.html"):
            # 有页面在加载 = 不是真的要走（比如按了刷新），取消退出倒计时
            LIFECYCLE["bye_at"] = 0.0
            html = (WEB / "templates" / "index.html").read_text(encoding="utf-8")
            html = html.replace("%%WAASS_TOKEN%%", TOKEN)
            # 语言和整份键表在这里落地。键表内联而不是让前端再发一个请求：
            # 静态文案的填充在脚本一加载就跑，晚一步就会先闪一遍中文。
            lang = i18n.current()
            html = html.replace("%%WAASS_HTML_LANG%%", i18n.HTML_LANG.get(lang, "en"))
            html = html.replace("%%WAASS_I18N%%", _i18n_script(lang))
            return self._send(200, "text/html; charset=utf-8", html.encode("utf-8"))

        if path.startswith("/static/"):
            return self._file(STATIC / path[len("/static/"):])
        if path.startswith("/thumbs/"):
            return self._file(DATA_DIR / "thumbs" / path[len("/thumbs/"):])

        if path == "/api/state":
            if not self._api_guard():
                return
            return self._api_state()
        if path == "/api/health":
            return self._send(*_json({"ok": True}))

        if path == "/api/journal":
            if not self._api_guard():
                return
            return self._send(*_json({
                "items": safety.journal_summary(),
                "game_running": safety.game_running(),
            }))

        if path == "/api/packages":
            if not self._api_guard():
                return
            return self._send(*_json(installer.list_packages()))

        if path == "/api/saves":
            if not self._api_guard():
                return
            return self._send(*_json({"items": saves.list_backups(),
                                      "dir": str(saves.gamesaves_dir(STATE.game_root()))}))

        if path == "/api/archives":
            if not self._api_guard():
                return
            qs = urllib.parse.parse_qs(u.query)
            only = (qs.get("dir") or [""])[0]
            items = downloads.scan()
            if only:
                items = [i for i in items if i["dir"] == only]
            return self._send(*_json({
                "items": items,
                "dirs": downloads.dirs(),
                "new": sum(1 for i in items if not i["known"]),
            }))

        if path == "/api/settings":
            if not self._api_guard():
                return
            return self._send(*_json({"config": load_config(),
                                      "downloads": downloads.summary()}))

        if path == "/api/conflicts":
            if not self._api_guard():
                return
            qs = urllib.parse.parse_qs(u.query)
            return self._send(*_json(
                STATE.conflicts_report(force=bool(qs.get("force")))))

        if path == "/api/merger/state":
            # 只读探针：冲突面板开着时每几秒问一次，用来发现「用户去
            # Script Merger 里合完了」。只读台账的 mtime 和条目数，
            # 不碰 mods 目录，也不触发重扫。
            if not self._api_guard():
                return
            root = STATE.game_root()
            if root is None:
                return self._send(*_json({"found": False}))
            inv = merger.inventory(root)
            st = merger.merged_state(root, inv, _read_settings(root))
            custom = merger._custom_exe()
            return self._send(*_json({
                "found": bool(inv.get("found")),
                "mtime": inv.get("mtime"),
                "count": int(inv.get("count") or 0),
                "names": inv.get("names") or [],
                "merged": st,
                "exe": inv.get("exe"),
                # 手动指定过就标出来，界面要给一句「用的是你指定的那个」
                "custom": bool(custom),
                "custom_path": merger.configured_exe(),
                "dir": inv.get("dir"),
            }))

        if path == "/api/track":
            if not self._api_guard():
                return
            with STATE.lock:
                res = STATE.scan_result
            return self._send(*_json(track.rows(res.visible_mods() if res else [])))

        if path == "/api/nexus/status":
            if not self._api_guard():
                return
            return self._send(*_json(nexus_meta.status()))

        if path == "/api/nexus/search":
            if not self._api_guard():
                return
            qs = urllib.parse.parse_qs(u.query)
            return self._send(*_json(nexus_meta.search((qs.get("q") or [""])[0],
                                                       count=8)))

        if path == "/api/about":
            if not self._api_guard():
                return
            root = STATE.game_root()
            with STATE.lock:
                mods = list(STATE.scan_result.visible_mods()) if STATE.scan_result else []
            counts = {
                "mods": len(mods),
                "enabled": sum(1 for m in mods if m.enabled),
                "packages": len(installer.list_packages().get("active", [])),
                "backups": len(saves.list_backups()),
            }
            return self._send(*_json(appinfo.info(root, DATA_DIR, counts)))

        if path == "/api/storage":
            if not self._api_guard():
                return
            try:
                data = storage.report()
            except Exception as e:                       # noqa: BLE001
                return self._send(*_json({"error": f"{type(e).__name__}: {e}"}, 500))
            return self._send(*_json(data))

        if path == "/api/library":
            if not self._api_guard():
                return
            return self._send(*_json(library.index()))

        if path == "/api/localize":
            if not self._api_guard():
                return
            root = STATE.game_root()
            with STATE.lock:
                res = STATE.scan_result
            if root is None or res is None:
                return self._send(*_json({"mods": [], "error": i18n.t("srv.no_scan_yet")}, 400))
            try:
                rows = localize.inspect(root, res.visible_mods())
            except Exception as e:                   # noqa: BLE001
                return self._send(*_json({"mods": [], "error": f"{type(e).__name__}: {e}"}, 500))
            return self._send(*_json({
                "mods": rows,
                "jobs_dir": str(localize.JOBS_DIR),
                "backup_dir": str(localize.BACKUP_DIR),
            }))

        if path == "/api/localize/panel":
            if not self._api_guard():
                return
            root = STATE.game_root()
            with STATE.lock:
                res = STATE.scan_result
            if root is None or res is None:
                return self._send(*_json({"error": i18n.t("srv.no_scan_yet")}, 400))
            try:
                rows = localize.catalog(root, [m.key for m in res.visible_mods()])
            except Exception as e:                   # noqa: BLE001
                return self._send(*_json({"error": f"{type(e).__name__}: {e}"}, 500))
            engines, cfg = _engine_rows()
            return self._send(*_json({
                "mods": rows,
                "cats": [{"id": k, "label": v, "hint": localize.CAT_HINT.get(k, ""),
                          "default": k in localize.CAT_DEFAULT}
                         for k, v in localize.CAT_LABEL.items()],
                "engines": engines,
                "engine_order": cfg.get("order") or [],
                "records": localize_job.history(8),
                "total_menu": sum(r["menu"] for r in rows),
                "ready_mods": sum(1 for r in rows if r["ready"]),
            }))

        if path == "/api/localize/job":
            if not self._api_guard():
                return
            qs = urllib.parse.parse_qs(u.query)
            jid = (qs.get("id") or [""])[0]
            try:
                return self._send(*_json(localize_job.snapshot(jid)))
            except KeyError as e:
                return self._send(*_json({"error": str(e)}, 404))

        if path == "/api/localize/history":
            if not self._api_guard():
                return
            return self._send(*_json({"records": localize_job.history(20)}))

        self._send(*_json({"error": "not found", "path": path}, 404))

    def do_POST(self):
        if self._forbidden():
            return
        touch_page()
        u = urllib.parse.urlparse(self.path)
        path = u.path
        # 请求体上限：汉化译文导入这类最大也就几 MB，64MB 绰绰有余。
        # 不设上限的话，一个坏掉的 Content-Length 就能把进程拖死。
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except (TypeError, ValueError):
            length = 0
        if length < 0 or length > 64 * 1024 * 1024:
            return self._send(*_json({"error": i18n.t("srv.body_too_large")}, 413))
        raw = self.rfile.read(length) if length else b""
        try:
            body = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            return self._send(*_json({"error": "bad json"}, 400))

        if path == "/api/bye":
            # pagehide 时用 sendBeacon 打的招呼：带不了自定义头，所以用 query 校验
            qs = urllib.parse.parse_qs(u.query)
            if (qs.get("t") or [""])[0] != TOKEN:
                return self._send(*_json({"error": "unauthorized"}, 401))
            if LIFECYCLE["watch"]:
                LIFECYCLE["bye_at"] = time.time()
            return self._send(204, "text/plain", b"")

        if not self._api_guard():
            return

        if path == "/api/refresh":
            t0 = time.time()
            STATE.refresh()
            return self._send(*_json({"ok": True, "ms": int((time.time() - t0) * 1000)}))

        if path == "/api/config":
            if "lang" in body:
                # 切界面语言。只写 config.json 的 lang 一个键，别的配置原样留着。
                # 前端收到 200 之后自己 location.reload()，这边不用管重绘。
                i18n.save_pref(str(body.get("lang") or "auto"))
                return self._send(*_json({"ok": True, "lang": i18n.current(),
                                          "config": load_config()}))
            if body.get("redetect"):
                # 用户点了空态里那个「重新查找」：丢掉探测缓存，按注册表/库清单
                # 从头再走一遍（他可能刚装完游戏、或者刚把 Steam 库加到这块盘）。
                forget_detected()
                root = detect_game_root(refresh=True)
                STATE.refresh()
                return self._send(*_json({"ok": True,
                                          "game_root": str(root or ""),
                                          "config": load_config()}))
            cfg = load_config()
            if body.get("game_root"):
                cand = Path(body["game_root"])
                if not (cand / "bin").is_dir():
                    return self._send(*_json({"error": i18n.t("srv.not_game_root")}, 400))
                cfg["game_root"] = str(cand)
                save_config(cfg)
                STATE.refresh()
            return self._send(*_json({"ok": True, "config": load_config()}))

        if path == "/api/library":
            act = str(body.get("action") or "")
            try:
                if act == "create_group":
                    res = {"ok": True, "group": library.create_group(
                        str(body.get("name") or ""), body.get("members") or None)}
                elif act == "rename_group":
                    res = {"ok": True, "group": library.rename_group(
                        str(body.get("id") or ""), str(body.get("name") or ""))}
                elif act == "delete_group":
                    res = library.delete_group(str(body.get("id") or ""))
                elif act == "set_member":
                    res = library.set_member(str(body.get("id") or ""),
                                             str(body.get("key") or ""),
                                             bool(body.get("on")))
                elif act == "set_tags":
                    res = {"ok": True, "tags": library.set_tags(
                        str(body.get("key") or ""), body.get("tags") or [])}
                elif act == "auto_group":
                    with STATE.lock:
                        res = STATE.scan_result
                    res = library.auto_group_by_category(
                        res.visible_mods() if res else [])
                else:
                    return self._send(*_json({"error": i18n.t("srv.unknown_action", act=act)}, 400))
            except (ValueError, KeyError) as e:
                return self._send(*_json({"error": str(e)}, 400))
            return self._send(*_json(res))

        if path == "/api/localize/jobs":
            root = STATE.game_root()
            with STATE.lock:
                res = STATE.scan_result
            if root is None or res is None:
                return self._send(*_json({"error": i18n.t("srv.no_scan_yet")}, 400))
            keys = body.get("mods") or None
            try:
                jobs = localize.build_jobs(root, [str(k) for k in keys] if keys else None)
            except Exception as e:                   # noqa: BLE001
                return self._send(*_json({"error": f"{type(e).__name__}: {e}"}, 500))
            return self._send(*_json({
                "count": jobs["count"], "file": jobs["file"],
                "items": jobs["items"][:4],
            }))

        if path == "/api/localize/apply":
            # 三种来源：服务端文件路径 / 前端直接贴的 JSON / JSON 文本
            data = body.get("data")
            fpath = str(body.get("file") or "")
            try:
                if not data:
                    if not fpath:
                        return self._send(*_json({"error": i18n.t("srv.no_translation_source")}, 400))
                    data = localize.load_jobs(fpath)
            except (FileNotFoundError, ValueError) as e:
                return self._send(*_json({"error": str(e)}, 400))
            root = STATE.game_root()
            try:
                if root is not None:
                    safety.authorize_game(root)
                safety.assert_writable()
                res = localize.apply_jobs(data)
            except safety.GameRunning as e:
                return self._send(*_json({"error": str(e)}, 409))
            except guard.WriteDenied as e:
                return self._send(*_json({"error": i18n.t("srv.write_denied", err=e)}, 403))
            except Exception as e:                   # noqa: BLE001
                return self._send(*_json({"error": f"{type(e).__name__}: {e}"}, 500))
            if res.get("changed"):
                STATE.refresh()
            return self._send(*_json(res))

        if path == "/api/localize/run":
            root = STATE.game_root()
            if root is None:
                return self._send(*_json({"error": i18n.t("srv.no_scan_yet")}, 400))
            keys = [str(k) for k in (body.get("mods") or [])]
            if not keys:
                return self._send(*_json({"error": i18n.t("srv.pick_mods_first")}, 400))
            cats = body.get("cats")
            try:
                job = localize_job.start(root, keys,
                                         [str(c) for c in cats] if cats else None)
            except ValueError as e:
                return self._send(*_json({"error": str(e)}, 400))
            except Exception as e:                   # noqa: BLE001
                return self._send(*_json({"error": f"{type(e).__name__}: {e}"}, 500))
            return self._send(*_json(job))

        if path == "/api/localize/job/cancel":
            try:
                return self._send(*_json(localize_job.cancel(str(body.get("id") or ""))))
            except KeyError as e:
                return self._send(*_json({"error": str(e)}, 404))

        if path == "/api/localize/write":
            root = STATE.game_root()
            try:
                res = localize_job.write(str(body.get("id") or ""),
                                         edits=body.get("edits") or None,
                                         root=root,
                                         only=body.get("only") or None)
            except KeyError as e:
                return self._send(*_json({"error": str(e)}, 404))
            except safety.GameRunning as e:
                return self._send(*_json({"error": str(e)}, 409))
            except guard.WriteDenied as e:
                return self._send(*_json({"error": i18n.t("srv.write_denied", err=e)}, 403))
            except ValueError as e:
                return self._send(*_json({"error": str(e)}, 400))
            except Exception as e:                   # noqa: BLE001
                return self._send(*_json({"error": f"{type(e).__name__}: {e}"}, 500))
            if res.get("changed"):
                STATE.refresh()
            return self._send(*_json(res))

        if path == "/api/localize/restore":
            root = STATE.game_root()
            mode = str(body.get("mode") or "orig")
            if mode not in ("orig", "hist"):
                return self._send(*_json({"error": i18n.t("srv.bad_mode")}, 400))
            try:
                res = localize_job.restore(body.get("record") or None, mode=mode,
                                           force=bool(body.get("force")), root=root)
            except safety.GameRunning as e:
                return self._send(*_json({"error": str(e)}, 409))
            except guard.WriteDenied as e:
                return self._send(*_json({"error": i18n.t("srv.write_denied", err=e)}, 403))
            except Exception as e:                   # noqa: BLE001
                return self._send(*_json({"error": f"{type(e).__name__}: {e}"}, 500))
            if res.get("restored"):
                STATE.refresh()
            return self._send(*_json(res))

        if path == "/api/localize/config":
            try:
                translate.save_config(body or {})
            except Exception as e:                   # noqa: BLE001
                return self._send(*_json({"error": f"{type(e).__name__}: {e}"}, 400))
            engines, cfg = _engine_rows()
            return self._send(*_json({"ok": True, "engines": engines,
                                      "engine_order": cfg.get("order") or []}))

        if path == "/api/localize/probe":
            # 真发一次请求，测「现在这个网络下到底能不能翻」。会慢，别自动跑。
            try:
                rows = translate.probe()
            except Exception as e:                   # noqa: BLE001
                return self._send(*_json({"error": f"{type(e).__name__}: {e}"}, 500))
            return self._send(*_json({"ok": True, "engines": rows}))

        if path == "/api/localize/history":
            return self._send(*_json({"records": localize_job.history(20)}))

        if path == "/api/localize/forget":
            # 只有用户明确点「删掉汉化备份」才会到这里
            if not body.get("confirm"):
                return self._send(*_json({"error": i18n.t("srv.confirm_required")}, 400))
            return self._send(*_json(localize_backup.drop_all(confirm=True)))

        if path == "/api/track":
            act = str(body.get("action") or "")
            try:
                if act == "set":
                    res = track.set_manual(str(body.get("key") or ""),
                                           str(body.get("ref") or ""),
                                           str(body.get("version") or ""))
                elif act == "clear":
                    res = track.clear_manual(str(body.get("key") or ""))
                elif act == "ignore":
                    # 自制/移植的模组不在 Nexus 上，标一下就不再提醒。
                    # 没有这个出口的话，「还有 N 个没对上 Nexus」会永远挂着，
                    # 提示条从此再也消不掉。
                    res = track.set_ignored(str(body.get("key") or ""),
                                            bool(body.get("on", True)))
                else:
                    return self._send(*_json({"error": i18n.t("srv.unknown_action", act=act)}, 400))
            except ValueError as e:
                return self._send(*_json({"error": str(e)}, 400))
            STATE.refresh()
            with STATE.lock:
                res = STATE.scan_result
            out = track.rows(res.visible_mods() if res else [])
            out.update({"ok": True})
            return self._send(*_json(out))

        if path == "/api/app/check":
            return self._send(*_json(appinfo.check()))

        if path == "/api/nexus/autosync":
            # 启动时后台自动同步一次：补封面 + 查有没有新版本。
            # 只取「一天以前的」缓存，所以同一天里反复开程序几乎不花时间。
            # 全程静默：没配 ID、断网、Nexus 抽风都不弹错，只如实回报结果。
            with STATE.lock:
                mods = list(STATE.scan_result.mods) if STATE.scan_result else []
            ids = [m.nexus_id for m in mods if m.nexus_id]
            if not ids:
                return self._send(*_json({"ok": True, "skipped": "没有带 Nexus ID 的模组",
                                          "requested": 0, "got": 0, "covers": 0,
                                          "ui": _banner_state()}))
            res = nexus_meta.refresh(ids, with_cover=True, days=1)
            if res.get("got") or res.get("covers"):
                STATE.refresh()
            res.update({"ok": True, "ui": _banner_state()})
            return self._send(*_json(res))

        # ---------- Nexus 资料 ----------
        if path == "/api/nexus/refresh":
            ids = body.get("ids")
            if ids is None:
                with STATE.lock:
                    mods = list(STATE.scan_result.mods) if STATE.scan_result else []
                ids = [m.nexus_id for m in mods if m.nexus_id]
            if not isinstance(ids, list):
                return self._send(*_json({"error": i18n.t("srv.ids_must_be_list")}, 400))
            res = nexus_meta.refresh([str(x) for x in ids],
                                     with_cover=bool(body.get("cover", True)),
                                     force=bool(body.get("force")))
            if res.get("got"):
                STATE.refresh()
            return self._send(*_json(res))

        if path == "/api/nexus/link":
            # 搜索到一个候选，用户点了「就是这个」，就把它的 ID 记下来
            key = str(body.get("key") or "").strip()
            mod_id = str(body.get("mod_id") or "").strip()
            if not key or not mod_id.isdigit():
                return self._send(*_json({"error": i18n.t("srv.missing_mod_or_id")}, 400))
            try:
                track.set_manual(key, mod_id)
            except ValueError as e:
                return self._send(*_json({"error": str(e)}, 400))
            got = nexus_meta.fetch([mod_id])
            rec = got.get(mod_id) or {}
            if rec and body.get("cover", True):
                nexus_meta.download_cover(mod_id,
                                          rec.get("thumb") or rec.get("picture"))
            track.drop_cache()
            STATE.refresh()
            return self._send(*_json({"ok": True, "nexus_id": mod_id,
                                      "meta": rec}))

        if path == "/api/nexus/guess":
            # 本地证据（自己记过的 / 安装档案 / 下载目录里的原包）都认不出的模组，
            # 拿名字去 Nexus 搜候选。**只备菜不点菜**：一个都不自动绑，等人在
            # 界面上点确认。绑错 ID 会让「检查更新」指向别人，比空着更糟。
            # 搜过的结果存三天，同一天反复开程序不会重复打 Nexus。
            with STATE.lock:
                mods = list(STATE.scan_result.mods) if STATE.scan_result else []
            force = bool(body.get("force"))
            limit = int(body.get("limit") or 10)
            # 合订本和「已标记不在 Nexus 上」的不搜 —— 搜了也永远搜不到，
            # 白占限速额度，还让缓存里堆一堆无关记录。
            muted = track.load_ignored()
            todo = [m for m in mods if not m.nexus_id
                    and not getattr(m, "is_merged", False) and m.key not in muted]
            out: dict = {}
            searched = 0
            error = ""
            for m in todo:
                rec = None if force else identify.cached_guess(m.folder)
                if rec is None:
                    if searched >= max(1, min(limit, 20)):
                        continue                     # 这次先不搜，下次启动接着补
                    try:
                        # 拆好词再搜：文件夹名是 modSwordsOnHip 这种连写，
                        # 直接丢给 Nexus 的关键词匹配是搜不出东西的。
                        # 多档降级也是必须的 —— `auto loot enhanced` 一条都搜不到，
                        # 放宽成 `auto loot` 才有；只发全词等于永远空着。
                        hits = nexus_meta.search_multi(
                            identify.search_queries(m.name or m.folder), count=8)
                    except nexus_meta.NexusError as exc:
                        error = str(exc)
                        break                        # 网络不通就别继续挨个撞
                    # 两条路都走一遍再合并：自己硬盘里的原包是最硬的证据
                    # （`SmartAutoLoot_Remaster_13035.zip` 就在那儿），联网搜索
                    # 覆盖得到本地没有的，但排在前面的常常是同名别家版本。
                    local = identify.candidates(m.name or m.folder, m.version or "")
                    remote = identify.shortlist(hits.get("items") or [],
                                                m.name or m.folder, m.version or "")
                    items = identify.merge_candidates(local, remote)
                    identify.save_guess(m.folder, items)
                    searched += 1
                    rec = {"items": items}
                if rec.get("items"):
                    # 键用 m.key 而不是 m.folder：禁用中的模组文件夹带 `~` 前缀
                    # （`~modXxx`），而界面上的行是按去掉 `~` 的 key 索引的。
                    # 用 folder 当前缀的话，那几个带 `~` 的模组永远看不到候选。
                    out[m.key] = {"name": m.name, "items": rec["items"]}
            pending = sum(1 for m in todo if not (identify.cached_guess(m.folder) or {}).get("items"))
            return self._send(*_json({"ok": True, "requested": len(todo),
                                      "searched": searched, "count": len(out),
                                      "pending": pending, "error": error,
                                      "items": out, "ui": _banner_state()}))

        if path == "/api/open-url":
            url = str(body.get("url") or "")
            if not re.match(r"^https://(www\.)?nexusmods\.com/", url):
                return self._send(*_json({"error": i18n.t("srv.only_nexus_links")}, 400))
            os.startfile(url)                        # noqa: S606 已限定域名
            return self._send(*_json({"ok": True}))

        if path == "/api/mods/toggle":
            root = STATE.game_root()
            if root is None:
                return self._send(*_json({"error": i18n.t("srv.game_root_not_found")}, 400))
            key = str(body.get("key") or "")
            enabled = bool(body.get("enabled"))
            try:
                res = actions.set_enabled(root, key, enabled)
            except safety.GameRunning as e:
                return self._send(*_json({"error": str(e)}, 409))
            except (FileNotFoundError, FileExistsError) as e:
                return self._send(*_json({"error": str(e)}, 400))
            STATE.patch_mod(key, enabled=enabled)
            return self._send(*_json(res))

        if path == "/api/mods/batch_toggle":
            root = STATE.game_root()
            if root is None:
                return self._send(*_json({"error": i18n.t("srv.game_root_not_found")}, 400))
            keys = body.get("keys")
            if not isinstance(keys, list) or not keys:
                return self._send(*_json({"error": i18n.t("srv.no_mods_selected")}, 400))
            enabled = bool(body.get("enabled"))
            try:
                res = actions.set_enabled_many(
                    root, [str(k) for k in keys], enabled)
            except safety.GameRunning as e:
                return self._send(*_json({"error": str(e)}, 409))
            except (FileNotFoundError, FileExistsError, ValueError) as e:
                return self._send(*_json({"error": str(e)}, 400))
            for k in res["keys"]:
                STATE.patch_mod(k, enabled=enabled)
            return self._send(*_json(res))

        if path == "/api/mods/priority":
            root = STATE.game_root()
            if root is None:
                return self._send(*_json({"error": i18n.t("srv.game_root_not_found")}, 400))
            key = str(body.get("key") or "")
            try:
                prio = int(body.get("priority"))
            except (TypeError, ValueError):
                return self._send(*_json({"error": i18n.t("srv.priority_must_be_int")}, 400))
            try:
                res = actions.set_priority(root, key, prio)
            except safety.GameRunning as e:
                return self._send(*_json({"error": str(e)}, 409))
            STATE.patch_mod(key, priority=prio)
            return self._send(*_json(res))

        if path == "/api/reorder":
            root = STATE.game_root()
            if root is None:
                return self._send(*_json({"error": i18n.t("srv.game_root_not_found")}, 400))
            keys = body.get("keys") or []
            if not isinstance(keys, list) or not keys:
                return self._send(*_json({"error": i18n.t("srv.empty_order_list")}, 400))
            prios = body.get("prios")
            if prios is not None and not isinstance(prios, list):
                return self._send(*_json({"error": i18n.t("srv.prios_must_be_array")}, 400))
            try:
                res = actions.reorder(root, [str(k) for k in keys], prios)
            except ValueError as e:
                return self._send(*_json({"error": str(e)}, 400))
            except safety.GameRunning as e:
                return self._send(*_json({"error": str(e)}, 409))
            STATE.refresh()
            return self._send(*_json(res))

        if path == "/api/undo":
            try:
                safety.assert_writable()
            except safety.GameRunning as e:
                return self._send(*_json({"error": str(e)}, 409))
            ok, msg = safety.undo_last()
            if ok:
                STATE.refresh()
            return self._send(*_json({"ok": ok, "message": msg}, 200 if ok else 400))

        if path == "/api/open-folder":
            root = STATE.game_root()
            key = str(body.get("key") or "")
            if key == "@translate":                  # 汉化任务包所在目录
                d = localize.JOBS_DIR
                d.mkdir(parents=True, exist_ok=True)
                os.startfile(str(d))                 # noqa: S606 本程序自己的目录
                return self._send(*_json({"ok": True}))
            if key == "@data":                       # 本程序自己的数据目录
                DATA_DIR.mkdir(parents=True, exist_ok=True)
                os.startfile(str(DATA_DIR))          # noqa: S606 本程序自己的目录
                return self._send(*_json({"ok": True}))
            if key == "@userconfig":                 # 巫师3 的用户配置目录（input.settings 等在这里）
                # 只开目录不开文件：os.startfile 对可执行后缀会直接运行，
                # 而这个路径来自前端参数，不能让它决定打开哪个文件。
                if root is None:
                    return self._send(*_json({"error": i18n.t("srv.game_root_not_found")}, 400))
                d = game_layout(root)["user_config"]
                if not d.is_dir():
                    return self._send(*_json({"error": i18n.t("srv.config_dir_missing")}, 404))
                os.startfile(str(d))                 # noqa: S606 已限定为游戏配置目录
                return self._send(*_json({"ok": True}))
            if root is None or not key:
                return self._send(*_json({"error": i18n.t("srv.missing_params")}, 400))
            lay = game_layout(root)
            # key 是前端传上来的文件夹名。不校验的话 `..\..\` 能跳出 mods，
            # 变成「打开机器上任意文件夹」—— 危害有限，但没有理由留着这个口子。
            # 要求解出来的父目录必须就是 mods 本身。
            try:
                mods_dir = lay["mods"].resolve()
            except OSError:
                return self._send(*_json({"error": i18n.t("srv.mods_dir_unusable")}, 404))
            for cand in (lay["mods"] / key, lay["mods"] / ("~" + key)):
                try:
                    inside = cand.resolve().parent == mods_dir
                except OSError:
                    inside = False
                if not inside:
                    continue
                if cand.is_dir():
                    os.startfile(str(cand))       # noqa: S606 本地路径，已校验
                    return self._send(*_json({"ok": True}))
            return self._send(*_json({"error": i18n.t("srv.folder_not_found")}, 404))

        if path == "/api/merger/open":
            # 只启动，不代跑合并。改文件是 Script Merger 自己的事，
            # 我们既不知道它合并了什么，也不该替用户点那几下鼠标。
            root = STATE.game_root()
            if root is None:
                return self._send(*_json({"error": i18n.t("srv.game_root_not_found")}, 400))
            exe = merger.exe_path(root)
            if exe is None:
                return self._send(*_json(
                    {"error": i18n.t("srv.merger_not_found")},
                    404))
            try:
                safety.assert_writable()
            except safety.GameRunning as e:
                return self._send(*_json({"error": str(e)}, 409))
            try:
                subprocess.Popen(                       # noqa: S603 固定路径，无参数
                    [str(exe)], cwd=str(exe.parent),
                    close_fds=True,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except OSError as e:
                return self._send(*_json(
                    {"error": i18n.t("srv.merger_start_failed", err=e)}, 500))
            conflicts.drop_cache()
            return self._send(*_json({"ok": True, "exe": str(exe),
                                      "hint": i18n.t("srv.merger_recheck_hint")}))

        if path == "/api/merger/locate":
            # 装在别处的 Script Merger：让用户自己指一次 exe，记住它。
            # 为什么不自动扫全盘：Script Merger 的目录名就叫 Merger，
            # 全盘找一分钟起步，而且盘上可能有已经废弃的旧副本。
            root = STATE.game_root()
            if root is None:
                return self._send(*_json({"error": i18n.t("srv.game_root_not_found")}, 400))
            if body.get("clear"):
                merger.clear_exe()
                conflicts.drop_cache()
                return self._send(*_json({"ok": True, "cleared": True}))
            try:
                picked = picker.pick("exe")
            except OSError as e:
                return self._send(*_json({"error": i18n.t("srv.picker_open_failed", err=e)}, 500))
            if not picked:
                return self._send(*_json({"ok": False, "cancelled": True}))
            p = Path(picked)
            name = p.name.lower()
            if not name.endswith(".exe"):
                return self._send(*_json({"error": i18n.t("srv.pick_merger_exe")}, 400))
            # 不硬性要求文件名完全匹配（改名/汉化版都有），但提醒一下，
            # 免得用户随手挑了个别的程序，后面「打开」按钮点开一片空白。
            suspicious = not any(k in name for k in ("merger", "script"))
            try:
                merger.set_exe(p)
            except FileNotFoundError as e:
                return self._send(*_json({"error": str(e)}, 400))
            conflicts.drop_cache()
            return self._send(*_json({
                "ok": True, "exe": str(p), "parent": str(p.parent),
                "suspicious": suspicious,
            }))

        # ---------- 安装 / 卸载 ----------
        if path in ("/api/preview", "/api/install"):
            root = STATE.game_root()
            if root is None:
                return self._send(*_json({"error": i18n.t("srv.game_root_not_found")}, 400))
            archive = str(body.get("archive") or "").strip().strip('"')
            if not archive:
                return self._send(*_json({"error": i18n.t("srv.no_archive_path")}, 400))
            if not Path(archive).is_file():
                return self._send(*_json({"error": i18n.t("srv.archive_not_found", path=archive)}, 400))
            try:
                if path == "/api/preview":
                    res = installer.preview(archive, root)
                else:
                    res = installer.install(
                        archive, root,
                        allow_risk=bool(body.get("allow_risk")),
                        on_conflict=str(body.get("on_conflict") or "abort"),
                        dry_run=bool(body.get("dry_run")),
                    )
                    if not body.get("dry_run"):
                        STATE.refresh()
            except installer.NeedsConfirm as e:
                return self._send(*_json({"error": str(e), "need_confirm": e.detail}, 409))
            except installer.InstallError as e:
                return self._send(*_json({"error": str(e)}, 400))
            except safety.GameRunning as e:
                return self._send(*_json({"error": str(e)}, 409))
            except guard.WriteDenied as e:
                return self._send(*_json({"error": i18n.t("srv.write_denied_action", err=e)}, 403))
            except Exception as e:                       # noqa: BLE001 解压类错误统一露出
                return self._send(*_json({"error": f"{type(e).__name__}: {e}"}, 500))
            return self._send(*_json(res))

        if path in ("/api/uninstall", "/api/restore"):
            root = STATE.game_root()
            if root is None:
                return self._send(*_json({"error": i18n.t("srv.game_root_not_found")}, 400))
            pkg_id = str(body.get("package_id") or "")
            if not pkg_id:
                return self._send(*_json({"error": i18n.t("srv.no_package_id")}, 400))
            try:
                res = (installer.uninstall if path == "/api/uninstall" else installer.restore)(
                    pkg_id, root)
            except installer.InstallError as e:
                return self._send(*_json({"error": str(e)}, 400))
            except safety.GameRunning as e:
                return self._send(*_json({"error": str(e)}, 409))
            except guard.WriteDenied as e:
                return self._send(*_json({"error": i18n.t("srv.write_denied_action", err=e)}, 403))
            STATE.refresh()
            return self._send(*_json(res))

        if path == "/api/uninstall-all":
            root = STATE.game_root()
            if root is None:
                return self._send(*_json({"error": i18n.t("srv.game_root_not_found")}, 400))
            try:
                res = installer.uninstall_all(root)
            except safety.GameRunning as e:
                return self._send(*_json({"error": str(e)}, 409))
            STATE.refresh()
            return self._send(*_json(res))

        if path == "/api/settings":
            act = str(body.get("action") or "")
            try:
                if act == "add_dir":
                    res = downloads.add_dir(str(body.get("path") or ""))
                elif act == "remove_dir":
                    res = downloads.remove_dir(str(body.get("path") or ""))
                elif act == "reset_dirs":
                    res = downloads.reset_dirs()
                elif act == "set_game_root":
                    cand = Path(str(body.get("path") or ""))
                    if not (cand / "bin").is_dir():
                        return self._send(*_json({"error": i18n.t("srv.not_game_root")}, 400))
                    cfg = load_config()
                    cfg["game_root"] = str(cand)
                    save_config(cfg)
                    STATE.refresh()
                    res = {"ok": True, "config": load_config()}
                elif act == "set_option":
                    cfg = load_config()
                    for k in ("confirm_before_install",
                              "remember_last_dir", "nexus_auto_meta",
                              "nexus_auto_cover"):
                        if k in body:
                            cfg[k] = bool(body[k])
                    save_config(cfg)
                    res = {"ok": True, "config": cfg}
                else:
                    return self._send(*_json({"error": i18n.t("srv.unknown_action", act=act)}, 400))
            except FileNotFoundError as e:
                return self._send(*_json({"error": str(e)}, 400))
            except OSError as e:
                return self._send(*_json({"error": f"{type(e).__name__}: {e}"}, 500))
            return self._send(*_json(res))

        if path == "/api/pick-file":
            kind = str(body.get("kind") or "file")
            try:
                picked = _tk_dialog(kind)
            except Exception as e:                       # noqa: BLE001 没有 GUI 环境
                return self._send(*_json({"error": i18n.t("srv.dialog_open_failed", err=e)}, 500))
            return self._send(*_json({"ok": True, "path": picked}))

        if path == "/api/ui/dismiss":
            kind = str(body.get("kind") or "")
            if kind == "warn":
                keys = body.get("keys")
                if not isinstance(keys, list):
                    keys = [body.get("key")]
                ui_store.forget_warns([str(k) for k in keys if k])
            elif kind == "dl":
                ui_store.forget_dl(str(body.get("sig")
                                       or STATE.downloads_summary().get("new_sig") or ""))
            elif kind == "upd":
                ui_store.forget_upd(str(body.get("sig") or ""))
            else:
                return self._send(*_json({"error": i18n.t("srv.unknown_dismiss_kind")}, 400))
            return self._send(*_json({"ok": True, "ui": _banner_state()}))

        if path == "/api/localize/peek":
            # 「从文件读取」选完文件先看一眼内容：几条任务、几条已填译文。
            # 让用户知道文件读进来了，而不是点了没反应。
            try:
                data = localize.load_jobs(str(body.get("file") or ""))
            except (FileNotFoundError, ValueError) as e:
                return self._send(*_json({"error": str(e)}, 400))
            items = data.get("items") or []
            filled = sum(1 for it in items if str(it.get("dst") or "").strip())
            return self._send(*_json({
                "ok": True,
                "file": str(body.get("file") or ""),
                "count": len(items),
                "filled": filled,
                "note": str(data.get("note") or ""),
            }))

        # ---------- 存档 ----------
        if path == "/api/saves/backup":
            root = STATE.game_root()
            try:
                res = saves.backup(root, mode=str(body.get("mode") or "full"),
                                   note=str(body.get("note") or ""))
            except FileNotFoundError as e:
                return self._send(*_json({"error": str(e)}, 400))
            return self._send(*_json(res))

        if path == "/api/saves/restore":
            root = STATE.game_root()
            try:
                res = saves.restore_backup(str(body.get("id") or ""), root)
            except FileNotFoundError as e:
                return self._send(*_json({"error": str(e)}, 400))
            return self._send(*_json(res))

        # ---------- 存储与清理 ----------
        if path == "/api/storage/clean":
            ids = body.get("ids")
            if ids is not None and not isinstance(ids, list):
                return self._send(*_json({"error": i18n.t("srv.ids_must_be_list")}, 400))
            try:
                res = storage.clean([str(x) for x in (ids or [])],
                                    only_safe=bool(body.get("safe_only")))
            except guard.WriteDenied as e:
                return self._send(*_json({"error": str(e)}, 403))
            res["after"] = storage.report()
            return self._send(*_json(res))

        self._send(*_json({"error": "not found", "path": path}, 404))

    # ---------- 具体实现 ----------
    def _file(self, p: Path):
        p = p.resolve()
        # 白名单必须是「目录内」，不能用 startswith —— 否则 data/thumbs_evil
        # 这类兄弟目录也能蹭过前缀检查。
        def _under(child: Path, parent: Path) -> bool:
            try:
                child.relative_to(parent)
                return True
            except ValueError:
                return False
        if not (_under(p, STATIC.resolve()) or _under(p, (DATA_DIR / "thumbs").resolve())) \
                or not p.is_file():
            return self._send(404, "text/plain", b"not found")
        ctype = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
        return self._send(200, ctype, p.read_bytes())

    def _api_state(self):
        with STATE.lock:
            res = STATE.scan_result
            scanning = STATE.scanning
            err = STATE.last_error
        root = STATE.game_root()
        cfg = load_config()
        payload = {
            "app": {"name": appinfo.APP_NAME, "version": appinfo.APP_VERSION,
                    "date": appinfo.APP_DATE, "repo": appinfo.APP_REPO},
            "config": cfg,
            "game_found": root is not None,
            # 游戏版本（主菜单上那个 5.01c）。读 exe 里的内嵌字符串，带缓存；
            # 读不到就给空，界面不显示这个标签，而不是显示个错的。
            "game_version": gamever.detect(root) if root else {"version": "", "src": "", "exe": ""},
            # 游戏在跑的时候一切写操作都会被 guard 拒。界面要提前知道这件事，
            # 否则用户点二十下开关才在最后一次报错里看到原因。
            "game_running": safety.game_running(),
            "scanning": scanning,
            "error": err,
            "scan": res.to_dict() if res else None,
            "packages": installer.list_packages(),
            "saves": saves.list_backups()[:5],
            "saves_dir": str(saves.gamesaves_dir(root)),
            "unknown_mods": installer.unknown_mods(root) if root else [],
            "ui": _banner_state(),
            "conflicts": STATE.conflicts_report(),
            "library": library.index(),
            "writable": [str(p) for p in guard.allowed_roots()],
            "layout": ({k: str(v) for k, v in game_layout(root).items()} if root else None),
        }
        return self._send(*_json(payload))


def _bg_enrich(force: bool = False) -> None:
    """启动后补齐资料：一次批量请求把缺的补上，然后收工。

    为什么要等一会儿：开局要先把界面画出来、游戏目录扫完，联网不能占前排。
    全程只发一到两个请求，资料新鲜的话一个都不发。
    """
    time.sleep(2.5)
    try:
        cfg = load_config()
        if not cfg.get("nexus_auto_meta", True):
            return
        if STATE.scan_result is None:
            STATE.refresh()
        with STATE.lock:
            res = STATE.scan_result
            mods = list(res.mods) if res else []
        ids = [m.nexus_id for m in mods
               if m.nexus_id and (force or not nexus_meta.is_fresh(m.nexus_id))]
        if not ids:
            return
        out = nexus_meta.refresh(ids, with_cover=cfg.get("nexus_auto_cover", True))
        if out.get("got"):
            track.drop_cache()
            STATE.refresh()
    except Exception:                                # noqa: BLE001 后台任务，失败就安静
        pass


def serve(port: int = 0) -> ThreadingHTTPServer:
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    httpd.daemon_threads = True
    return httpd


def _graceful_exit(httpd: ThreadingHTTPServer, reason: str) -> None:
    """退出。清理缓存这一步可能被占用/杀毒拖住，所以先挂一个 3 秒的保险丝。"""
    LIFECYCLE["reason"] = reason
    _log_lifecycle(reason)
    threading.Timer(3.0, os._exit, (0,)).start()
    # 退出前把浏览器内核的临时文件带走，别留几 MB 垃圾在硬盘上
    try:
        storage.trim_browser_cache()
    except Exception:                                # noqa: BLE001
        pass
    try:
        httpd.shutdown()
    except Exception:                                # noqa: BLE001
        pass
    os._exit(0)


def _watch_close_loop(httpd: ThreadingHTTPServer, grace: float = CLOSE_GRACE) -> None:
    """界面窗口关了就把自己关掉，别留后台进程。

    快路径看页面打的那声招呼，慢路径看系统里还有没有窗口（见 LIFECYCLE 上方注释）。
    """
    misses = 0
    while True:
        time.sleep(1.0)
        if keep_alive():
            continue
        try:
            n = winwatch.count() if LIFECYCLE["app_window"] else -1
        except Exception:                            # noqa: BLE001 查窗口失败就当不知道
            n = -1
        reason = exit_reason(time.time(), LIFECYCLE, n, grace=grace)
        if reason in ("bye", "no-page"):
            _graceful_exit(httpd, reason)
        if reason == "no-window":
            misses += 1
            if misses >= WINDOW_MISSES:
                _graceful_exit(httpd, f"{reason}({n})")
            continue
        misses = 0


def start_background(port: int = 0, watch_close: bool = False) -> tuple[ThreadingHTTPServer, int]:
    i18n.bootstrap()          # 按 config.json 的 lang 定语言（缺省跟随系统）
    httpd = serve(port)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    if watch_close:
        LIFECYCLE["watch"] = True
        threading.Thread(target=_watch_close_loop, args=(httpd,), daemon=True).start()
    threading.Thread(target=_bg_enrich, daemon=True).start()
    return httpd, httpd.server_address[1]
