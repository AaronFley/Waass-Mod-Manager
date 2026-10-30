"""写保护闸门：默认拒绝所有写入。

为什么单独做一层
----------------
2026-10-25 出过一次事故：测试脚本第一版忘了设隔离环境变量，把用户的真实
配置目录当成沙盒，写了他两次 mods.settings。内容恰好没变，但这是不该发生的。

结论：**不靠"记得设环境变量"，改成默认全拒**。任何写入必须满足其一：
  1. 路径在显式授权的白名单根目录之内（`allow()`），并且没开只读开关；
  2. 处于测试模式（WAASS_TEST_MODE=1），路径在 WAASS_SANDBOX_DIR 之内。

漏了授权 → 抛 WriteDenied，程序当场失败并报错，绝不静默写真实目录。
"""
from __future__ import annotations

import os
from pathlib import Path

ENV_TEST_MODE = "WAASS_TEST_MODE"
ENV_SANDBOX = "WAASS_SANDBOX_DIR"
ENV_READONLY = "WAASS_READONLY"

_allowed: list[Path] = []        # 目录（含子目录）
_allowed_files: list[Path] = []  # 单个文件，且只有这个文件
_denied_hits: list[str] = []


class WriteDenied(RuntimeError):
    """不是异常流程，是硬保护触发。看到它就说明代码漏了授权。"""


def _norm(p: str | Path) -> Path:
    try:
        return Path(p).resolve()
    except (OSError, RuntimeError):
        return Path(os.path.abspath(str(p)))


def _under(child: Path, parent: Path) -> bool:
    try:
        child.relative_to(parent)
        return True
    except ValueError:
        return False


# ---------------- 模式 ----------------

def test_mode() -> bool:
    return os.environ.get(ENV_TEST_MODE) == "1"


def readonly() -> bool:
    v = os.environ.get(ENV_READONLY, "").strip().lower()
    return v in {"1", "true", "yes", "on"}


def sandbox() -> Path | None:
    v = os.environ.get(ENV_SANDBOX)
    return _norm(v) if v else None


# ---------------- 授权 ----------------

def allow(*roots: str | Path) -> list[Path]:
    """授权一个目录（含子目录）可写。测试模式下只认沙盒内的目录。"""
    added: list[Path] = []
    for r in roots:
        if r is None:
            continue
        p = _norm(r)
        if test_mode():
            sb = sandbox()
            if sb is None:
                raise WriteDenied(
                    f"测试模式必须先用 {ENV_SANDBOX} 指定沙盒根目录，否则不许授权任何路径"
                )
            if not _under(p, sb):
                raise WriteDenied(f"测试模式拒绝授权沙盒外的路径：{p}")
        if p not in _allowed:
            _allowed.append(p)
            added.append(p)
    return added


def allow_file(*paths: str | Path) -> list[Path]:
    """只授权这一个文件，它的兄弟文件和子路径都不许写。

    用来把 mods.settings 单独放行，而不用把整个「文档/The Witcher 3」
    （里面有存档）都开放出去。
    """
    added: list[Path] = []
    for r in paths:
        if r is None:
            continue
        p = _norm(r)
        if test_mode():
            sb = sandbox()
            if sb is None or not _under(p, sb):
                raise WriteDenied(f"测试模式拒绝授权沙盒外的文件：{p}")
        if p not in _allowed_files:
            _allowed_files.append(p)
            added.append(p)
    return added


def deny_all() -> None:
    _allowed.clear()
    _allowed_files.clear()


def allowed_roots() -> list[Path]:
    return list(_allowed) + list(_allowed_files)


def denied_hits() -> list[str]:
    return list(_denied_hits)


# ---------------- 核心检查 ----------------

def is_writable(path: str | Path) -> bool:
    try:
        assert_can_write(path)
        return True
    except WriteDenied:
        return False


def assert_can_write(path: str | Path, what: str = "") -> None:
    """不满足条件就抛 WriteDenied，附一句人话原因。"""
    p = _norm(path)
    label = f"（{what}）" if what else ""

    if readonly():
        _denied_hits.append(str(p))
        raise WriteDenied(f"程序处在只读模式{label}，不会写任何文件：{p}")

    if p in _allowed_files:
        return

    for root in _allowed:
        if p == root or _under(p, root):
            return

    _denied_hits.append(str(p))
    if test_mode():
        sb = sandbox()
        raise WriteDenied(
            f"测试模式禁止写沙盒外的路径{label}：{p}"
            + (f"\n沙盒是：{sb}" if sb else "")
        )
    raise WriteDenied(
        f"这个路径没有写入授权{label}：{p}\n"
        "如果这是你主动发起的安装/卸载，说明程序漏了授权步骤，请把这个提示发给我。"
    )


def assert_can_write_many(paths, what: str = "") -> None:
    """批量预检：先全部检查一遍，有一个不合适就整体不干。"""
    for p in paths:
        assert_can_write(p, what)


def describe() -> dict:
    """给 doctor 工具和界面用的当前状态。"""
    return {
        "test_mode": test_mode(),
        "sandbox": str(sandbox() or ""),
        "readonly": readonly(),
        "allowed": [str(p) for p in allowed_roots()],
        "denied_count": len(_denied_hits),
    }
