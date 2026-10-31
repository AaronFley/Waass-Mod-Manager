---
name: 问题反馈 / Bug report
about: 程序打不开、功能不对、界面异常、模组装不上
title: ""
labels: ""
assignees: ""
---

## 先说一句

**别贴完整的 `mods.settings`**，里面有你的模组列表就够了，但请不要带上任何账号信息。
下面四项里，前三项几乎决定了能不能定位问题。

## 1. 程序版本

设置 → 关于，形如 `Waass 1.3.0`。

## 2. 环境

- Windows 版本：Win10 / Win11，多少位
- 游戏版本：重制版 5.01c？还是别的
- 游戏装在哪个盘无所谓，但请说明是 **Steam / GOG / Epic / 其他**

## 3. 出了什么事

- 你点了什么、期望看到什么、实际看到什么
- 如果能复现，写一下步骤（第 1 步、第 2 步……）
- 有没有红条/黄条提示？上面那句话原样抄过来

## 4. 日志

在程序的 `data/` 目录里，这两个文件通常就够：

| 文件 | 里面是什么 |
| --- | --- |
| `crash.log` | 崩溃堆栈（没有就是没崩过） |
| `lifecycle.log` | 启动、关窗、自退的记录 |

直接把文件拖进来即可。**上传前请扫一眼**，确认没有你不想公开的路径。

## English

Version (`Settings → About`), Windows version, game version and store, what you
clicked / expected / got, and `data/crash.log` + `data/lifecycle.log` if they exist.
Please skim the logs before uploading.
