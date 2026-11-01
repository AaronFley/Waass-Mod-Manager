# Network access

**English** · [简体中文](#简体中文)

## English

This page covers everything Waass Mod Manager does on the network: when it connects, what it sends, what it never sends, and how to turn all of it off. It is meant to be checked against the code, not taken on trust.

There are three outbound points. There are no others.

### 1. Nexus metadata

| | |
| --- | --- |
| Endpoint | `api-router.nexusmods.com` (`POST /graphql`) |
| When | Installing a mod, once shortly after launch, and when you click refresh |
| Sent | The IDs of mods already in your mods folder. No account, no key, and no identifier beyond the program's own name and version |
| Received | Public information only: name, author, version, summary, category, thumbnail, download count |
| Default | On |
| Turn off | **Settings → Nexus metadata**, or go offline |

This is a read-only query endpoint. If it is unavailable, entries show *unrecorded*; installing, scanning, and uninstalling are unaffected.

### 2. Update check

| | |
| --- | --- |
| Endpoint | `api.github.com` by default, and configurable |
| When | Ten seconds after launch, and when you click **Check for updates** |
| Sent | Nothing. The request only reads the version number of the latest release |
| Received | A version number and a link to the release page |
| Default | On |
| Turn off | Set `"update_check": false` in `data/config.json` (there is no switch in the UI) |

The program never downloads or replaces files on its own. When a newer version exists it shows a notice; opening the release page is your choice.

### 3. Localization translation

| | |
| --- | --- |
| Endpoints | `index-translate.bilibili.com` and `api.mymemory.translated.net`; `fanyi-api.baidu.com` only if you add your own key |
| When | Only when you start a translation, and only for the mods you selected |
| Sent | The menu strings you chose to translate |
| Received | Their Chinese translations |
| Default | Asks once before anything is sent. Nothing leaves the machine until you agree |
| Turn off | Decline the first prompt, or switch every engine off in the **Translation engines** dialog in the localization panel |

The first-run prompt lists the services that will actually receive text, based on the engines currently enabled.

### What is never done

- No file downloads. The program never fetches mod archives or program updates by itself
- No login and no account; no feature requires a key
- No collection of user data: no usage statistics, no analytics — and beyond what the three points above list, no mod list, file names, or paths leave the machine
- No telemetry, no crash reports, no analytics of any kind
- Nothing is uploaded anywhere; every request above only reads
- Script error text you paste into the conflicts dialog is matched on this machine only. It is never sent, stored or written to disk

### Turning all of it off

Each of the three points can be turned off — two have a switch in the UI, the update check is a line in `data/config.json`. Turning them off is not a degraded mode with a catch: no request is made at all.

With no network connection, the program remains fully usable. Scanning, enabling and disabling mods, load order, conflict detection, installing from a local archive, uninstalling, cleanup, and restoring localization originals all behave exactly as they do online. Only the three points above stop: metadata stays *unrecorded*, update checks go quiet, and translation is unavailable.

---

## 简体中文

本文说明 Waass Mod Manager 在网络上做的一切：什么时候连、发什么、绝不发什么、以及怎么全部关掉。它是拿来对着代码核对的，不是让你相信的。

一共三个出网点，没有第四个。

### 1. Nexus 资料

| | |
| --- | --- |
| 端点 | `api-router.nexusmods.com`（`POST /graphql`） |
| 时机 | 安装模组时、启动后不久补一次、以及你点刷新时 |
| 发送 | 已在 `mods` 目录里的模组编号。没有账号、没有 Key，除程序自己的名称与版本号外不带任何标识 |
| 接收 | 只有公开信息：名称、作者、版本、简介、分类、缩略图、下载量 |
| 默认 | 开 |
| 关闭 | **设置 → Nexus 资料**，或直接断网 |

这是一个只读查询接口。它不可用时，条目显示「未记录」；安装、扫描、卸载都不受影响。

### 2. 程序更新检查

| | |
| --- | --- |
| 端点 | 默认 `api.github.com`，可更换 |
| 时机 | 启动十秒后，以及你点「检查程序更新」时 |
| 发送 | 无。请求只读取最新发布页的版本号 |
| 接收 | 一个版本号和该发布页的链接 |
| 默认 | 开 |
| 关闭 | 在 `data/config.json` 写 `"update_check": false`（界面上没有这个开关） |

程序不会自行下载或替换任何文件。有新版本时只给出提示，是否打开发布页由你决定。

### 3. 汉化翻译

| | |
| --- | --- |
| 端点 | `index-translate.bilibili.com`、`api.mymemory.translated.net`；只有你自己填了密钥才会用 `fanyi-api.baidu.com` |
| 时机 | 只有你主动发起汉化时，且只处理你勾选的模组 |
| 发送 | 你选中要翻译的菜单文字 |
| 接收 | 这些文字的中文译文 |
| 默认 | 先问一次，同意之前不发送任何内容 |
| 关闭 | 第一次弹窗时不同意，或在汉化面板的**翻译方式**里关掉所有引擎 |

首次说明中的服务清单按当前启用的引擎生成 —— 写的就是这次真会收到文字的那几个。

### 绝不做的

- 不下载任何文件。程序不会自行获取模组压缩包或程序更新
- 不登录、不使用账号，任何功能都不需要 Key 才能使用
- 不收集用户数据：没有使用统计、没有埋点；除上面三处明确列出的内容外，模组列表、文件名、路径都不会离开这台机器
- 没有遥测、没有崩溃上报、没有任何形式的埋点
- 不向任何地方上传内容；上面每一条请求都只是读取
- 你粘进「冲突检查」的报错文字只在本机匹配，不发送、不保存、不写进任何文件

### 全部关掉之后

三个出网点都能关掉 —— 两个在界面上有开关，更新检查是 `data/config.json` 里的一行。关掉不是「功能受限但还能凑合」：是一条请求都不再发。

关闭网络后，程序仍完整可用。扫描、启用与禁用、加载顺序、冲突检查、从本地压缩包安装、卸载、清理、还原汉化原件，行为与联网时完全一致。只有上面三件事停下来：资料保持「未记录」，更新检查不再出声，汉化不可用。
