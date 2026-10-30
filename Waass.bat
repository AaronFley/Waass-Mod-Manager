@echo off
cd /d "%~dp0"

rem 只用 PATH 里的 Python。写死某个用户的安装路径，换台机器必然失效 ——
rem 而这个脚本本来就是给「不想用 exe、想跑源码」的人准备的。
rem 注意：本文件必须是 GBK 编码。cmd 按当前代码页逐字节解析 bat，
rem 存成 UTF-8 再 chcp 65001 会让带括号的 if 块解析错位（实测报「语法不正确」）。

where pythonw >nul 2>nul
if not errorlevel 1 (
    start "" pythonw run.py
    exit /b
)

where python >nul 2>nul
if not errorlevel 1 (
    start "" python run.py
    exit /b
)

echo.
echo   没找到 Python。
echo.
echo   装一个 Python 3.10 或更高版本，安装时记得勾选
echo   "Add python.exe to PATH"，然后重新双击本文件。
echo.
echo   不想装 Python 的话，直接下载 Releases 里的 exe，那个自带运行时。
echo.
pause
