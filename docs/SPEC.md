# Waass Mod Manager — 技术规范

> 这份文档是"事实底稿"。代码写错了以这里为准，这里写错了以实测为准，
> 实测结果要回写到这份文档里。

## 1. 目录结构

```
<游戏根目录>\                        D:\game\steam\steamapps\common\The Witcher 3
├─ bin\x64_dx12\witcher3.exe        判定"这是不是巫师3"的唯一依据
├─ bin\x64_dx12\*.dll               注入型 mod 读写这里（如 dsound.dll / w3loadfix.dll）
├─ bin\config\r4game\user_config_matrix\pc\
│    ├─ audio.xml … localization.xml   游戏自带 10 个，永不算 mod
│    ├─ <ModName>.xml                  正常生效的 mod 菜单
│    └─ <ModName>.xml.disabled         被改名停用的 mod 菜单
├─ mods\modXXX\                     内容型 mod 的主体
│    └─ content\scripts\  content\*.w3strings  bin\config\...\pc\*.xml
├─ dlc\dlcXXX\                      与某个 mod 配套的资源包
└─ ~modXXX  / ~dlcXXX               文件夹名带 ~ = 停用（老办法）

<用户配置目录>\                       D:\1\The Witcher 3（文档目录被重定向到这）
├─ mods.settings                     启用状态与加载顺序的唯一权威
├─ user.settings / input.settings    游戏设置
└─ gamesaves\
```

## 2. 启用 / 禁用：三套机制，必须都认

| 机制 | 表现 | 说明 |
|---|---|---|
| 文件夹改名 | `~modX` | **1.2.6 起本程序的写入口**。引擎只扫 `mod` 开头的目录，`~` 前缀等于没装 |
| `mods.settings` | `[modX]` + `Enabled=0` | 官方支持；本程序只读它，不再拿它当开关 |
| 菜单改名 | `X.xml.disabled` | 只停用设置菜单，主功能仍在 |

本程序的原则：
- 读的时候三种都认，任一为"停用"即视为停用
- **写的时候只改文件夹名**（`actions.set_enabled`）：
  - 禁用 = `modX` → `~modX`，启用 = 改回来，各是一次 `rename`，日志可撤销
  - **不写 `mods.settings`** —— 那文件零注释、段间恰好一个空行，
    Script Merger 的解析器不容忍任何多余字符，能不动就不动
  - `Priority` 原样保留，所以禁用再启用之后加载顺序不变
- 唯一的例外：启用时发现 `mods.settings` 里那条记录是 `Enabled=0`
  （旧版本程序或用户手动关的），必须翻成 `1`，否则名字改回来了游戏照样不加载
- DLC 的启用状态跟随主 mod（同样用 `~` 前缀）

## 3. mods.settings 硬规则

格式极其苛刻。**Script Merger 的解析器不支持注释**，写了注释会让它崩
`NullReferenceException`（2026-10-20 实测踩过）。

```
[modModSettingsMenuFix]
Enabled=1
Priority=10

[modHoods]
Enabled=1
Priority=100
```

1. 第一个字符必须是 `[`
2. 每段只有三行：`[名字]` / `Enabled=` / `Priority=`
3. 段之间一个空行
4. 零注释、零标题、零说明
5. 不写 BOM

## 4. 加载顺序

- 数值越小越先加载（优先级越高）
- 同名 Priority 会让 Script Merger 报"加载顺序相同"
- 编号习惯：按功能分区留空位（10/20/30…），不要用 1/2/3 连号
- 建议基线：`mod0000_MergedFiles` = 0（如果做过脚本合并）

## 5. Nexus API（v1）

> **1.0 现状**：界面不再调用 API。改成 `core/track.py` 的纯本地台账
> （ID / 页面地址 / 本地版本 + 跳转按钮），`core/nexus.py` 保留但未接入。
> 下面这段留作以后做「可选联网检查」时的参考。

- 鉴权：请求头 `apikey: <Personal API Key>`
- 查单个 mod：`GET https://api.nexusmods.com/v1/games/witcher3/mods/{mod_id}.json`
  → `version` / `updated_timestamp` / `author` / `category_id` / `picture_url`
- 查文件列表：`GET .../mods/{mod_id}/files.json`
- 追踪列表：`GET /v1/user/tracked_mods.json`
- 校验 key：`GET /v1/users/validate.json`

**下载限制（决定了自动更新的上限）**

| 账号 | 能否通过 API 拿直链 |
|---|---|
| Premium | 可以，直接调 `files/{file_id}/download_link.json` |
| 免费 | 不行，必须带网页 `nxm://` 链接里的 `key` + `expires` |

所以免费账号的"自动更新"实现方式是：注册 `nxm://` 协议 → 用户点网页上的
「Mod Manager Download」→ 本程序接管 → 用链接里的凭证换直链 → 下载安装。
用户的动作是"点一下"，其余全自动。

- 限速：逐个请求间隔 ≥ 1.05 秒，遇 429 退避重试

## 6. 安全约束（不可妥协）

1. 扫描全程只读
2. 任何写操作前，先把受影响的文件快照到 `data/snapshots/<时间戳>/`
3. 卸载 = 移到 `data/quarantine/`，不做真删除
4. `witcher3.exe` 在运行 → 拒绝一切写操作
5. 每次写操作进撤销栈，支持一键回滚
6. 不碰游戏本体文件（`content\`、`bin\x64_dx12\*.exe`、存档）
7. 服务只监听 `127.0.0.1`，带一次性 token

## 7. 已知的实现难点

- **w3strings**：目前社区工具（含本仓库 `witcher3/tools/w3strings_zh.py`）只能用
  "等长替换"——中文必须比原文短。要做到中文自由长度，必须完整读写索引表。
  这是 M6 汉化功能的前置条件。
- **mod 归属识别**：没有标准清单文件。本程序用三级策略：
  RMMR 存档 → 文件夹名相似度 → readme 里的 Nexus 链接。
- **脚本冲突**：真正的冲突判定需要解析 `.ws` 逻辑路径并比对，
  RMMR 交给 Script Merger 做；本程序先做逻辑路径比对（覆盖判定），
  真正的脚本合并仍建议交给 Script Merger。

## 8. 数据目录与生命周期（1.0.1 定案）

### 8.1 旧的第三方资料只搬一次

第一次扫描时读一次别的管理器的存档，把 Nexus ID / 作者 / 分类 / 封面搬进
`data/legacy.json` 和 `data/thumbs/`，写 `migrated_at` 作为"搬过了"的标记。
之后**扫描器不再碰那个软件**（`scanner.py` 只调 `legacy.load()`）。

- 搬不到（没装过）就不写标记，以后装了还能自动搬；
- 设置里「再导入一次」走 `force=True`；
- `legacy.forget()` 只清资料，不动 mod、不动缩略图、不动中文名。

> 这一条是为了"独立运行"。原先每次扫描都读外部存档，等于把可用性绑在别人身上。

### 8.2 界面窗口是浏览器内核，必须驯服

界面用 `msedge/chrome --app` 开独立窗口，`--user-data-dir=data/webview`。
**踩过的坑**：给一个全新 user-data-dir，浏览器会当成"刚装完浏览器"，
用 Windows 登录的微软账号做隐式登录，弹「我们正在你的所有设备上同步你的浏览数据」。

必须带的参数（`run.py` 的 `open_app_window`）：

```
--no-first-run --no-default-browser-check --no-service-autorun
--disable-sync                       # 关同步
--disable-features=msEdgeIdentityFeatures,msImplicitSignin,msEdgeSyncPromo,
                   EdgeSyncPromo,SigninInterception,msEdgeFirstRunExperience
--disable-background-networking --disable-component-update --disable-breakpad
--disk-cache-size=16777216 --media-cache-size=1048576
```

另外预置 `webview/First Run` 标记文件，并在启动前 / 退出后各清一次缓存
（`storage.trim_browser_cache()`）。实测：运行中 12 MB → 退出后 2.2 MB。

保留 `Default/Local Storage`（主题、上次视图）和 `Network`（删了会重新触发登录）。

### 8.3 进程生命周期

页面 `pagehide` 时 `navigator.sendBeacon('/api/bye?t=<token>')`，服务端记下时间；
watchdog 每 1 秒看一眼，超过 6 秒没有新页面（`GET /` 会清掉这个标记）就
`storage.trim_browser_cache()` 然后 `os._exit(0)`。

这样既不会留后台进程，也不会在 F5 刷新时把服务误杀。

### 8.4 清理边界（硬规则）

- 删除范围只来自 `storage.GROUPS` 里硬编码的相对路径，不接受外部传路径；
- 每个待删路径必须通过 `_inside_data()`（在 `DATA_DIR` 内且不是 `DATA_DIR` 本身）
  和 `guard.assert_can_write()`（测试模式强制沙盒、只读模式全拒）；
- 核心数据（`config/track/names/library/installed/legacy/integrity/journal` + `saves/`）
  不进任何清理列表，只统计体积；
- 自动上限：快照 30 份、汉化备份 20 个、撤销日志 200 条、`crash.log` 覆盖写。

## 9. Nexus 资料自动获取（1.0.2）

### 9.1 官方 API 不用，但也不能什么都不做

官方 API（`api.nexusmods.com`）要 Personal Key，实测无 Key 直接 `401`。用户明确不想填 Key。
但"新装一个 mod 却不知道它是谁"是真问题 —— 没有 ID 就没有作者、封面、版本对比。

实测三条路的结论（2026-10-25）：

| 路径 | 结果 |
|---|---|
| `www.nexusmods.com/witcher3/mods/<id>` HTML | **403**，Cloudflare `Just a moment...` |
| 同上，用 Edge `--headless=new` 加载 | 还是 challenge 页 |
| `api.nexusmods.com/v1/...` | 401，要 Key |
| `staticdelivery.nexusmods.com`（图片 CDN） | 可直连（404/200，不是 403） |
| **`POST api-router.nexusmods.com/graphql`** | **200，不需要登录、不需要 Key** |

最后那个是 Nexus 新版网页前端自己在用的接口。它的 query 里有 `legacyMods(ids:[{gameId,modId}])`
和 `mods(filter:...)`，返回 `Mod` 类型：`name / author / version / summary / category /
pictureUrl / thumbnailUrl / downloads / endorsements / updatedAt / adultContent`。
witcher3 的 `gameId` 固定 **952**。

### 9.2 文件名解析：两种格式都要认

这是最关键的一环 —— 解析不出 ID，后面全空。Nexus 下载名有两代：

```
新：Light_Rewrite_12144_0.26.2_2026-10-23T03-19Z_864tMl89U.zip
老：Hoods-4242-3-7-1698765432.zip
    All Quest Markers-13087-1-7-3-1698765432.7z
    UI Remastered - Next Gen-14578-1-1-2-1698765432.zip
```

**老格式版本段数不定**（1~4 段），还用 `-` 和 `_` 混着，一条正则啃不下来。做法是从右往左数：

1. 最后一段是 9~13 位纯数字 → Unix 时间戳；
2. 往左连续的数字段（最多 4 段）→ 版本；
3. 再往左那一段必须是 **3~6 位纯数字** → Nexus ID，否则整条判为"认不出"；
4. 剩下的是名字。

第 3 步的位数限制是防误判的关键：`Vesemir-2-1-1698765432.zip` 里 `2` 只有 1 位，
宁可不认也不瞎猜一个 ID 出来 —— 猜错的后果是把别的 mod 的作者和封面贴到它身上。

### 9.3 ID 的四条来源（按优先级）

| 顺序 | 来源 | 何时 |
|---|---|---|
| 1 | `data/track.json` | 你手填 / 搜索确认过的，永远最高 |
| 2 | `data/legacy.json` | 旧管理器一次性搬过来的存档 |
| 3 | mod 目录里的 README / `.url` | 里面写着 nexusmods 链接 |
| 4 | 文件名 | 见 9.2 |

### 9.4 数据流

```
安装 → 解析文件名得 ID → refresh() → 缓存 data/nexus/<id>.json（7 天）
                          └→ 缩略图 → data/thumbs/nexus_<id>.<ext>（约 30 KB）
track.apply() 只读缓存（不联网）→ 贴到 ModRecord → 界面
启动 → 后台 _bg_enrich()：把过期/缺失的 ID 批量问一次，然后收工
```

`track.apply()` **绝不联网**，它是每次刷新界面都会跑的。联网只发生在三处：
安装时、启动后台补一次、你点「刷新资料」。

### 9.5 边界（硬规则）

- 只发 POST 到那一个 GraphQL 端点，只读公开数据；不登录、不带账号、不下载 mod 文件。
- 全局最小间隔 0.8 秒，一次批量最多 60 个 ID（实测 17 个一次 1.08 秒）。
- 失败后冷却 5 分钟，期间不再试；失败一律 `return {}`，绝不阻塞安装/扫描。
- 缓存 7 天；`force=True`（手动刷新）才无视。
- 设置里 `nexus_auto_meta` / `nexus_auto_cover` 可关，关掉后整条链路退化成纯本地。
- 清理由 `storage` 的 `nexus` 组管（safe 档，删了会重新抓）。

### 9.6 搜索配对只给候选，不自动选

`mods(filter:{nameStemmed:{value:<关键词>,op:MATCHES}})` 是词干匹配，很宽松：
搜 "Hoods" 会带出法语翻译版、中文翻译版、以及 "Dynamic Swords on Hip"。
所以程序只把候选列出来（按下载量排序），**必须由人点一个确认**。
自动选第一条会有一半概率贴错 mod 的资料，比不显示更糟。

搜不出来时给两条路：打开网页自己搜（`/search/?gsearch=`）、或者手填 ID。

## 10. 界面提示的「已读」账本（1.0.3）

顶部那条提示（`N 处需要注意`）是**状态播报**，不是待办。
没有已读状态时，只要状态不变它就每次开软件都亮，看起来像有没处理完的错误。

账本：`data/ui.json`

```json
{ "dismissed_warns": ["conflict_folder~disabled|modXXX", "..."], "dismissed_dl": "3f9a1c..." }
```

- **警告按条记**。key = `f"{code}|{target or ''}"`，截断到 `_WARN_KEY_MAX`。
  被记过的从 `ui.warns` 里剔掉，只把剩下的交给界面。
  `level == "info"` 的警告本身就进不了这个通道，不算在计数里。
- 最多存 200 条（`KEEP_MAX`），防文件无限长。
- 删掉 `ui.json` 无副作用，只是提示重新开始。

接口：`POST /api/ui/dismiss`，body `{kind:"warn", keys:[...]}`，
返回新的 `ui` 段，前端直接替换 `S.data.ui` 而不重拉全量状态。

**1.0.7 变更**：`下载目录有 N 个新包` 那条提示整个撤掉了 —— 包的数量不代表任何
需要处理的事（用户原话：「没什么用」）。改动：

- `server.py::_banner_state()` 不再返回 `dl` 段，也不再调 `downloads_summary()`
  （省掉一次下载目录扫描）；`/api/state` 里也不再下发 `downloads` 段。
- `web/static/app.js::renderStatus()` 删掉整段 chip 渲染；`index.html` 删掉
  `#btnNewArchives` 元素；`app.js` 里对应的点击监听也一并删（元素没了还
  `addEventListener` 会让整个脚本崩）。
- 侧边栏「导入压缩包」按钮是替代入口，一直在。
- `core/ui.py` 的 `dismissed_dl` 记账逻辑**保留不用**，将来想恢复提示不用重写。

## 11. 系统对话框必须用原生 API（1.0.3）

`core/picker.py`。**别改回 tkinter。**

tkinter 的 `filedialog` 是独立 Tk 顶层窗口，而调用它的是 HTTP 请求线程（后台线程）。
Windows 的前台焦点规则会把这种窗口压到别的窗口下面 —— 用户点了「从文件读取」，
看起来毫无反应，实际弹框藏在主界面背后。取消时也没有任何回调，所以什么都不会发生。

原生 `comdlg32.GetOpenFileNameW` 有两个决定性好处：

1. `hwndOwner` 设成主界面窗口（`_owner_hwnd()`，按标题 `Waass Mod Manager` 找），
   对话框必定盖在主界面上，也跟着主界面一起最小化/关闭；
2. 过滤器是系统样式，能按 `kind` 分开（`json` 只给 `*.json`，`archive` 给 `*.zip;*.7z;*.rar`）。

细节：

- `CommDlgExtendedError() == 0` 表示用户取消 → 返回 `""`，不是错误；非 0 才抛 `OSError`。
- `OFN_NOCHANGEDIR` 必须带，否则系统会悄悄改掉本进程的工作目录。
- **选目录仍然走 Tk**。实测从 HTTP 线程调 `SHBrowseForFolder` 会卡半分钟才返回，
  Tk 的目录框反而当场响应。这是唯一保留 Tk 的地方，原因写在 `picker.pick` 的 docstring 里。

前端配套：选完必须给回执 —— `读到 N 条，其中 M 条已填译文`（`POST /api/localize/peek`），
取消也要提示，否则用户依然分不清"没选"和"坏了"。

## 12. 图标规格（1.0.3）

`tools/make_icon.py` 纯标准库生成，无第三方依赖。产物在 `web/static/`。

**尺寸**：`.ico` 里放 16/20/24/32/40/48/64/96/128/256 共十档，每档**单独渲染**而不是缩放。

**存储格式**：小于 256 用老式 BMP(DIB) 条目，256 用 PNG。
小尺寸塞 PNG 在部分 Windows 组件里支持不完整，会退回拿大图缩 —— 同样是糊。

**笔画粗细不是等比缩放**。抗锯齿按像素覆盖率算，尺寸越小边缘占比越大，
"比例正确"的笔画在小图上会被灰边吞掉。所以给 16~48 一个半宽下限：

| 尺寸 | 笔画直径 | 占 W 宽度 |
| --- | --- | --- |
| 16 | 2.10 px | 25% |
| 32 | 3.00 px | 18% |
| 48 | 3.56 px | 14% |
| 64+ | 等比（13%） | 13% |

下限表在 `STROKE_HALF`，等比系数在 `STROKE_RATIO`（0.065 = 半宽占 W 跨度，直径即 13%）。
**要求随尺寸单调变粗**，改表时用 `half_width()` 打印一遍核对，别让 48 比 64 还粗。

**页面只挂一个 `.ico`**（`index.html`）：

```html
<link rel="icon" type="image/x-icon" href="/static/icon.ico?v=4">
<link rel="shortcut icon" href="/static/icon.ico?v=4">
```

**不要**再额外挂 `sizes="16x16"` 的单尺寸 PNG。页面上只要出现小尺寸条目，
外壳就可能拿那张小图去撑任务栏，放大后必然发虚 —— 这正是 1.0.2 模糊的成因之一。
改动图标后要同时改 `?v=N`，否则浏览器 favicon 数据库里还是旧图。

## 13. 授权必须按「实际要写的路径」算（1.0.4）

`core/safety.py::authorize_rels(root, rels)`。**安装 / 卸载 / 恢复三处都必须用它。**

### 事故经过

2026-10-26 装 `SmartAutoLoot Remaster V1.1.7` 失败，报
`这个路径没有写入授权（写入前预检）：...\bin\config\base\autoloot.ini`。

那个包有三类内容：

| 类别 | 例子 | 归类 |
| --- | --- | --- |
| mod 本体 | `mods/modAutoLoot/` | `mod_folder` |
| 菜单 | `bin/config/r4game/user_config_matrix/pc/modAutoLoot.xml` | `menu_xml` |
| 游戏配置 | `bin/config/base/autoloot.ini` | `bin_config` |

而安装端当时写的是：

```python
if plan.bin_binaries:          # ← 只有 dll/exe 才放开 bin
    guard.allow(root / "bin")
```

`autoloot.ini` 不是二进制，`bin_binaries` 为空 → `bin/` 整层没被授权 →
`bin/config/base/` 永远写不进去。**这个漏洞从 M3 装功能起就存在**，
只在「带游戏配置文件的包」上触发，所以前面几个包都没暴露。

### 现在的规则

`authorize_rels` 按每条目标相对路径判断，只放开必要的一层：

| 目标路径 | 放开 |
| --- | --- |
| `bin/config/**` | 只放开 `bin/config`（.ini/.xml，不含可执行文件） |
| 其它 `bin/**` | 放开整个 `bin`（dll/exe，`risk=high`，要 `allow_risk` 二次确认） |
| 其它（mods/、dlc/） | 由 `authorize_game()` 负责，这里不重复 |

**不要退回到「看 plan 里有没有某类文件」的写法** —— 分类一旦新增（就像这次的
`bin_config`），授权就会漏。以目标路径为准，新分类自动覆盖。

### 卸载也补上了闸门

`uninstall()` / `restore()` 以前直接 `shutil.move`，**根本不经过 `guard`**，
等于写保护对卸载无效。现在两处都是：

```python
safety.authorize_rels(root, pkg["paths"])
guard.allow(DATA_DIR, QUARANTINE)
safety.assert_writable(*[_abs(root, rel) for rel in pkg["paths"]])
```

顺带的好处：游戏在跑时卸载也会被 `assert_writable` 挡住（以前不会）。

### 界面

`bin_config` 不列进主清单（会太长），单独一段黄色提示「会改游戏自带的配置文件」，
并说明卸载时会一起还原。它 **不** 计入 `risk=high` —— 改 ini 不会让游戏起不来，
和 dll 不是一回事，混在一起会让真正的高危提示贬值。

## 14. 游戏自带配置的合并写入（1.0.5）

`core/config_merge.py`（纯函数）+ `core/config_patches.py`（基线 + 账本）。
**接进 `core/install.py` 的 install / uninstall / restore 三处，缺一处就等于没做。**

### 为什么

2026-10-26 用户提出：覆盖游戏自带配置文件时应该用合并而不是替换，卸载时也应只删
mod 写入的那几行而不是删掉整个文件。抽查他的下载目录（377 个压缩包，33 个巫师3 mod）：

| 写到哪 | 包数 | 说明 |
| --- | --- | --- |
| `mods/modXXX/` | 全部 | 各自占一个文件夹，天然不冲突 |
| `dlc/` | 8 | 同上 |
| `user_config_matrix/pc/modXXX.xml` | 21 | **都是新建自己的菜单文件**，不改原版 |
| `bin/config/base/*.ini` | 1 | RemasteredBloom → `visuals.ini` |
| `bin/x64_dx12/*.dll` | 3 | W3LoadFix / PT Optimization / winmm 类 |

所以真正踩到「覆盖原版」的概率不高，但踩到就很脏。最典型的证据是
`Debug Console` 的 `general.ini`：

```ini
; 游戏里           ; 包里
[General]         [General]
ConfigVersion=7   ConfigVersion=5
                  DBGConsoleOn=true
```

整份替换会把配置格式从 7 降到 5，而 mod 真正需要的只是加一行 `DBGConsoleOn=true`。
这就是「必须合并」的实证。

### 两条硬规则（改代码前先读）

1. **只加和改，从不按「包里没写」删键。**
   多数 mod 附的是片段文件，少数附完整文件。两种都不能因为「这个键包里没有」
   就判定游戏该删它 —— 那等于用 mod 作者的版本覆盖掉你游戏的全部参数。
2. **还原时当前值 != 当初写入的值 → 不硬还原，报冲突。**
   说明已被另一个 mod 或用户手工改过。硬还原会吃掉别人的改动。
   需要强制时用 `revert(..., force=True)`，只有测试和"确实要回原版"才用。

### 受保护字段

`PROTECTED_KEYS = {"configversion"}` —— 游戏自己管理的配置格式版本。
包里想改它就标成 `skip`，预览里解释原因，不写进文件。
**不要因为「某个 mod 想改」就扩大这个集合**，加之前先确认那个键不是游戏自管的状态。

### 数据结构

`data/config_patches.json`

```json
{ "packages": { "<pkg_id>": { "name": "...", "files": {
    "bin/config/base/general.ini": {
      "format": "ini",
      "changes": [{"op":"add","section":"general","key":"dbgconsoleon",
                   "value":"true","old":"","line":"DBGConsoleOn=true",
                   "section_raw":"General"}],
      "applied_at": "...", "baseline": "data/baseline/bin/config/base/general.ini"
} } } }
```

- `changes` 里同时存 `skip` 项 —— 卸载时要靠它解释「为什么这条没撤」。
- `line` 存包里的原始行，插入时照抄，保留键的大小写。
- `section_raw` 存节名原始写法。**ini 节名大小写敏感**，写回 `[autoLoot]` 等于没生效，
  这个坑踩过一次。

`data/baseline/<相对路径>` —— 第一次动这个文件之前的内容，永不被覆盖。
作为整份还原的安全网存在，**必须留在 `core/storage.py` 的 `KEEP_DIRS` 里**，
不能进清理清单。

### 卸载语义

| 文件类型 | 卸载动作 |
| --- | --- |
| mod 文件夹 / dlc / 菜单 xml / dll | 搬进 `data/quarantine/<id>/`（可恢复） |
| 合并过的 `bin/config/*.ini` | **不搬**。反向撤销 changes：add 型的行删掉，mod 型还原 old 值 |

所以 `pkg["paths"]` 排除合并文件，另存 `pkg["merged"]`。
`uninstall` / `restore` 的 `authorize_rels` 和 `assert_writable` 都要把
`pkg["paths"] + _merged_of(pkg)` 一起传进去，否则撤改动时会被写保护挡下。

**只合并配置、不带 mod 文件夹的包**（如 Debug Console），卸载后隔离区是空的。
`restore()` 不能因为它 `q_dir` 不存在就报错 —— 判据是
`if not restored and not merge_changed: raise`。

### 空节清理

`_drop_empty_sections()`：撤销后如果某个节一个键都不剩、且该节的改动全是 `add`
（即整节都是 mod 建的），连节头一起删掉。否则文件里会留一个空的 `[AutoLoot]`，
看着像没卸干净，下次装回来还会变成两个同名节。**节原本就有（有 `mod` 型改动）
的不许删节头。**

### 不做合并的类型

- `.xml`（菜单定义 / `config_description.xml` / `keyboard_layouts_description.xml`）：
  结构嵌套深，按节点合并写错会让游戏设置界面打不开。目前**没有**任何已知包需要它。
- `.dll` / `.exe`：二进制，没有合并语义，只能整份替换 + 保留原件。
- `.json`：目前无案例。真要支持先确认那份文件是不是游戏在读。

### 行尾与编码

- 读：`utf-8-sig` → `utf-8` → `gbk` → `cp1252` → `latin-1`，记住用了哪个，写回时用同一个。
- 行尾跟着**当前文件**走（多数是 CRLF），新增行不混入裸 LF。
- 文件最后一行原本没有换行符的，不额外补。

---

## 15. Script Merger 的协作边界（1.0.6）

### 为什么不自研脚本合并

`.ws` 是 WitcherScript，三方合并要按语法判读（`@wrapMethod` 注解、类继承、
`state` 块），靠文本 diff 合出来的东西编译不过。Script Merger 用的是 REDkit
的脚本解析器，而且作者还在跟进重制版。自研等于重造一个会出错的轮子。

**结论：合并这件事永远交给它。** 管理器只做三件它做不到的事。

### 边界：只读台账 + 只启动进程

`core/merger.py` 全部是只读的：

| 动作 | 做不做 |
| --- | --- |
| 读 `Merger/MergeInventory.xml` | 做 |
| 读 `ScriptMergerRemastered.settings.xml` 的 `MergedModName` | 做（用户可能改过产物名） |
| 扫产物文件夹里的 `.ws`（兜底空壳判断） | 做 |
| 替用户合并脚本 | **不做** |
| 写 `MergeInventory.xml` | **不做** |
| 给 `mod0000_MergedFiles` 补 `mods.settings` 条目 | **不做**（缺条目 = Priority 0 = 最优先，这是它故意的设计） |

唯一会碰到游戏目录的动作是 `POST /api/merger/open`：`subprocess.Popen([exe],
cwd=exe.parent)` 启动它，不传参数、不等待、不捕获输出。改文件是它自己干的。
调用前过 `safety.assert_writable()`，游戏在跑就 409 拒绝。

### 冲突判定：产物是裁判，不是参赛者

原来只按「同一相对路径出现 ≥2 次」判定，合并产物自己也有那个文件，于是被数成
第三方。1.0.5 之前一直如此，用户看到的是「合并完从 2 个冲突变成 3 个」。

`_judge_scripts(rel, keys, inv, prod_names)` 现在给出四种结论：

| 情况 | level | 输出 |
| --- | --- | --- |
| 没有合并产物参与 | `merge` | `script_overlap` |
| 组里所有非产物成员 ∈ 台账来源 | `ok` | `script_merged`（绿） |
| 组里还有非来源成员 | `merge` | `script_overlap`，参与者 = 剩下的 + 产物 |
| 产物有文件但台账没这条 | `merge` | `script_overlap`，文案说明「台账没记录」 |

聚合键从「参与者集合」改成 `(level, 参与者, 已覆盖来源)`，否则同一批 mod 里
已合并的和没合并的会被并成一张卡。

### 另外两个没人盯着的坑

- `merge_stale`：台账 `IncludedMod` 里记着、磁盘 `mods/` 下已不存在 → `warn`。
  合订本里留着引用已消失类的代码，游戏启动报「找不到 XXX」，用户完全看不出
  是卸 mod 卸出来的。
- `merge_empty`：台账有条目，产物文件夹存在但一个 `.ws` 都没有 → `warn`。
  典型场景是 Script Merger 清旧合并后崩了、没重跑。

### 降级

没装 Script Merger（`exe_path()` 为 None 且没有 `MergeInventory.xml`）→
`inventory()` 返回 `found: False`，冲突检测完全走 1.0.5 的旧逻辑，
界面隐藏启动条。这条路径有独立测试（`tests/test_merger.py` 场景 1）。

### 1.0.7 修的坑

`merger` 段在 `conflicts.analyze()` 的返回里是放在 **`stats` 里面**的
（`stats.merger`），而 1.0.6 的前端写的是 `rep.merger`（顶层），取到 undefined
→ 永远显示「没检测到 Script Merger」。改成 `st.merger` 即好。
**教训：后端把一段数据塞进子结构时，前端取值路径要跟着一处改；这类错不会抛异常，
只会静默显示默认值 —— 必须用真实返回做断言，不能只看代码。**

### 已知未覆盖

- 合并产物的**内容**是否真的包含两边改动 —— 需要解析 `.ws`，不做。
  目前只信 `MergeInventory.xml` 的记录。
- 菜单 xml（`input.xml` 等）的节点级合并 —— 见第 14 节「不做合并的类型」。
- `bin/config` 层的同名 ini 争抢进 `conflicts.py` —— 合并写入已支持（第 14 节），
  但冲突面板还不会提示「两个 mod 改同一个键」。

---

## 16. 合并产物不进模组列表（1.0.8）

### 规则

`mod0000_MergedFiles` 这类合订本在磁盘上和普通 mod 长得一样，但它是 Script
Merger 的**工作产物**，不是用户装的 mod。1.0.8 起：

- `ModRecord.is_merged` 由扫描器打标（`core/scanner.py` 3.5 节），
  依据 `merger.inventory()` 的产物名单（含用户改过的 `MergedModName`）。
- **界面看不见**：`ScanResult.visible_mods()` 过滤后序列化
  （`/api/state`、`/api/track`、`/api/localize`、`/api/about`、
  分组自动归类、`unknown_mods` 全部走可见列表）。
- **内部逻辑保留**：`conflicts.analyze()` 拿完整的 `res.mods` ——
  合订本是判定「已合并」的裁判，过滤了它整个判定就失效。

### 顺手修的两个服务端隐患（全栈复盘产物）

1. `_file()` 的静态白名单原来用 `startswith` —— `data/thumbs_evil` 这种
   兄弟目录能蹭过前缀。改成 `Path.relative_to`。
2. `do_POST` 的 `Content-Length` 原来无上限、`int()` 不设防 —— 一个坏头
   就能炸掉处理线程或拖爆内存。现在 >64MB 返回 413，解析失败按 0 处理。

---

## 17. 「最新版」认主文件版本（1.0.9）

### 现象

用户反馈「好几个本地版本号比 Nexus 最新版本还高」。实测 20 个 mod 里 3 个：

| mod | 本地 | 界面显示的「Nexus 最新」 | 真实最新（MAIN 文件） |
| --- | --- | --- | --- |
| Switchable Cloaks（10172） | 1.9.2.1 | 1.9.2 | **1.9.2.1** |
| More Skill Slots（13357） | 1.1.3 | 1.1.1 | **1.1.3** |
| UI Remastered Edition（14578） | 1.1.2 | 1.1 | **1.1.2** |

三次都是同一个方向：**本地的数字是对的，我们的「最新」取错了。**

### 根因

`/mods/{id}.json` 的 `version` 是作者手填的**展示值**，不等于他上传的主文件
`mod_version`。17 个 mod 里 3 个差 1～2 个热修版。拿它比，最新版就成了「本地比线上高」。

反方向更危险：本地 1.9.2、主文件 1.9.2.1、页面写 1.9.2 → 判 `current`，
用户永远看不到那个热修。

### 修法

1. `nexus_meta.mod_files()` 走 GraphQL `modFiles(modId, gameId)`（免 Key，
   和取资料走同一条路），把主文件版本写进缓存记录：`main_version / main_file /
   main_date / main_files`。
2. `_pick_main()` 的挑选顺序：
   - 只看 `category == "MAIN"`。`OPTIONAL / MISCELLANEOUS` 是可选汉化与兼容补丁，
     `OLD_VERSION / ARCHIVED` 是历史版本 —— 混进来会得出「最新是 1.10 的德语翻译包」。
   - MAIN 有多个版本取**版本号最大的**，不取上传时间最新的：作者重传旧文件会刷新时间，
     版本号不变。
   - 没有 MAIN 才退回 `primary == 1`，再退回全部。
3. `nexus.py`（要 Key 的那条 REST 路径）同样改用 `files.json` 里 MAIN 的
   `mod_version`，并且每个 mod 都查 —— 原来只在本地缺版本号时才查。
4. `track.apply()` 优先用 `main_version`，把页面值存进 `page_version` 备查。

### 顺手统一的三份版本比较

`track._ver_tuple` / `nexus.vkey` / `nexus.is_newer` 是同一套规则的三份拷贝，
还略有出入。收进 `core/version.py`：只此一份，两个模块保留同名入口。

规则补了一条：带 `alpha/beta/rc/pre/dev` 后缀的，同号时算更旧（`0.3beta < 0.3`）。
原来 `0.3beta` 和 `0.3` 抠出来完全相等 —— 同一个 mod 的三个下载包
（0.2 / 0.3beta / 0.3）按 mtime 取最新时纯属运气好。

### 新增 `ahead` 档

本地 > 线上时不再混进 `current`（原来 `else` 兜底），单列 `ahead`：
不算「能更新」，也不算「已最新」，排序在新版之后、未知之前，界面上写清楚原因。

### 成本

每个 mod 多一次 GraphQL 请求，`MIN_GAP` 0.8 秒，17 个 mod 约 13 秒。
只发生在需要刷新的那一次（资料 7 天过期），跑在启动后的后台线程里。
老缓存（有资料但没有 `main_version`）走 `refresh()` 的 `fill` 分支只补文件清单，
不重取整条资料。

### 仍未覆盖

- 本地版本的**来源**本身仍是推断的：优先手填 / 安装记录 / 旧管理器记录，
  最后才用下载目录里那个 zip 的文件名。所以 `version_cn` 会写「下载的包」——
  它代表「下载过这个版本」，不等于「装的就是这个」。
- 不读 mod 文件夹内部的自述文件来判断真实版本（巫师 3 没有统一格式）。

---

## 18. 合订本必须登记且排最前（1.0.10）

### 事故

2026-10-27，用户用本程序重排加载顺序，进游戏报 22 条：

```
Error [modautoloot]game\gameplay\containers\container.ws(562):
    Could not find function 'GetAutoLootManager'
```

`GetAutoLootManager()` 是 `W3PlayerWitcher` 的类方法，定义在 `playerWitcher.ws`。
三个 mod 各带一份**完整的**同名文件：

| 文件 | 行数 | 含该方法 |
|---|---|---|
| `modMoreSkillSlots/.../playerwitcher.ws` | 12970 | 否 |
| `modAutoLoot/.../playerWitcher.ws` | 13013 | 是 |
| `mod0000_MergedFiles/.../playerWitcher.ws`（合订本） | 13013 | 是 |

最终生效的是 MoreSkillSlots 那份，AutoLoot 的方法消失，调用点全部编译失败。

**报错里的关键线索**：有 `[modmoreskillslots]...playerwitcher.ws` 的编译警告，
但一条 `[modautoloot]...playerwitcher.ws` 都没有 —— AutoLoot 的那份根本没参与编译。

### 根因

`mod0000_MergedFiles` **从来没有进过 `mods.settings`**。

- `0000` 前缀只在**所有 mod 都没有条目**时有效：那时按文件夹名字母序加载，
  排前面的赢，`0000` 天然第一。
- 一旦任何 mod 有了条目，Priority 接管排序；**没登记的 mod 会被排到最后**。
- 于是合订本输给所有源 mod，合并结果当场作废。

这不是「重排时丢的」—— 合订本从 10-26 由 Script Merger 生成起就没登记过
（10-20 的旧表里有 `[mod0000_MergedFiles] Priority=0`，那是别人模板抄来的）。

### Priority 语义（实测钉死）

**数字小的先加载 = 冲突时赢**。同一次事故给出双向证据：

| 状态 | MoreSkillSlots | AutoLoot | 结果 |
|---|---|---|---|
| 改前 | 70 | 60 | 正常（AutoLoot 赢） |
| 改后 | 40 | 70 | 崩（MoreSkillSlots 赢） |

官方（CDPR REDkit wiki）规定：`Priority` 取值 1–9999，示例就是
`[mod0000_MergedFiles] Enabled = 1 Priority = 1`。社区注释更直白：
*"This mod always goes first, otherwise we'd lose all the changes we introduced
by merging the files in the first place."*

### 四条硬规则

| 编号 | 规则 | 落点 |
|---|---|---|
| R1 | 合订本存在 → 表里必须有条目，`Enabled=1`，`Priority` 严格小于所有其他 mod | `merger.ensure_registered()` |
| R2 | 所有写 `mods.settings` 的路径（重排 / 改优先级 / 启停 / 装 / 卸 / 恢复）写完都过一遍 R1 | `actions.*`、`install.*` |
| R3 | 不满足 R1 时，扫描警告 + 冲突面板红条明说 | `scanner`、`conflicts`、`app.js` |
| R4 | 合订本不进可拖拽排序列表，固定第一 | `order.js` 用 `visible_mods()` |

**边界**：扫描永远只读，只报警不写。补条目只发生在用户主动写文件时。
判定用「是否严格最小」而不是「是否等于 1」—— 表里是 0 还是 1 都合法，不去美化它。

### 顺带修掉的老 bug

`merger.script_files_of()` 用 `relative_to(base.parent)` 拼路径，结果多了一层
`content/scripts/scripts/...`，永远匹配不上台账里的路径。1.0.9 一直靠台账兜底
所以没暴露，但 `detail()` 的「产物里有没有这个文件」全判错。改成 `relative_to(base)`。

### 「已合并」分类

合订本从「全部模组」里消失（1.0.8）之后，用户在界面上再也看不见它 —— 这正是
事故零提示的一半原因。1.0.10 给它一个只读角落：

- 左侧「状态 → 已合并」，计数 1；
- 卡片带「只读」徽标，没有开关、不能拖；
- 详情列出：状态、Priority、合并了哪些脚本、每个来源 mod 还在不在硬盘上、
  「为什么动不得」。

### 移除「旧资料存档」入口

数据（`data/legacy.json`）保留，继续作为 Nexus ID / 作者 / 封面的来源；
删掉的是设置页那张卡片和 `/api/legacy/import`、`/api/legacy/forget` 两个写接口。
理由：已经搬完的东西没有理由「再导入一次」，而重复导入会把用户后来手工追认的 ID
覆盖回旧值。想重新搬就删 `legacy.json`。
同时撤掉扫描时那句「已把 N 条旧资料存进本程序目录」—— 入口没了，这句话只剩困惑。

### 加载顺序的启用/禁用配色

两个标签原来都是灰底灰字，扫一眼分不出哪些真在生效。改成绿（`--ok`）/ 红
（`--danger`）+ 对应的 soft 底色，只给这两个状态用。

### 仍未覆盖

- 台账里的 `IncludedMod Hash` 是合并时源文件的指纹，可以判断「源 mod 后来变过、
  合订本已过期」。算法没反推出来（CRC32 / CRC32C / Adler32 / MD5 前 8 后 8 都不对），
  下一版改用自己的指纹表，不依赖它。

---

## 19. 十一处防错 + 对齐 Win11（1.1.0）

### 来由

1.0.10 交付后，用户拿别处的竞品调研（Vortex / MO2 / Script Merger / RMMR）来问
「哪些该做」。评估时核对了报告里的事实，有三处对不上，先按实情调整了范围：

| 报告的说法 | 实际情况 | 后果 |
|---|---|---|
| §10「P-1 合订本未登记」是头号待修 | 1.0.10 已修完，且比报告要求更严（不只要有条目，必须**严格最靠前**） | 该项出队 |
| 按 187 个 mod 设计（多选、局部重渲染、密度三档） | 本机真实 19 个可见模组 | 多选降级到 1.1.1，密度三档不做 |
| 侧栏 218px → 236px | 窗口固定 1280×820，加宽后详情列只剩 2px 余量 | 不加宽 |

### 这一版做了什么

**1. 游戏运行中全局只读（C4）**
之前只禁用了「撤销」按钮。现在 `game_running` 为真时：顶部一条常驻橙色提示条讲清
「为什么现在别改」，所有写盘入口（开关 / 保存顺序 / 安装 / 卸载 / 恢复 / 清理 /
路径设置 / 撤销）在捕获阶段被拦下并给出同一句解释。**拦截放在 click 捕获阶段而不是
逐个 `disabled`**：按钮看起来还能点，点了会有话说，比一片灰按钮更容易理解。
底部状态栏那句也改成同一口径 —— 验收时发现上面写「别改」、下面还写「可以安全部署」，
自相矛盾。

**2. 卸载后配置冲突改成阻塞对话框（C1）**
卸载时如果发现「装这个包时改过的配置又被别人改过」，以前只飘 7 秒 toast。这类后果
是「游戏里按键还绑在上一个 mod 上」，用户会真的丢东西却不知道，现在改成必须先点一下
才走的对话框：列出每一处（文件 / `[段] 键` / 期望值 vs 现值 / 为什么没还原），
出口三个 —— 撤销这次卸载（走现成 journal）、打开配置目录、就这样我知道了。
详情面板底部也加了「卸载这个模组…」入口，点了直接跳到「已安装的包」并高亮那个包
（一次带多个 mod 的包会额外提示「会一起移走」）。

**3. 加载顺序：不让改动白丢 + 把语义写对**
拖动改完直接关窗，改动会静默消失（1.1.0 之前就是这样）。现在关闭时会拦一下，
底部常驻「有改动还没保存 —— 直接关窗会丢」。
同时纠正了一句写反的说明：原来写「先加载的会被后面的盖掉」，而实测规则是
**数字小的先加载、冲突时先加载的赢**（§18 的双向证据）。写反的代价用户已经付过一次
（22 条 `Could not find function`），这种句子不能靠印象。

**4. 合并台账自动侦测**
Script Merger 是独立进程，它合并完本程序并不知道。冲突面板打开时每 3 秒只读探一次
`GET /api/merger/state`（台账 mtime + 条目数 + 合订本登记状态），变了就静默重画并
提示「待合并 3 → 0」。**只在面板开着时轮询**，关掉就停；判定用的是
`mtime|count|登记状态|优先级|文件数` 五项摘要，不比较文件内容，开销可以忽略。

**5. 反馈闭环**
- 每次开关/排序/保存优先级之后，提示条上直接带「撤销」（走现成的 `/api/undo`），
  不用开日志再找一条。
- 状态栏常驻最近一条操作（`✓ 禁用 modAlpha 20:10`），点开就是日志面板。
- 选中一个模组时，和它冲突的几个在列表里一起高亮（左侧黄条），不用记名字再搜一遍。
- 建分组、粘路径这类输入从 `prompt()` 换成站内输入框（C2）。**这不动 §11 那条
  「必须原生对话框」的结论** —— 那条针对的是选文件/选目录（要拿系统路径、要能拖拽），
  文本输入用原生 `prompt` 只会被浏览器压住、样式也不受控。
- 空状态从一句话变成「说清为什么空 + 下一步」（C3）。

**6. 视觉：跟随系统 + Win11 刻度（A1 / A3）**
- 主题三态：跟随系统 / 亮色 / 暗色，默认跟随系统并监听系统切换。首屏在 `<head>` 里
  先定主题再加载样式，避免亮色闪一下。
- 圆角拆成两档：卡片与面板 8px，控件 4px（原来 7/12/14 混用，面板和按钮一起变软）。
- 控件高统一 32px（`select` / 分段 / 按钮 / 侧栏项）。
- 字号只抬「读的字」：卡片副标题、路径、状态栏、各面板说明抬到 11.5px 左右。
  **没有**按报告一律抬到 12px —— 卡片最小宽度 162px，一律 12px 会让副标题被省略号
  吃掉（`Standardised Naming Con...` 肉眼可见）。角标、药丸这类「扫一眼就够」的维持原样。

### 唯一新增的服务端接口

`GET /api/merger/state`（只读）。`core/guard.py` 一行没改，1.1.0 没有新增任何写路径。
`/api/open-folder` 顺带补了 `@userconfig` 分支（只 `startfile` 目录，不接受任意后缀）。

### 与调研文档的偏差（明确的取舍）

| 报告要求 | 本版 | 理由 |
|---|---|---|
| B 多选 + 批量开关（P0 头条） | 推到 1.1.1 | 19 个 mod 用不上批量；真正需要的是防错 |
| D 密度三档 | 不做 | 三档合计省下的是滚动距离，19 个 mod 一屏就够，多一套模式只多一处会坏的地方 |
| A2 系统强调色 | 不做 | 主色是整套视觉的锚，跟随系统会同时影响选中态/链接/进度色，收益小于一次全站换色的风险 |
| C3 第三个出口 = 导入压缩包 | 改成「去 Nexus 搜这个名字」 | 搜不到东西的原因通常是没装，而不是装错；导入入口顶栏一直有 |

### 验收记录（2026-10-27）

- 16 套测试全绿（`test_install` / `test_config_install` 已覆盖卸载冲突的后端契约）。
- 界面截图逐项过：主界面、暗色、加载顺序、冲突面板、已合并、空状态、只读态、
  已安装的包（含聚焦）、详情面板。
- 只读态与开关/撤销用**沙盒**跑通：临时游戏目录 + 临时配置目录 + 临时数据目录，
  点开关写出 `mods.settings`、提示条出现「撤销」、点撤销后文件与 journal 复原。
  真实游戏目录全程只读（`tools/doctor.py check` 指纹未变）。

### 仍未覆盖

- 只读态下「点了会被拦」这条是代码里统一的捕获阶段拦截，界面截图只能证明按钮外观，
  手感的最后一厘米要真机确认。
- 合订本内容是否过期仍判断不了（§18 末尾的同一件事）。
- 卸载冲突对话框的真实数据来自「装过、又被别人改过配置」的包，沙盒没造出这一场景，
  后端契约由 `test_config_install` 守着，界面首次真实触发时留意一下。

## 20. 三层冲突检测 + 批量开关（1.1.1）

### 来由：一次真实事故暴露检测盲区

2026-10-27 用户报「Hood Slots 的额外槽位不显示、More Skill Slots 的图例提示不对」。
排查（另一条对话）最终定位到病因：**三个模组的 `.bundle` 里塞着同一个界面资源**
`gameplay\gui_new\swf\inventory\panel_inventory.redswf`，游戏只加载其中一份，
另一份的界面改动整片消失 —— 而脚本功能照常在跑。

这类问题 1.1.0 的 Waass 一个字都说不出来，因为它的冲突检测只有一层：
**同名脚本文件**。而真实世界有三层：

| 层 | 撞的是什么 | 后果 | 可见性 |
|---|---|---|---|
| 1 | 同一个 `.ws` 文件路径 | 后者覆盖前者，改动整份消失 | 文件名可见，好查 |
| 2 | 同一个函数被两个 mod 加注解 | `@wrapMethod` 可共存但顺序敏感；`@replaceMethod` 静默覆盖 | **完全不可见** |
| 3 | 不同 `.bundle` 里的同一个资源路径 | 只有一份生效，其他消失 | **完全不可见** |

第 2、3 层的共同点是「脚本还在跑、游戏不报错」，用户只能看到「功能在，界面不对」，
没有任何线索。1.1.1 把这两层补上。

### 三个只读探针

新增三个模块，全部只读、全部在 `core/guard.py` 之外（不涉及任何写路径）：

**`core/bundle_entries.py` — 只读 POTATO70 名称表**

资源包格式叫 POTATO70，公开资料只提到「压缩字 0=原样 / 1=zlib」，
所以按公开资料写的解析器读到 REDkit 打包的包会全部失败 ——
**压缩字 5 = LZ4 块**（实测，见 `witcher3/mods/panel-and-bundle-notes.md`）。

关键取舍：**只读名称表，不解压任何内容**。名称表在文件头 `0x20` 起，每条固定 304 字节
（256 名称 + 16 哈希 + 32 元数据），条目数在 `0x08`。
判断「两个包是否提供同一资源」只需要路径字符串，因此不需要实现 LZ4 解压
（工作区里有 `lz4blk.py` 能解，但没必要搬进来）。

```python
MAGIC = b"POTATO70"   # 0x00
REC = 304             # 每条记录固定长度
NAME_SLOT = 272       # 名称槽（其余是元数据）
HEADER = 0x20         # 名称表起点
MAX_TABLE = 64 << 20  # 防御：名称表不可能这么大
MAX_ENTRIES = 60000
```

`classify()` 把资源分三类，只有前两类上报：

| 类别 | 判定 | 上报 |
|---|---|---|
| `ui` | `.redswf/.swf/.gfx/.redflash` | 是（warn） |
| `data` | `.xml/.reddlc/.w2quest/.w2phase`，或路径含 `\definitions\` | 是（info） |
| `asset` | 其余（贴图/模型/声音） | **否** |

`asset` 一律不报是刻意的：材质类 mod 之间撞贴图是常态，报出来就是一片噪音，
用户会学会无视整个面板。缓存以 `(path, mtime, size)` 为键、上限 400 条，
整库实测 0.01 秒。

**`core/scripts_meta.py` — 注解目标 + 编码**

```python
_ANNO = re.compile(r"@(wrapMethod|replaceMethod)\s*\(\s*([A-Za-z_][\w.]*)\s*\)")
```

只收 `@wrapMethod` / `@replaceMethod`：`@addMethod` / `@addField` 是纯新增，
天然可共存，收进来只会制造假冲突。

编码检测是这一版的另一个实用点。5.00 把脚本资源从 UTF-16 改成 UTF-8，
4.x 时代的老包原样拷进来会编译失败，游戏只报文件名 —— 用户完全看不出是编码问题。
BOM 好认，**无 BOM 的 UTF-16 只能靠 NUL 比例**：

```python
probe = head[:64]
if len(probe) >= 8 and probe.count(0) * 10 >= len(probe) * 4:
    return "utf-16-le" if probe[1:2] == b"\x00" else "utf-16-be"
```

（第一版写成 `count(0) >= max(8, len//4)`，12 字节的 probe 永远到不了 8 个 NUL，
短脚本全部漏检。`test_mod_inspect` 抓到了。）

超过 4MB 的文件只查编码、不抽注解 —— 大文件基本是合并产物，抽注解没有意义还慢。

**`core/w3strings.py` 增加版本读取**

头是 `RTSW` + u32 版本。重制版 5.00 = **164**。表比游戏旧时，新增文字查不到，
界面显示 `##` 或空白。`game_baseline()` 优先读 `content/content0/cn.w3strings`，
没有就退到 `en`。

### 检测口径

**只查已启用的模组。** 这条不是优化，是正确性：`~modSmoothUIRemastered`（已停用）
和 UI Remastered 在 20 个面板文件上撞车，报出来毫无意义 —— 它压根不加载。
原先五个循环全部从 `mods` 改成 `active`。

**`@wrapMethod` 报 info 不报 warn。** 它是官方支持的叠加机制，本身不是错。
但叠加顺序由加载顺序决定，所以它是排查「动过顺序之后某个功能变了」的唯一线索。
`@replaceMethod` 是 warn：整段替换会连别人的包装一起丢掉，且不报错。

**语气上不指控任何一方。** 界面文案写的是「先别急着卸载：有时上游模组已经把对方的
改动整合进去了，要看实际效果再决定」—— HoodSlots 那件事就是反例：
UI Remastered 的面板里其实**已经带了** HoodSlots 的槽位 UI，
真正的问题是它默认把槽位隐藏了。照着「撞车 = 该卸一个」办事会卸错人。

### 新增检测项一览

| code | 级别 | 说明 |
|---|---|---|
| `anno_replace` | warn | 同一个函数被 ≥2 个 mod 用 `@replaceMethod` 整段替换 |
| `anno_wrap` | info | 同一个函数被 ≥2 个 mod `@wrapMethod` 叠加，顺序敏感 |
| `ws_encoding` | warn | 脚本是 UTF-16 / 其它编码，5.00 编译不过 |
| `bundle_ui` | warn | 包内同一界面资源被多个 mod 提供 |
| `bundle_data` | info | 包内同一定义资源被多个 mod 提供 |
| `w3strings_stale` | warn | 中文语言表版本低于游戏本体 |

`stats` 相应扩展：`bundle_mods / bundle_ui / bundle_data / anno_targets /
anno_wrap / anno_replace / ws_bad / w3_stale / w3_base / disabled`。

真实库（19 个可见模组）跑出来：`1 script_merged(ok)` / `2 anno_wrap(info)` /
`2 bundle_ui(warn)` / `1 w3strings_stale(warn)`，0.11 秒。
`panel_inventory.redswf` 被 3 个 mod 同时提供、HoodSlots 语言表 v162 对本体 v164 ——
用户报的那次事故，程序现在不用人教就能说出来。

### 多选与批量开关

19 个 mod 用不上批量，但「一次关掉 5 个排查冲突」是真实的调试动作，所以做了。
关键设计只有一条：**整个批量是一次 `Operation`**。

如果每个 key 各开一个 Operation，日志里会多出 5 条、撤销要点 5 次 —— 那等于没有撤销。
`set_enabled_many()` 因此：

1. 去重；
2. **校验全部前置** —— 任何一个找不到、或 `~` 改名的目标已存在，整体不动。
   宁可一条都不改，也不要改到一半停下（半成品状态最难解释）；
3. 一次 `op.backup(settings_path)`；
4. 循环改名 + 改 `Enabled`；
5. 过一遍 `_ensure_merged_entry()`（§18 的 R2：所有写 `mods.settings` 的路径都要过）；
6. 一次 `op.commit()`。

`POST /api/mods/batch_toggle` 与单个开关共用错误映射（`GameRunning`→409）。

界面上用「批量选择」按钮进入 picking 模式：卡片上出现勾选框、开关暂时隐藏
（这时点卡片是选中而不是开关，两种语义叠在同一个位置必然误触）、顶部出现操作条。

### 详情面板重排

调研报告提的是「信息密度」，实际问题是**顺序**：冲突卡片排在前面，把「禁用 / 打开目录」
挤到了屏幕外。改法：

- 顶部只留 5 行身份信息（状态 / 本地版本 / Nexus 版本 / 作者 / Nexus ID）；
- 紧跟一排快捷动作（禁用-启用 / 打开目录 / 模组页 / 下载页）；
- 冲突摘要压成标题 + 最多 3 条，`info` 级不显示，底部给「打开冲突检查（另有 N 条说明）」；
- 「加载顺序」上移，文件数 / 体积 / 安装时间 / 下载量收进 `<details>技术细节</details>`；
- 卸载单独放到底部危险区，带一句「卸载以包为单位」。

### 顺带修掉的既有 bug

`core/safety.py::undo_last()` 在**冷启动**时会失败：写入白名单是进程内的，
用户装完东西、关掉 Waass、重开、点撤销 → `有 1 个路径没有写入授权`。
修法是从 journal 记录本身反推授权（`_authorize_for_undo`），不依赖进程状态。
同时修掉第二个问题：撤销失败会把 journal 条目一并丢掉，等于连「改过什么」都忘了 ——
现在失败时条目原样保留。

### 验收记录（2026-10-28）

- **17 套测试全绿**。`test_conflicts` 从 7 项扩到 26 项，新增 [7]~[11] 覆盖
  `bundle_ui` / `anno_wrap` / `ws_encoding` / `w3strings_stale`，以及
  「停用的模组不出现在任何一项里」。
- `test_actions` 新增 [7b]~[7d]：批量禁用只产生 1 条 journal、一次撤销全复原、
  前置校验失败时同批合法的那个也没被改、重复 key 去重、`~` 改名计数。
- `test_mod_inspect.py` 32 项覆盖三个探针的边界（坏 magic / 荒谬的名称表长度 /
  截断文件 / 缓存失效 / 注释里的假注解 / 无 BOM 的 UTF-16）。
- 真实游戏目录全程只读，`tools/doctor.py check` 指纹未变。

### 仍未覆盖

- `bundle_data` 只报「被多方提供」，不判断哪一份更新。物品 XML 里可能有
  只存在于其中一份的新条目，这类「合并后的并集」需要真解析 CR2W，超出只读探针的范围。
- `@wrapMethod` 的叠加顺序只说「与加载顺序有关」，不预测具体结果 ——
  不同 mod 包的是同一个函数的不同阶段，顺序语义要看各自实现。
- 资产类（贴图/模型/声音）撞车仍然完全不报。若将来要做，得先解决噪音问题。

## 21. 一键汉化（1.2.0）

### 旧版为什么难用

旧流程是五步：按模组点「提取」→ 导出 JSON → 交给 AI → 复制回来 → 粘进框里点应用。
十五个模组就是十五轮。但真实库里**待翻的只有 37 条**，十分钟的活儿。

所以问题不在量，在于中间三步是程序不会翻译硬塞给用户的。1.2.0 把这三步收进程序。

### 三步走

**① 选**：模组勾选列表 + 内容类别（菜单页 / 模组名 / 物品名）。
默认只勾「菜单页」—— 用户 2026-10-28 的要求：默认不动模组名，否则容易出 bug。
不预勾任何模组（范围由用户决定，程序不替他选），只给一个「全选可翻的」按钮。

**② 翻**：后台线程跑，每 600 毫秒轮询进度，可随时停。

**③ 改**：结果表里直接改译文，改完立刻重算「能不能放下」，右下角一次写入。

### 三条通道，一条能改

| 通道 | 形态 | 数量（本机） | 1.2.0 处理 |
|---|---|---|---|
| A 语言表 | `cn/zh.w3strings` 里的明文条目 | 15 个模组 | **改写**（等长替换） |
| B 脚本内嵌 | `.ws` 里 `if (lang == "ZH") return "…"` | AutoLoot 941 字、UIRE 传送 343 字等 | **只读识别**，不改 |
| C 菜单 XML | `bin/config/.../pc/*.xml` 的字面文本 | 0 条 | 沿用旧逻辑 |

B 通道是 2026-10-28 补上的，来由是一次被用户当场纠正：
AutoLoot 游戏里全中文，而体检报「没有中文语言表」。查证发现它的中文写在
`modAutoLootLoc.ws` 里按语言分支返回，压根不经过语言表。只认 A 通道会误报。

同一次自查还修了两个措辞错误：
- 「语言表加密」说的是**我们读不出原文**，不等于该模组缺中文，不能混成一句话；
- `Swords On Hip` 那几条英文全在**代码注释**里，不是界面文字，根本不该进待翻列表。

### 分类：默认只碰菜单页

```python
CAT_MENU / CAT_MOD / CAT_ITEM   # 菜单页 / 模组名 / 物品名
CAT_DEFAULT = (CAT_MENU,)
```

判定只用两条硬信号，其余一律归到菜单页 —— 宁可多翻一条菜单，
也不要把物品名误当菜单翻（后者会真出问题）：

1. 语言表第 0 条、或文本与模组显示名/文件夹名一致 → 模组名；
2. 所有格（`Deadly Ronin's Kimono`）且无菜单特征（`<font`、Auto/Enable/Show…）→ 物品名。

本机结果：菜单页 37 条（hoods Switchable Cloak 36 + Hood Slots 1），
模组名 4 条、物品名 3 条全部排除。

### 翻译引擎链

| 级别 | 引擎 | 配置 | 说明 |
|---|---|---|---|
| L0 | 翻译记忆 + 术语表 | 不要 | 命中即出，重复串与术语从此一致 |
| L1 | Google gtx / MyMemory | 不要 | 免 Key，实测两条都通 |
| L2 | 百度 / 其他需 Key 的 | 要填 Key | 一行一个，轮换使用 |

`core/translate.py` 只用标准库 `urllib`，不引第三方依赖。
引擎连续失败 3 次就本轮跳过，不会让一批翻译卡在一个抽风的接口上。

**多个 Key 轮换**是用户明确要求的（「多配置几个 key 防止某些接口出问题时汉化失败」）：
`eng.used` 记每个 Key 用了多少次，每次取最少用的那个。

### 译文质量：三件工程活

机翻质量本身不能改，但三类问题是工程能修的：

**① 标签对必须当槽位。** 中文语序会把标签挪位置，
`Auto unequip <font>IN INTERIORS</font>` 直译成 `在室内 [1] 中自动卸下 [0]` ——
闭标签跑到开标签前面，游戏里就是错位的颜色块。改成：
骨架 `Auto unequip {0}` 先翻 → 校验槽位顺序 → 标签里的文字单独翻 → 按位置拼回。

**② 短词靠术语表，不靠引擎。** `IN INTERIORS` 单独翻成「在室内装饰」、
`IN ANY STATE` 翻成「在任何州」（state 当成美国的州）。这类词脱离语境必错，
且**错译往往最长**，长度问题和质量问题在这里是同一个问题。

**③ 同一句英文必须落成同一个中文。** 逐条独立翻的必然结果：

```
Auto unequip → 自动卸下 / 自动解除装备 / 自动取消装备
Equip back   → 装备回来 / 恢复装备 / 返回装备
during X weather → 在X天气期间 / 在X时 / X时
```

单看每条都对，摆在一个菜单里就是明摆着的机翻。`_FIX_WORDS` 把高发的收敛成一套说法。

> 归一规则必须**先做空格归一**再匹配。引擎常吐 `在 <font>理发</font> 次访问期间`，
> 空格夹在中间会让「紧邻 次」这类判定全部失效；而且同一个源句两次翻译句式会变，
> 判定不能依赖某一个具体句式。

### 等长写入与「放不下」

写回仍是等长替换：只改文本块字节，偏移/长度/索引不动，文件大小一模一样。
代价是译文不能比原文长，一个汉字 3 字节。

`shorten()` 四级递进：清空格（无损）→ 套缩句规则 → 删括号补充说明 → 仍放不下就如实标注。
**绝不截断** —— 截断就是一屏乱码。

真实库 37 条里 35 条一次写成，2 条放不下：
`Other` → 其他、`Hoods` → 兜帽，都是 5 字节对 6 字节，差整整 1 个字节。
结果表如实标「还差 1 字节（约 1 个汉字），请在表里改短」，用户自己决定
保持英文还是改单字缩写。

> **变长写入为什么没做**：技术上可行（原型已验证：114 条改长后逐字回读一致），
> 真实理由不是做不到，而是界面的行宽是按原文字节长度设计的，中文变长可能被截断。
> 等长不只是保守，它在「别把界面撑坏」上本来就更对。

### 备份与还原

用户要求「所有汉化的模组必须做好备份和还原，出 bug 能一键还原」。
`safety.Operation` 的快照不够：它只留最近 30 份且会被自动清理，
三个月前那个还原点等于没有。所以另立一套（`core/localize_backup.py`）：

```
data/localize/orig/<相对路径>        原始副本，第一次见到就存，永不覆盖
data/localize/history/<批次>/<相对路径>  每次汉化前一份，留最近 20 批
data/localize/index.json            历次记录，含前后 sha256
```

两套都要的理由：`orig` 回答「它本来长什么样」（一键还原用这个），
`history` 回答「这一批之前长什么样」（连续汉化两次时只退第二次，不抹掉第一次的成果）。

还原前比对当前文件 sha256 与记录里的 `sha_after`：不一致说明模组更新过，
先提示再问要不要硬覆盖 —— 不默默覆盖，那会把新内容换成旧备份。
还原也走 `safety.Operation`，所以「还原」这个动作本身还能被撤销。

**`data/localize/` 已进 `KEEP_DIRS`，清理面板永远不列它、不删它。**
之前是个漏洞：新的原件目录没登记，而清理清单里躺着的是 1.1 旧目录
`localize_backups`，看着像"备份能清"其实清的是一堆没用的旧中间文件，
真原件反倒成了"其他文件"（只统计、不可点，但归类是错的）。
现在两件事都纠正了：`localize` 与 `saves`/`baseline` 同级保护，
旧的 `localize_backups` 项改名为「汉化中间文件（旧版）」并写清「清它不影响还原」。
边界由 `tests/test_storage_safety.py`（19 项）钉住 —— 包括
「点名清理所有清单 id 之后原件字节不变」这条。

### 接口

| 方法 | 路径 | 作用 |
|---|---|---|
| GET | `/api/localize/panel` | 模组清单 + 类别 + 引擎状态 + 历次记录 |
| POST | `/api/localize/run` | `{mods, cats}` → 任务，后台开翻 |
| GET | `/api/localize/job?id=` | 进度 / 结果 |
| POST | `/api/localize/job/cancel` | 停 |
| POST | `/api/localize/write` | `{id, edits, only}` 一次写入 |
| POST | `/api/localize/restore` | `{record, mode, force}` |
| GET | `/api/localize/history` | 记录列表 |
| POST | `/api/localize/config` | 引擎开关与 Key |
| POST | `/api/localize/probe` | 真发一次请求测连通性 |

错误映射沿用全局规则：`GameRunning`→409、`WriteDenied`→403。
`probe` 单独成一个接口，是因为它会真的等网络 —— 打开面板不该先卡三秒。

### 验收记录（2026-10-28）

- **20 套测试全绿**，新增四套：
  - `test_translate.py` 46 项：标签保护、结构校验、槽位拆分、措辞归一、
    空格归一、缩句、字节预算扣标签、术语/记忆、分类、引擎注册表。全部离线。
  - `test_localize_backup.py` 34 项：原件不覆盖、批次号撞号、漂移检测、
    `force` 语义、还原可撤销、history 只留 20 批、清空必须确认。
  - `test_localize_job.py` 23 项：写入等长、原文不被留残句、`only` 单条重写、
    手改长度重判、一键还原、边界报错。
  - `test_storage_safety.py` 19 项：`localize` 进 KEEP_DIRS、清单无该项、
    全量清理与点名清理后原件字节不变、history/index 都还在、
    同时验证清理本身没空转（thumbs/隔离区确实被删）。
- 真实库端到端（只读游戏目录）：37 条 → 35 写成 / 2 放不下 / 0 失败，约 10 秒。
- 未在真实游戏目录上执行过写入 —— 按红线，写操作只在沙盒与用户授权下进行。

### 仍未覆盖

- B 通道只识别不改写。改 `.ws` 字面量没有长度限制，但要保证语法与编码不变、
  且能被一次撤销，风险量级和改语言表不同，留到 1.2.1 评估。
- 变长写入（档 B）押后。
- 机翻质量是「能读懂」级别，术语靠词表校正，不是官方译名水准。
  想要更好只能配 L2 的 Key。
- 贴图 / 模型上的字完全不覆盖。

---

## 22. 1.2.1：判定修正、入口收敛、图标与启动同步

这一版没有新功能，七件事全是「上一版哪里让人不顺手」。按改动性质分三类：
**判定错**（1）、**多余的东西**（2、8）、**该有却没有**（3、4、5、6、7）。

### 22.1 汉化判定：两条规则漏了

用户实测反馈：「显示可翻译的两个模组我之前手动对他们做个非常规的汉化了，你没有检测出来。」
查证后确认是两种完全不同的漏判。

**（a）手改菜单 XML 的汉化，程序没认出来。**

用户的汉化方式是直接改 `bin/config/.../pc/SwitchableCloak.xml`：

```xml
<Var id="ModEnabled" displayName="启用模组" tags="customNames;nonLocalizedName"/>
```

45 个 `displayName` 里 32 个写成中文、13 个仍保留作者 key（`Mods.priler_*` / `njs_*`）。
界面文字于是来自 XML 字面，语言表里那 36 条英文**已经被取代、不会再显示**。
1.2.0 只数语言表，就把这个模组报成「还有 36 条英文没翻」—— 数字没错，结论错了。

新增 `localize.xml_takeover()`，两个条件同时满足才判「已被菜单接管」：

1. XML 里至少 1 条中文字面 `displayName` —— 证明确实有人改过；
2. 语言表里**已翻**的条目数 >= XML 里仍以 key 形式引用的**不同 key 数**。

用「不同 key 数」而不是「引用次数」：`njs_all` 同一个 key 会被好几个 Preset 用到，
按次数算会虚高，把真该翻的模组判成完成。实测这个模组 13 次引用只有 9 个不同 key，
已翻 10 条 → 判定成立。

被接管的英文不再进任务表，但**信息不丢**：`build_jobs()` 额外返回 `stale`，
体检面板标「已手工汉化（改的是菜单 XML）」并把条数显示为「过期 N」（删除线灰字）。
`extract_xml()` 也因此多返回 `cjk` 与 `key_names`，并把以前直接跳过的 `Mods.*`
前缀 key 也算进「仍在靠语言表的行」—— 少算会让判定偏松。

**（b）单个分类词被当成菜单项。**

Hood Slots 只剩一个 `Hoods`，是护甲分类名。5 个字节放不下「兜帽」（6 字节），
列成待翻只会让人盯着一个永远写不进去的条目发愁。

`classify()` 加一条：单个词 + 首字母大写 + 不在 `_MENU_WORDS` 里 →
`CAT_ITEM`。`_MENU_WORDS` 收了 Enable / Disable / Settings / Options / Comments /
Other / Debug 这些「看着像名词其实是菜单」的词 —— 少一个词只是少翻一句，
多一个词会把分类名当待翻，两害相权取轻。

结果：真实库的待翻菜单文字从 37 条降到 **0 条**。这个结论是对的：
用户库里确实没有需要补的菜单页，剩下的 9 条全是模组名和物品名（默认不翻）。

### 22.2 去掉「中文名」，去掉「已安装的包」入口

「中文名」整条功能移除：左侧入口、`web/static/names.js`、`/api/names` 两个接口、
`@names` 打开目录，全部删掉。**`data/names.json` 没删**，但 1.2.1 起不再把它
贴到 `mod.name_cn` 上 —— 卡片和详情一律显示模组自己的名字，和 Nexus、游戏里一致。
想恢复只需把 `server.py` 里那行 `names.apply_to(...)` 的注释去掉。
（理由：保留显示却没有编辑入口，等于留下一份改不动的隐藏状态，比两种极端都糟。）

「已安装的包」只移除入口。它和「详情面板 → 卸载这个模组」是连着的
（`/api/packages` 仍然给卸载流程提供包档案），所以后端和对话框都留着，
`?dialog=packages` 调试入口也还在。

### 22.3 顶栏「Nexus」按钮

点击 → 服务端 `os.startfile()` 用系统默认浏览器打开
`https://www.nexusmods.com/games/witcher3`。**不能在页面里 `target=_blank`** ——
Edge 的 `--app` 窗口会把外链接住，开成一个同样没有地址栏的新窗口，
用户就再也回不到管理器了。`/api/open-url` 原本就只放行 nexusmods.com，直接复用。

### 22.4 任务栏图标发虚：小尺寸的 W 是糊的

不是缩略图问题，是**任务栏按钮上的图标**。查 `icon-16.png` 的像素发现：
W 只占画面中间 6 个像素高、四周全是紫底，加上四档灰阶抗锯齿 → 一小团糊。

三处改：
1. `W_PTS` 从 x 62..194 / y 84..176 放大到 x 50..206 / y 66..190（52%×36% → 61%×48%）；
2. `STROKE_HALF` 小尺寸加粗（16px: 1.05 → 1.10，32px: 1.50 → 1.95）；
3. **≤24px 干脆不做羽化**：W 的笔画覆盖率按 0.46 二分，要么实心白要么纯紫底。
   16px 上一条斜线只有 2 个像素宽，四档灰阶叠上去必然糊；硬边宁可有锯齿，
   也不能没有轮廓。外框（圆角方块）仍然抗锯齿。

favicon 的 `?v=4` 提到 `?v=5`，否则 Edge 会继续用缓存里的旧图。

### 22.5 启动自动同步 Nexus

`POST /api/nexus/autosync`：补缺失封面 + 查有没有新版本，全程静默。
`nexus_meta.refresh()` 新增 `days` 参数，启动同步用 `days=1` ——
每天第一次开程序真的查一遍，同一天再开几次直接命中缓存，不把 Nexus 当靶子打。
（服务端是 `ThreadingHTTPServer`，同步跑 10 秒也不挡界面。）

结果通过 `core/ui.py` 的 `dismissed_upd` 记账：顶部出蓝色提示条「N 个模组有新版本」，
带「看看有哪些」（跳「检查 MOD 更新」的可更新筛选）和「忽略」。
指纹只由可更新名单算，**不含时间** —— 1.0.3 踩过含 mtime 导致提示自己复亮的坑。
没配 ID、断网、Nexus 抽风都静默跳过，不留任何界面痕迹。

### 22.6 主题三态图标

三种状态（跟随系统 / 日间 / 夜间）以前只有两个图标：`auto` 直接复用解析后的日或月。
新增 `.ic-auto`（左半实心太阳 + 右半新月），显示条件挂在
`data-theme-mode` 而不是 `data-theme` 上 —— 后者在「跟随系统 + 系统是暗色」
和「手动选暗色」时完全一样，分不出来。首屏内联脚本也同步写这个属性，避免闪一帧错图标。

### 22.7 其它两处

- 「批量选择」原来用 `.btn-sm`（28px / 11.5px），夹在 32px 的排序下拉和视图切换中间，
  明显矮一截。改用 `.btn` 并给 `.content-tools .btn` 统一 32px / 12px。
- 左侧「Nexus 追踪」→「检查 MOD 更新」，对话框标题同步改。
  打开它的人十次有九次是想看有没有新版本，不是为了维护台账。

### 22.8 验收记录（2026-10-28）

- **22 套测试全绿**，新增 `test_localize_detect.py` 28 项：
  接管判定（够 key / 不够 key 两向）、部分汉化不误判、无 XML 不受影响、
  12 个分类词用例、第 0 条仍是模组名。
- 真实库：待翻菜单 37 → 0，`hoods Switchable Cloak` 标「已手工汉化 · 过期 36」，
  `Hood Slots` 的 `Hoods` 归到物品名。
- 自动同步实测：17 个带 ID 的模组，14.5 秒（冷缓存），6 个可更新，指纹稳定。
- 明暗两套主题截图核对：提示条文字可读、按钮不换行、工具栏三件控件等高。

---

## 23. 1.2.2：三处「看着不顺眼」

这一版没有新功能，三件事都是观感/手感问题：图标太丑、提示关不掉、
已禁用的模组夹在中间碍眼。前两条是界面问题，第三条牵出一个真实的风险 ——
**「为了看着顺眼去改加载顺序」本身就不该成为用户的事**。

### 23.1 主题三态图标：跟随系统改成「半明半暗的圆」

1.2.1 的 `auto` 图标是「左半太阳 + 右半新月」拼起来的，实测在 16px 下像两块
不相干的图形挤在一起，用户反馈「太丑」。

改成从日间图标派生：**同一个圆，左半实心（黑）、右半只描边（亮），光线只在右侧**。
左半边实心就是「黑夜那半」，右半边留白就是「白天那半」，逻辑比拼两个图标自洽。

**这里踩了个 16px 的坑，记一下。** 最初照「左实心 + 右描边 + 三根光线」画，
截图放大后发现三根光线在 16px 下退化成三个孤立的点 —— 因为一条 1.2 长、
1.5 粗、圆头端点的线段，在 16px 画布里就是 2×2 个像素。日间图标能用同样
长度的光线，靠的是 8 根围成一圈形成图案；只剩 3 根时就没有图案可言了。

试了三轮（光线长度/半径/根数、内外双半径、左弧加粗），结论是：
**这个尺寸下「半实心圆 + 光线」不可兼得**。最终去掉光线，只留半实心圆 ——
它本身就是通行的「明暗对比」符号，读起来是「一半亮一半暗」，正是「跟随系统」。
圆的半径从 3.1 提到 4.2，让两半的体积够大，边缘不糊。

> 想加回光线的话：`web/templates/index.html` 的 `.ic-auto` 里补一条
> `<path d="M13.7 8h1.2M12.1 3.9l.9-.9M12.1 12.1l.9.9"></path>` 即可，
> 但在 16px 下会变成三个点，不推荐。

### 23.2 「N 个可更新」加单次忽略

顶栏那个红色药丸以前是纯展示：一直在、点了没反应、也没有关闭的办法。
现在拆成两个可点区域 —— 点文字打开「检查 MOD 更新」并过滤到可更新，
点 × 忽略这一批。

**忽略的是「这一批」，不是永久关掉**：写的是 `data/ui.json` 的 `dismissed_upd`，
指纹只由「可更新的模组名单」算，不含时间。名单真变了（又冒出一个新版本），
指纹变、提示自己回来。和启动同步那条蓝色提示条**共用同一个指纹**，
所以在哪边忽略，另一边也一起消失 —— 不会出现「关了一个、另一个还挂着」。

顺带修了两处：药丸外层从 `<button>` 改成 `<span>`（按钮里套按钮是无效 HTML，
点 × 会连带触发「打开清单」）；× 改用 SVG 画而不是字符 `×`
（字符按字体基线排，在药丸里会偏上，各字号下粗细还不一致）。

### 23.3 已禁用的模组排到最后（纯显示）

**问题**：已禁用的模组夹在加载顺序里，看着乱。用户原来的解法是手动把它们的
加载顺序拖到最后 —— 但那是**真实数据**，一旦禁用再启用，得重新拖回去。

**改法**：`visibleMods()` 在「按加载顺序」排序之后，再做一次稳定分组：
启用的一律在前、禁用的一律在后。**一个 priority 都不改**，纯显示层。

#### 牵出来的真问题：拖拽重排会顺带改掉禁用模组的号

只改显示不行。用户拖一下卡片，原来那套逻辑会 `order.forEach` 把**整张表**
按 10、20、30 重新发号 —— 已禁用的模组也在表里，于是它们的号被改到末尾，
「禁用时看着靠下、启用后位置丢了」的老问题原样复活。

改成：**只给「已启用」的那几个重新发号，而且只发它们原来就占着的那些号。**
`/api/reorder` 因此多了可选参数 `prios`（1:1 指定，必须长度一致、不重复、非负）。
好处有三个：

1. 已禁用的 mod 一个数字都不动 —— 这正是「禁用再启用，顺序不变」的要求；
2. 不会撞号 —— 一个号原本只属于一个模组，重排只在启用那批内部做置换；
3. 不用凭空造新号 —— 拖完还是原来那批数字，`mods.settings` 的 diff 更小。

`/api/reorder` 不带 `prios` 时行为和以前完全一样（整表 10、20、30…），
「加载顺序」对话框走的就是这条路径 —— 那里是**专门**调顺序的地方，
显示的是真实顺序（禁用的也不藏），全表重排正是它该做的事。
对话框顶部加了一句说明这两个列表为什么不一样。

拖拽本身也加了拦截：拖已禁用的卡片会被 `preventDefault` 并提示
「已禁用的模组不占加载顺序，启用之后才需要排位置」，而不是拖了没反应。

### 23.4 验收记录（2026-10-28）

- **22 套测试全绿**；`test_actions.py` 新增 [7e] 六项：
  只重排指定模组时其他模组的号不动、合订本仍最靠前、
  长度不一致/号重复/负数三种入参都被拒。
- 真实库（19 个模组、4 个禁用）：列表里最后四行正是禁用的那四个
  （Priority 160/170/180/190），启用的一行没乱。
- 「N 个可更新 + ×」实测：点击 × 后接口返回 `dismissed: true`，
  刷新界面药丸和蓝色提示条同时消失；复位后重新出现。
- 图标四种状态逐一截图核对（跟随系统亮/暗、手动亮、手动暗），
  明暗两套主题下都不糊。

---

## 24. 1.2.3：主视觉换新（2026-10-29）

### 24.1 来源与边界

设计稿目录为 `waass-icon`（另附 `WAASS-ICON-SPEC.md`，只在开发机保留，
不进仓库）：
深灰底 + 金色 W 字标，三阶金色 `#FEEAAA / #E9B45F / #D99139`，三阶深色
`#1E1E1E / #343636 / #0A0A0A`。用户明确两条边界：**图标原样使用**（不重绘、不改
`waass.ico` 容器）、**双主题但默认深色**。

### 24.2 令牌层

颜色只在 `:root` 和 `[data-theme="dark"]` 两个块里定义，组件一律引用变量。
两条铁律（来自规范 §3，写进样式表注释）：

1. `#E9B45F` 只做底色和描边，**绝不当浅底上的文字**（对白底 1.9:1）；
2. 浅色主题里金色降一档：文字用 `--gold-ink: #8F5A0E`（5.6:1），
   填充用 `--accent: #B87A1E`。

新增令牌：`--on-gold`（金底上的墨字 `#1A1206`）、`--on-accent`
（浅色白 / 深色墨，用于 `--accent` 填充上的文字与勾）、`--gold-1/2/3`、`--gold-edge`、
`--gold-ink`。圆角从 8/4 提到 10/6。

深色底色按规范原值取自 `#1E1E1E`，往上四级：`--panel #262626` →
`--panel-2 #2D2F2F`（卡片）→ `--panel-3 #343636`（hover / 按下）→ `--panel-4 #3D4040`。
`#343636` 在规范里标的是「卡片」，实测当 hover 更合适 —— 19 张卡片全用它会整体发灰。

### 24.3 组件级改动（令牌盖不到的部分）

| 位置 | 改动 | 原因 |
|---|---|---|
| 主按钮 | 金色三阶渐变 + 墨字 + 内高光 | 全站唯一允许用渐变的地方 |
| 顶栏 / 状态栏 | 1px 实线换成 1px 阴影 + 长投影 | 内容是「嵌在框里」而不是被切成三段 |
| 列表视图 | 行背景透明、hover 才给底、`transform` 关掉 | 每行一块底会被读成「整列都选中了」 |
| 卡片 | 浅色白底 / 深色 `panel-2`，hover 抬 1px | 深色下影子不可见，靠底色差和 1px 内高光 |
| 选中卡片 | 金色描边 + 很淡外发光 | 深底上 1px 内描边看不出来 |
| 冲突面板 `cf-info` | 去掉金色底，只留金色左条 | 金色铺满会与「需要留意」的警告底混淆 |
| 开关（深色） | `#5FBF8F` → `#4C9A77` | 15 个薄荷绿药丸抢走整屏注意力 |
| 滚动条（深色） | 用 `#3A3D3D`，hover 才到 `#5A5E5E` | 原 `--border-strong` 太亮 |
| `:focus-visible` | 统一金色 2px 环 | 键盘可达性，鼠标点击不触发 |

### 24.4 默认主题

无 `localStorage['waass.theme']` 时取 `dark`（`index.html` 内联脚本 + `app.js` 的 `S.theme`
两处必须一致，否则首帧会闪一下浅色）。三态循环、`data-theme-mode`、跟随系统监听都没动。
点过切换按钮的用户不受影响 —— 只在「从来没有记录」时才是深色。

### 24.5 图标接入

`waass.ico → web/static/icon.ico`，`-256 → icon.png`，`16/32/48/64/128` 同名归档；
favicon 版本号 `?v=5 → ?v=6`；顶栏与「关于」的 `W` 文字方块换成 `<img>`（顶栏 26px 用
64px 源，关于页 34px 用 128px 源）。

**唯一一处主动偏离设计稿**：`border-radius: 22%`。源图圆角外面是**纯黑像素而不是透明**
（实测 64px 版四角 RGBA = `0,0,0,255`），不裁掉会在深色顶栏上显出一圈更黑的方框。

`tools/make_icon.py` 画的是旧紫底白 W，加了闸门：`icon.ico` 已存在就不写，真要用加 `--force`。

**已知问题（用户决定原样使用，未修）**：`waass.ico` 六帧全是 PNG 压缩，而本项目的
约定是 <256 用 BMP/DIB（部分 Windows 组件不支持小尺寸 PNG，会退回放大图）。
加上 16/32 帧来自 AI 位图降采样，任务栏可能不如 1.2.1 自己画的那版锐利。
将来若要修：`make_icon.py` 里已有 BMP 编码器可复用。

### 24.6 调试参数（新增）

`?dialog=settings | localize | order | track` 直接打开对应弹窗，配合已有的
`?theme= / view= / select= / pick= / nosync=1` 做无头截图核对。

### 24.7 验收记录（2026-10-29）

- 22 套测试全绿（逐个跑：`for f in tests/test_*.py; do python $f; done`，
  `unittest discover` 抓不到这些脚本式套件）。
- 截面对照：深/浅 × 卡片/列表、详情面板、冲突、批量、设置（含关于区）、汉化、加载顺序、
  检查更新。修掉三处：列表行铺底、`cf-info` 铺金、深色开关过亮。
- `WAASS_MULTI=1 python run.py --serve-only` 起服务时不要用 Bash 后台 —— 会被回收，
  必须用受管后台任务。

---

## 25. 1.2.4：版本判定与几处清理（2026-10-29）

### 25.1 报「有更新」但已经更新过了

用户实测：更新 3 个 mod 后重新检查，本地版本号没变，3 个仍挂在「有更新」里。
三个独立成因，全部有数据证据（真实库 20 个 mod、下载目录 380 个包）：

| # | 成因 | 证据 | 修法 |
|---|---|---|---|
| a | 包名分隔符只认下划线 | 新包叫 `ModSettingsMenuFix 13284 1.5.0 2026-…Z token.zip`，解析返回 `None` | `registry._NEXUS_RE` 分隔符改 `[_ ]`，兜底正则同样放宽 |
| b | 不读 `content/info.json` | Hoods 的 `info.json` 写着 `"version": "3.7"`，旧逻辑只看 `version.txt/modinfo` | `scanner._collect` 新增 info.json 分支，`ModRecord.version_file` 标记来源 |
| c | 安装档案（快照）永远压住新包 | LightRewrite 档案 0.27.1（10-25 安装），10-29 又下 0.28.0 | 同 ID 存在比安装时间更晚的包时，档案不参与比较 |

### 25.2 本地版本的新规则：取最高的那一个

原来按「来源可信度」取第一个（手填 > 档案 > 历史 > 下载包 > 自带文件）。
问题在于**更新之后写文件的来源变了**：info.json 换新、包名换新、档案却停在旧值，
按可信度取第一个等于系统性偏向旧快照。

现在：手填仍然优先；其余来源各自给出候选，**取版本号最高的**（`core/version.vkey`
比较），版本相同才用来源优先级决定「标注是谁提供的」。

已知取舍：**只下载没安装**的新包也会被算成「已装」，可能把一次真更新藏起来。
反向错误（拿旧快照当已装）正是用户报的症状，所以选前者。界线写在 `track.py` 注释里。

### 25.3 游戏版本

`core/gamever.py`：在 `bin/<x64_dx12|x64>/witcher3.exe` 里找 `v X.YZ<字母>\0`
（主菜单角标用的同一份字符串）。实测 90 MB 文件 20 ms 命中，按
`(路径, mtime, size)` 缓存。抠不到回退 PE 文件版本（`5.0.16.41566`），
仍然没有就返回空 —— 界面不显示这个标签，而不是显示一个对不上号的版本。

根目录 `BUILD-INFO.json` 写着 `game_version: 5.00c`，**不能当依据**：那是某个
mod 自己的构建清单（`mod_version: 1.9.2`）。真实游戏版本已到 5.01c。

侧栏「游戏目录」下、路径文字旁边显示「游戏版本 5.01c」，金色文字档
（`--gold-ink`，浅底上 5.6:1），不是底色金。

### 25.4 汉化：`名字 + 版本号` 不算待翻

Mod Settings Menu Fix 的 `cn.w3strings` 共 7 条：5 条中文，另 2 条是
`Mod Settings Menu Fix` 与 `Mod Settings Menu Fix 1.5.0`。旧规则只把第 0 条
当模组名，带版本那条被当菜单句子；而 `inspect()` 连分类都不做，把两条都数进
「待翻」。游戏里其实是全中文。

两条修法：

1. `classify()`：`_is_name_with_version()` —— 去符号后等于「模组名 + 纯数字版本」的，
   判 `mod_name`；
2. `inspect()` 的 todo 计数改走 `classify()`，**和 `build_jobs()` 共用一套判断**。
   面板和任务包各说一套是这次误报的结构性原因。

面板文案也改了：只有模组名/物品名可翻时，显示 `why`（如「已经全中文了」）
而不是固定写「只剩模组名和物品名」。

### 25.5 两处移除

- **设置 → 安装行为**（安装后复制一份原包）：界面区块、`/api/settings` 白名单、
  `install.py` 的复制逻辑、`ARCHIVES_DIR`、测试 [11] 一起删。`data/archives`
  清理项保留，标题改成「旧版留下的原包副本」，只用于清遗留文件。
- **搜索框的 `/` 提示框**：`<kbd>`、`.search kbd` 样式、`keydown` 里的
  `/` 聚焦分支一起删（用户要求连快捷键一起去掉）。

### 25.6 验收记录（2026-10-29）

- **23 套测试全绿**（新增 `tests/test_gamever.py` 10 条；`test_track.py` 加 [9b]
  8 条；`test_localize_detect.py` 加 [5b] 4 条）。
- 真实库复跑：报「有更新」从 5 条降到 3 条（All Quest Markers 1.7.0→1.7.3、
  Seamless HUD 2.8.0→2.11.0、Smooth UI 0.3→0.3.2，这三个用户确实没更）；
  Hoods 3.7 / Mod Settings Menu Fix 1.5.0 / Light Rewrite 0.28.0 全部转为
  「已最新 / 本地更高」。汉化待翻从 2 条归零，面板显示「已经全中文了」。
- 解析改动回归：全量 380 个包，新旧解析结果**只差这 3 个新包**，旧格式一个没动。

---

## 26. 1.2.5：能给别人用（2026-10-30）

这一版的目标不是功能，是「GitHub 上发出去，别人 clone 下来是他自己的干净程序」。

### 26.1 游戏目录探测：三路 + 兜底

原来的 `_CANDIDATES` 只有 7 条硬编码路径，第一条是开发机的
`D:\game\steam\...` —— 在作者机器上自然一次命中，别人的机器上几乎必然落空。
「自动识别」这句话在发布版里等于空话。

现在（`core/paths.py`）：

```
WAASS_GAME_DIR  →  data/config.json  →  注册表/库清单  →  公开默认位置
```

| 来源 | 读什么 | 为什么这么读 |
|---|---|---|
| Steam | `HKCU\Software\Valve\Steam\SteamPath` → `steamapps\libraryfolders.vdf` 里每个 `"path"` | 多盘、多库是这个游戏的常态。不做 VDF 解析器：这文件只有一层，正则抓 `"path"` 最省事，也不会因为某个无关字段缺失整份失败 |
| GOG | `HKLM\SOFTWARE\WOW6432Node\GOG.com\Games\1495134320` 的 `path` | Galaxy 装的是 32 位程序，写 WOW6432Node；`KEY_WOW64_32KEY` 和直读都试一遍 |
| Epic | `%PROGRAMDATA%\Epic\EpicGamesLauncher\Data\Manifests\*.item` 的 `InstallLocation` | 清单是 JSON，坏一份只跳过它。**不认 `DisplayName`** —— 那字段可能被本地化，一律拿路径再过 `is_game_root()` |

三条硬约束：

1. **每一项都要能被跳过**。注册表被锁、清单是坏 JSON、路径不存在 —— 逐个
   `except OSError`，探测失败只该让下一个候选上场，不该把启动流程搞崩。
2. **候选要去重**。Steam 的库清单会把自己也列一条，注册表又给一条，大小写
   还不一致（实测同一台机器出 4 条重复）。`_dedupe()` 用 `normcase` 归一。
3. **探测结果要缓存，但不能缓存一个死路径**。`_DETECT_TTL = 20s`；
   命中缓存时仍然 `is_game_root()` 复验一次 —— 游戏被卸掉/挪走，下一次
   调用就得返回 `None` 而不是一条已经没有 exe 的路径。
   `save_config()` 里清缓存，改完配置立刻生效。

`detect_game_root(refresh=True)` 是「重新查找」按钮走的路；
`forget_detected()` 只丢缓存。

### 26.2 只读就是只读

「干净的交付」不只是仓库里没有 `data/`，还包括**程序在没被点任何写操作时，
硬盘上不该多出东西**。实测过一轮，抓到两处：

| 现象 | 结论 |
|---|---|
| 打开汉化面板会生成一份待翻译任务包 | **保留**。那是面板要展示的产物，用户主动打开才发生；`storage.GROUPS` 的 `jobs` 组可以清 |
| `--no-game` 场景下仍然写了 `legacy.json` + `thumbs/` | 测试设计问题：开发机上真的装着游戏，探测又找到了它。要模拟新电脑必须把探测和兜底一起掐掉 |

`tools/coldstart.py`：空 `data/` + 假游戏目录，跑 21 个只读接口，
断言「全部 200」「没有创建 `data/`」「游戏目录里没多出文件」。
`--no-game` 再跑一次「这台机器没装游戏」。发布前必跑。

### 26.3 写接口的 Host / 跨站校验

`GET /` 不鉴权，并且把 token 直接写进 HTML。攻击链：恶意网页把自己的域名
rebind 到 `127.0.0.1` → 浏览器以**同源**身份请求 `/` → 拿到 token →
`install` / `uninstall-all` 全通。

`X-Waass-Token` 挡不住这一条（rebinding 之后是"同源"，自定义头合法）；
真正有效的断点是**校验 Host**：

```python
_LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}
```

`do_GET` / `do_POST` 开头各调一次 `_forbidden()`，不通过回 403 JSON。
顺带拒绝 `Sec-Fetch-Site: cross-site`（浏览器自动带，curl/本机脚本不带，
所以缺失时放行）。

**`/api/bye` 必须放行**：`navigator.sendBeacon` 带不了自定义头，它能提供的
只有 query 里的 token + 浏览器自动加的 Host / `Sec-Fetch-Site: same-origin`。
Host 校验不影响它，但如果以后有人想给写接口加「必须带 `X-Waass` 头」，
这条链路会第一个断掉 —— 关不掉窗 = 留下后台进程 = 下次双击撞单实例锁。

`/api/open-folder` 的 `key` 另外要求「`resolve()` 出来的父目录必须就是
`mods`」。只 `is_dir()` 不够：`key = "..\.."` 也能命中一个真实存在的目录，
等于允许打开机器上任意文件夹。

### 26.4 几处小修

| 位置 | 问题 | 修法 |
|---|---|---|
| `actions.reorder` | `set_state()` 默认 `create=True`，未知 key 会凭空写出一条 `[xxx]`，界面返回 `ok` | 先收集 `_resolve_folder()` 找不到的 key，**整体不动**并抛 `ValueError` → 400 |
| `install.js` `metaLine` | 作者 / 版本号整段未 `esc()` 直接进 `innerHTML` | 逐段转义 |
| `run.py` `background_color` | 写死浅色 `#F4F4F7`，1.2.3 起默认深色 → 首帧闪白 | 写死 `#1E1E1E`。**不能"跟随设置"**：主题存在浏览器 profile 的 localStorage，服务端读不到 |
| `style.css` `--text-3` | 浅 `#8A8478` = 3.3:1、深 `#807C74` = 4.0:1，都低于 AA 的 4.5 | `#6E695F`（5.0:1）/ `#9A958B`（5.6:1） |
| `server.py` `server_version` | 硬编码 `Waass/0.1`，与 `APP_VERSION` 脱节 | `f"Waass/{appinfo.APP_VERSION}"` |

**焦点环不用动**：`:focus-visible { outline: 2px solid var(--accent) }` 早在
1.2.3 就在 `style.css` 末尾了。审计报告按「出现次数 = 2」判它缺失，是只数了
字符串没看内容。

### 26.5 死代码

`core/names.py` + `core/nexus.py` + `tests/test_names.py` 删除。
`nexus.py` 只被 `names.py` 引用（`nexus_meta.py` 是另一个自动抓资料的模块，
仍在用），而 `names.apply_to()` 从 1.2.1 起就被注释掉了 —— 整条链不可达。

`storage.GROUPS` 的 `jobs` 组保留 `names.json` / `names` 两个路径项：
用户本地可能还留着这些文件，留在清理清单里（而不是 KEEP 里）才能被清掉。

### 26.6 发布准备

- `.gitignore`：`build/`、`dist/`、`*.spec`、`data/`、`_backup/`、`_shots/`
  全部排除。`data/` 不放 `.gitkeep` —— 已验证空目录也能跑。
- `CHANGELOG.md`：版本日志从 README 搬出来，README 首屏只留一句 + 链接。
- `Waass.bat` 重写：只用 PATH 里的 `pythonw` / `python`，不写任何人的安装路径。
  **文件必须是 GBK 编码** —— 存 UTF-8 + `chcp 65001` 时带括号的 `if` 块
  会解析错位，实测报「命令语法不正确」。
- README 首屏：系统要求（仅 Windows 10/11 x64 + 游戏 v5.x + Edge/Chrome）、
  三张 1.2.5 截图、exe / 源码两条安装路径、SmartScreen 提示。

### 26.7 验证记录

- **24 套测试全绿**（新增 `test_http_guard.py` 20 条、`test_paths_detect.py`
  25 条；删除 `test_names.py`；`test_actions.py` 加 [7f] 3 条）。
- 本机真实探测：`_probe_candidates()` 从 Steam 注册表读出 4 条候选，
  第一条命中真实游戏目录；冷探测 2 ms、缓存 0.1 ms。
- 冒烟（打包 exe）：新增 **1 个**可见窗口（1792×1097），无多余弹窗；
  发 `WM_CLOSE` 后 9 秒内进程全退，可见窗口回到基线。
- 冷启动：有游戏 / 无游戏两种场景都通过，都没有往 `data/` 或游戏目录里落东西。
- `doctor.py check`：`mods/` 新增 `modUIRE_AlwaysQuestMarks`（用户自己装的）、
  `mods.settings` 的 mtime 被外部工具动过但**内容为 961 字节 / 21 段 / 无幽灵条目**、
  `ModSettingsMenuFix.xml` 是用户 10-29 自己装的版本。都不是本程序改的。
- 打包：`dist/Waass-1.2.5.exe` 8.9 MB。


## 27. 1.2.6：模组的「身份」从哪来（2026-10-30）

### 起因

用户清掉 `RMMR Mod Manager` 之后，封面、作者、更新检查全部失效，点「刷新资料」
也没有任何反应。查下去发现不是同步坏了，而是**整条链的起点没了**。

### 根因：ID 只有两个来源，而且都不是我们自己的

`nexus_id` 决定能不能取作者、封面、最新版本。1.2.5 及以前它只从两处来：

1. `data/legacy.json` —— RMMR 存档（一次性固化，本身不依赖 RMMR 本体）
2. `scanner._find_nexus_id()` —— mod 目录里 readme/txt/md 中的 `nexusmods.com/...mods/12345`

清理 RMMR **同时**用了一个全新的 `data/`（复制 exe 到别的文件夹测试），两个来源
同时归零。实测：这类模组在真机上 21 个，readme 命中 **0** 个。

这不只是「老用户清理了旧软件」才遇到 —— **任何从没装过 RMMR 的新用户，
第一次打开程序就是这个状态**。「检查更新」对他们完全不可用。

### 现状：五档证据

| 档 | 来源 | 可信度 | 什么时候失效 |
|---|---|---|---|
| 1 | `data/track.json` 手填 | 最高，永远优先 | — |
| 2 | `data/nexus_ids.json`（本模块记的） | 高 | 删 data 才失效 |
| 3 | `data/registry.json` 安装档案 | 高（装的时候从包名解析） | 没用本程序装过就没有 |
| 4 | 下载目录原包名 | 高（ID 由 Nexus 生成） | 原包被删 |
| 5 | readme 里的链接 | 中（作者手写，可能指同系列别的 mod） | mod 不带 readme |

档 2 是档 4 的**持久化**：认出来立刻落盘，原包以后被删也不丢。这条很关键 ——
下载目录是用户随时会清理的地方，不能当成长期依赖。

### 判定规则：用「差几个词」，不用相似度分数

`_word_diff()` 数的是两个名字词袋的对称差。自动绑定要求：

1. 差 0～1 个词（`Hoods`↔`Hoods` 差 0；`RoninArmor`↔`dlcRoninArmor` 差 1）
2. 满足条件的 ID **有且只有一个**
3. 分数过 `_BIND_SCORE = 62`

差 2 个词以上一律不绑。例：`Swords On Hip` 对 `Swords On Hip When Cloaked`
差 2，对 `Dynamic Swords (and Crossbow) On Hip` 差 3 —— 两个都不是对方，
**猜错比空着更糟**（「检查更新」会一直报别人的版本）。

实测分数与判定（真机 21 个模组）：

- 自动认出 **16** 个，**0** 个绑错
- 待人工点 4 个，其中 2 个本来就是自建/移植（`UIRE_Travel`、`UIRE_AlwaysQuestMarks`）
- 另外 2 个（合订本、复用上游的 HoodSlots）也不在 Nexus 上单独存在

### 五次踩坑记录（每次都改过一版才定下来）

1. **只做字符级匹配会全军覆没**。文件夹是 `modSwordsOnHip`（连写），Nexus 标题是
   `Swords On Hip`（分词）；`mod_hoodsSwitchableCloak` 和 `Switchable Cloaks - Hoods`
   连词序都相反。必须拆驼峰 + 按词袋比。
2. **词级打分太松会乱绑**。`UIRE_AlwaysQuestMarks` 和 `All Quest Markers On Map`
   只共用 `quest` 一个词，按 Jaccard 也能拿 59 分并成为唯一最高分 → 绑错。
   加了「覆盖率 ≥ 60%」才挡住。
3. **`_LANG_WORDS` 绝不能收 `remaster`**。重制版本体 mod 大多带这个词
   （`UI Remastered Edition` / `SeamlessAdaptiveHUDRemastered`），收进去等于
   把所有本体当翻译版扣 45 分，一个都认不出。两个字母的只留 `cn/ru/pl/fr`，
   `it`/`de`/`es` 在英文标题里是正常词（`Everything is on it`）。
4. **Nexus 的按名搜索是多词 AND，词越多越搜不到**。实测：

   | 关键词 | 结果 |
   |---|---|
   | `autoloot` | 12 条 |
   | `auto loot` | 6 条 |
   | `auto loot enhanced` | **0 条** |

   原先只发「拆好的全词」这一组，凡是「功能词 + 形容词」的写法
   （`AutoLoot Enhanced` 这种极常见的）永远空着候选。现在
   `identify.search_queries()` 产出「全词 → 去包装词 → 前两词 → 首词」几档，
   `nexus_meta.search_multi()` 逐档试，第一档有结果就停。
   注意 `name` 字段（非 stemmed）配 `WILDCARD` 全是 0 条，`nameStemmed` 也只
   接受 `MATCHES`——别的 op 会直接报 schema 错。这条路只有一种写法能用。
5. **包装词会把正主挡在候选线以下**。`AutoLoot Enhanced` 对
   `SmartAutoLoot Remaster V1.1.7`：覆盖率 2/3、多出 `smart`/`remaster`/`v1`
   三个词 → 66 − 60 = 6 分，连 40 分的候选线都过不去。
   - `_FILLER`（enhanced / plus / redux / edition / `v` …）只在**算「多几个词」**
     时不计，**覆盖率照算** —— 否则什么都能覆盖。
   - `V1.1.7` 这种版本前缀在 `tokenize()` 里就丢掉（`isdigit()` 只认纯数字，
     `V1` 拦不住），否则它会被当成一个真实单词。
   - 但**算「差几个词」时 `remaster` 仍要算**（`_FILLER_BIND` 把它剔出来）：
     重制版有时真的另开了一个页面，这条差异抹掉就会自动绑错。

### 排序：证据等级在分数之前

`merge_candidates()` 先按 `src`（both > local > nexus）排，再按分数。
纯按分数排会出事：AutoLoot 的正主 `SmartAutoLoot Remaster` 只有 46 分，
被 `Auto Loot Herbs`（13228，下载量高、分数高）压到第三位，人一眼就跳过去了。
下载目录里的原包名是「你这台机器上的铁证」，该排在联网搜到的同名 mod 前面。

### 「还没对上 Nexus」不能被永久消掉

自制/移植的模组永远搜不到，如果提示条一直挂着这 2 个，用户很快会学会无视所有提示。
`data/track.json` 增加 `ignored` 列表（与 `manual` 并列，`save_manual()` 只改
`manual` 字段，不会互相覆盖），界面每个缺 ID 的行有「不在 Nexus」按钮。

### 联网候选：只备菜，不点菜

`/api/nexus/guess` 把两条路的候选合并（本地包名 + Nexus 搜索），存
`data/nexus_guesses.json`，**三天内不重复搜**。一个都不自动绑 —— 同名的
别家版本太多（`AutoLoot` 能搜出四五个不同作者的实现，正主只排在第一位而已）。
界面 `merge_candidates()` 会标 `src: local / nexus / both`，本地命中的额外注明
「你的下载目录里有这个包」。

### 测试

- 新增 `tests/test_identify.py`（12 组 48 条）：拆词、差词、自动绑、歧义闭嘴、
  翻译版不绑、档案优先、下载目录读不出时不崩、记住/忘掉、候选筛选、两路合并、
  搜索档位、包装词与版本前缀。
- 全量 25 套测试全绿。
- 冷启动：有游戏 / 无游戏都通过，仍未创建 `data/`（假游戏里没有 mod，
  所以识别缓存不会落盘 —— 这条断言只覆盖「没 mod」的情况）。
- 真机：16 自动 / 4 待确认（其中 2 个是自建 mod）/ 0 错绑；
  便携模式（exe 复制到空目录）实测 `data/` 落在 exe 旁，16 个 ID 的资料与封面
  全部取回，关窗后 9 秒内进程退净无残留。

## 28. 1.2.6 第二批：禁用换机制、两处误报、说明书写进程序（2026-10-30）

### 禁用改成给文件夹改名（见 §2）

`actions.set_enabled()` 从「写 `mods.settings` 的 `Enabled=`」整体换成
「`rename()` 加/去 `~` 前缀」。四条约束：

1. 目标文件夹已存在（`~modX` 和 `modX` 同时存在）时**报错而不是覆盖**。
2. 改名走 `safety.Operation.record_rename`，`undo_last()` 直接搬回去。
3. **只在「启用 + 记录仍是 `Enabled=0`」时才写 `mods.settings`**，且写前照样
   `backup()`。禁用永远不写它。
4. 批量开关 `set_enabled_many()` 同构：先算全部目标名并逐一
   `safety.assert_writable()`，再逐个 rename，最后才考虑写一次 settings。
   **先全校验再动手**，避免做到一半被拒绝。

`scanner.py` 里两条「这个是老办法改名的，建议改成 `Enabled=0`」的警告随之删除 ——
老办法现在是正路，再提示就是误导。

### 菜单 XML 的归属：按覆盖率算，不按包含关系

症状：`hoodsSwitchableCloak` 明明汉化过，面板还是报「有待翻 4 条」。

两个 bug 叠在一起：

- **归因失败**：`mod_hoodsSwitchableCloak` 的 stem 是 `hoodsswitchablecloak`，
  文件名是 `switchablecloak`，`stem in flat` 不成立 → 这个 XML 被当成孤儿，
  里面的中文没人认领。
- **两条路各说一套**：`localize.inspect()`（面板）压根没调 `xml_takeover()`，
  只有 `build_jobs()`（生成任务包）看。于是面板说"待翻"、任务包说"没有"。

归属算法现在是这样（`scanner.py`）：

```
扁平化：两者都去掉 mod / _ / -，小写
相同                          → 1.0
一方是另一方子串              → len(短) / len(长)
都不成立                      → 0
覆盖比最高且 >= 0.5 的才算认领
```

阈值取 0.5 的依据：`modCloak` 对 `SwitchableCloak.xml` 只有 5/15 = 33%（同名
但确实不是它，硬认会把别人的菜单抢走），`mod_hoodsSwitchableCloak` 是 15/21
= 71%。**取最高分而不是第一个命中的**，否则字母序靠前的 `modCloak` 会先抢走。

`inspect()` 现在也调 `xml_takeover()`，与 `build_jobs()` 共用同一套判定。
被接管的英文记进 `stale`，界面说「已手工汉化（改的是菜单 XML），语言表里 N 条
是过期残留」，与「有待翻」严格分开。

### 弹窗关闭按钮错位：`button` 的默认 padding

`.icon-btn` 是 32px 见方的方框，里头一个 16px 的 SVG。X 却总是贴在右上角。

原因不在 flex，在 `padding`：浏览器给 `button` 的默认样式是
`padding: 1px 6px`，`box-sizing: border-box` 下 32px 宽里左右各被扣掉 6px，
内容区只剩 18px（16px 图标 + 1px 边框 ×2 已经放不下），图标被挤到一侧。

修法是**明确 `padding: 0` + `inline-flex` 居中**，不靠默认值。
弹窗标题栏那一枚单独收到 26px / 14px 图标（`.modal-head .icon-btn`）。

规则：**凡是靠「默认值大概是对的」撑着的居中，都要写死。**

### Script Merger：装在别处也能用

`merger.py` 增加自定义路径（存 `config.json` 的 `merger_exe`）：

- `configured_exe()` 返回配置里记着的路径（不管还在不在）
- `_custom_exe()` 只在文件真存在时认，不存在就自动退回查找 `<root>/Merger/`
- `exe_path()` 顺序：自定义 → `<root>/Merger/Merger.exe`
- `set_exe()` 校验存在性，`clear_exe()` 清掉自定义

`POST /api/merger/locate` 走 `picker.pick("exe")`（原生对话框），`clear:true`
清掉自定义路径。文件名不含 `merger` / `script` 时返回 `suspicious`，界面提醒
一句但不拦 —— 用户可能把 Merger 改名了。

界面三态：缺失（下载链接 + 指定位置 + 说明文字）/ 合订本不生效 / 已就位
（标注「你指定的位置」+「改回自动查找」）。

⚠️ `data-open` 不是全局约定，只有 `track.js` 自己绑了；其它面板必须用显式 id
加事件绑定，否则点了没反应。

### 使用说明：正文是文件，不是 JS 字符串

`web/static/guide.html` 是**纯片段**（没有 `html/head/body`），
`settings.js::openGuide()` 用 `fetch` 读进来塞进弹窗。

- 不单开浏览器窗口：`--app` 窗口没有地址栏，开出去就回不来。
- 不把九节正文写进 JS 字符串：那样改文案就得动代码。
- 读不到只少这一页，`catch` 里说清楚「不影响其它功能」。
- 样式全走 CSS 变量（`.gd-*` 在 `style.css`），暗色/亮色共用一套，不维护第二份配色。
- 断言的事实必须核对源码：快照留 30 份（`storage.prune_snapshots(30)`）、
  资料缓存 7 天（`nexus_meta.TTL_DAYS`）、汉化历史 20 批
  （`localize_backup.KEEP_HISTORY`）。**写文案时凭印象写数字是事故源。**

调试入口：`?dialog=guide`。

## 29. 1.2.7：关窗就得退（2026-10-30）

### 症状与证据

验收时发现：界面窗口早就不在了，`python run.py` 还在后台跑。检查记录 ——
18:09 `inspect_windows.py msedge` 报「没有可见窗口」，同一台机器上 run.py 进程
已存活 48 分钟，最后靠手动 kill 结束。这会连带一个更烦的症状：下次双击撞上
单实例锁，弹「Waass 已经在运行了，先关掉那个窗口」。

### 根因

原来的退出只有一条路，全押在前端一枪上：

```
pagehide → sendBeacon('/api/bye?t=<TOKEN>') → 服务端 bye_at=now → 6 秒后 os._exit
```

**关窗瞬间这一枪不可靠。** 渲染进程可能在 beacon 入队前就被内核收掉；浏览器
在拆网络服务时也会丢这个包。包没发出去，服务端就在等一个永远不来的招呼 ——
而 `_watch_close_loop` 只认 `bye_at`，永远是 0，于是 `continue` 到天荒地老。

### 修法：三条判据并列，任一成立就退

| 判据 | 条件 | 用途 |
|---|---|---|
| `bye` | 页面打了招呼，过了 6 秒宽限，且期间没有新页面加载（`GET /` 会清零） | 快路径，正常关窗约 6 秒退 |
| `no-window` | 独立窗口模式 且 页面加载过 ≥15 秒 且 系统里查不到标题为 `Waass Mod Manager` 的可见窗口 连续 3 次 且 最近 8 秒没有任何请求 | 兜底，beacon 丢了也能退 |
| `no-page` | 独立窗口模式 且 60 秒内一页都没加载 | 外壳压根没起来，别躺着 |

- `core/winwatch.py`：`EnumWindows` + `GetWindowTextW` + `IsWindowVisible`。
  最小化的窗口 `IsWindowVisible` 仍为真，所以最小化不算「窗口没了」。
  非 Windows 返回 `-1`（不知道），调用方必须按「不知道」处理，**不能当成 0**。
- 为什么还要「最近 8 秒没有请求」：窗口计数会有瞬时误差（重绘、还原动画），
  只看它容易误杀。两条同时成立才动手。
- 为什么 `app_window` 才启用：`--browser` 兜底是普通标签页，切标签标题就变，
  按窗口判会误杀。
- 心跳：前端每 15 秒 `GET /api/health`（无 token 要求），服务端在每个请求入口
  `touch_page()` 记 `last_seen`。没有心跳就分不出「开着没操作」和「已经关了」。
- 保险丝：退出前先 `threading.Timer(3.0, os._exit)`，再清浏览器缓存 ——
  清理可能被杀毒/文件占用拖住，不能让它决定进程什么时候死。
- `WAASS_KEEP=1` 关掉整套自动退出（跑接口、盯窗口时用）。
- 退出原因追加到 `data/lifecycle.log`（保留最近 8KB），事后不用猜是哪条触发的。

### 1.2.8 补充：bye 的迟到枪（同日实测）

切语言 = `location.reload()` → `pagehide` 打 bye beacon。beacon 是异步火忘的，
**可能比新页面的 `GET /` 晚到**：GET / 清零在前，beacon 落地在后，`bye_at`
从此没人清 → 6 秒后 `os._exit`。窗口是独立进程不跟着关，用户面对一个所有
fetch 都失败的「假死」界面 —— 面板打不开、切语言无效。

修法：页面身份证。`GET /` 生成 `page_id` 注入页面；bye beacon 带上它；
对不上当前 `page_id` 的 beacon 直接作废。心跳 `/api/health?page=` 同报身份证，
当前页活着就清 `bye_at`（防 beacon 恰好卡在两件事之间落地的缝隙）。
HTTP 层验证六种时序全过；真机连切两次语言 + 跨宽限期开面板，服务端存活。

### 判据做成纯函数

`server.exit_reason(now, life, win_count, ...)` 不碰时间、不查窗口，参数全进，
返回 `'bye' / 'no-window' / 'no-page' / ''`。`tests/test_lifecycle.py` 直接喂
假时间戳覆盖 11 种情形；真窗口留给冒烟验（构造不了就说明构造不了，
**别用「看起来对」代替实测**）。

实测：不启浏览器、只让「窗口没了」成立，18.8 秒退出（warmup 15 + 3 次采样）。

### 说明书这次核出三处错

`guide.html` 里「提示条颜色」「加载顺序」「导入格式」全对不上代码：

1. 原文「金色=有更新、蓝色=未对上 Nexus」—— 实际是**一条**琥珀色
   `.syncbar`，两件事用 `<i class="bar-div">` 隔开并列（1.2.6 才改的），
   警告是另一条**橙色** `.warnbar`。
2. 原文「数字小的先加载，先加载的在冲突时赢」——后半句是没验证过的引擎行为。
   只保留程序自己的语义：数字越小优先级越高，撞同一个文件听数字小的；
   数字相同由 `conflicts.py` 报「谁覆盖谁看运气」。
3. 原文只写 zip/7z/rar，实际 `archive.SUPPORTED_EXT` 有 8 种。

**教训（第二次了）**：写说明书时每一句都要落到某一行代码上，
特别是颜色、数字、先后顺序这三类。`1.2.6` 那次是数字对不上，这次是颜色和语义。

## 30. 1.2.8：中英双语（2026-10-30）

### 决策

- 不引 gettext，不引前端 i18n 框架。全部键表就是两份 JSON：
  `web/i18n/zh.json`、`web/i18n/en.json`，后端 `core/i18n.py` 和前端
  `web/static/i18n.js` 读同一份 —— 服务端和前端永远同源，不会「界面英文、
  提示弹中文」。
- 键表内联进首页（`%%WAASS_I18N%%`），不是运行时再 fetch。静态文案在脚本
  一加载就填，晚一步会先闪一遍中文。`</` 一律转义成 `<\/`，防文案里的
  `</script>` 提前收口。
- 切语言 = 存配置 + 整页重载，不做热切换。热切换要求每个面板自己重绘，
  漏一个就中英混排；重载只要 0.3 秒，保证没有角落被漏掉。用户已确认。
- 语言三态存 `config.json` 的 `lang`：`auto`（缺省，跟随系统）/ `zh` / `en`。
  系统语言非中文一律英文（`GetUserDefaultUILanguage`，非 Windows 落到环境
  变量）。其它语言的玩家拿英文界面，不搞俄语波兰语翻译。

### 纪律（翻译新增文案时沿用）

1. 键名 `命名空间.用途`，一句一整条，**禁止拼接** —— 中英语序不同。
2. 占位符用 `{name}`，`t()` 做 replace 不做 format（文案里有花括号也不会炸）。
3. 单复数只分 1 / 其它两档：`tn(one_key, many_key, n)`。50 对单复数键，
   `tests/test_i18n.py` 会查成对。
4. 模块顶层不许调 `t()`（语言运行时可切）。有常量表需求的做成「键固定、
   读取时翻译」的 Mapping（见 `localize._CatText`、`storage.GROUPS`）。
5. **前端不得拿译文当标识**匹配后端返回值。1.2.8 前有三处按中文文本匹配
   （安装警告去重 / 还原漂移 / is-live 高亮），英文界面下会静默失效 ——
   全部改成后端给稳定 `code` 字段。以后新增跨端判断一律先想 code。
6. 新键走 `tools/i18n_merge.py` 合并：键冲突报错、en 多出 zh 没有的占位符
   报错、en 省略占位符警告。`tests/test_i18n.py` 查键集合一致、无空值、
   英文无中文残留。
7. **专有名词双语必须对齐**，不能一边是玩家习惯的另一套叫法。已定：
   游戏版本 = 中文「重制版」/ 英文 **The Witcher 3 Remastered**（1.2.9 之前
   英文写成 `(next-gen)`，与中文的「重制版」不是一回事，已改）。
   注意区分：**真实 mod 包名里的 `NEXT-GEN` 是模组自己的标题，不许动**
   —— `core/identify.py` 的 `remaster` 判重逻辑正是围绕这些名字写的。

### 文档口径

原生英文，不逐句直译：`guide.en.html`（英文界面自动读）、`README.md` 的英文半部、
`CHANGELOG.en.md`、`docs/SPEC.en.md`。英文读者不装汉化，「CN localization」
按功能视角介绍。SPEC 本节同步译到 SPEC.en.md §30。

## 31. 1.2.9：数据兼容与启动自检（2026-10-30）

### 目标（用户原话的判断标准）

> 迭代新版本时用户可以无缝衔接，只需要替换主程序即可。

判据：**把 `data/` 保留、只把 exe 换掉，程序起来之后所有东西都在原处** ——
模组列表、分组、Nexus ID、安装档案、汉化原件、存档备份、操作日志。用户不该
为了升级去导出、导入、搬文件夹，也不该在升级后猜「我的东西还在不在」。

### 数据版本

`data/meta.json`：

```json
{"schema": 3, "app": "1.2.9", "first_seen": "…", "last_seen": "…",
 "history": [{"schema": 3, "app": "1.2.9", "at": "…"}]}
```

- `schema` = 数据格式版本。1 = 1.0.x 早期；2 = 1.2.8 及以前（只有若干 json，
  没有版本号）；3 = 1.2.9 起。
- `app` 的语义是**上一次运行的程序版本**，由 `compat.touch()` 独占维护。
  `prepare()` 不许写它 —— 写了自检就永远认为「版本没变」。
- 没有 `meta.json` 但有 `config.json` 或任一核心台账 → 认定 legacy（第 2 版）；
  连数据都没有 → fresh。两者都不能崩，必须各走各的。

### 迁移

- **先备份再动手**：整个核心文件集复制到
  `data/_backup/pre-schema<旧>-<时间>/`，附一份 `README.txt` 说明怎么回退。
  备份失败就**不迁移**（宁可不升，不能裸改）。
- **幂等**：`_STEPS = {2: _migrate_2_to_3}`，每步都要能跑两次结果一样。
  中断（断电、被杀）后下次启动按 meta 里的 schema 接着跑。
- 2→3 这一步刻意**不改任何数据格式**：只补常用目录、隔离坏文件、登记版本号。
  真实格式变化留给未来的 3→4，历史步骤永不修改。
- 需要补的目录只对「已有数据的老用户」补，全新安装不建空目录
  （`tools/coldstart.py` 会查启动后有没有多出文件）。

### 坏文件

读不出来的 `xxx.json` 改名为 `xxx.json.broken-<时间戳>` 并保留，程序当作
没有这份记录继续跑。**绝不删除**——用户手改漏个括号不该等于丢数据。

### 降级保护

`schema > SCHEMA`（数据来自更新版本的程序）时：

- 设 `WAASS_READONLY=1`，`guard` 从此拒绝一切写入；
- **不改 meta、不迁移、不隔离**；
- 界面给红条说明「数据是第 X 版、本程序只认到第 Y 版，已切只读」，
  并且**不给「忽略」**。

### 启动自检（`core/selfcheck.py`）

每次启动在起服务**之前**跑一次（run.py），因为「用户的 data 这一版认不认」
必须在别人读之前回答完。自检自己崩了要吞掉 —— 一个自检把用户挡在门外，
比不检查糟糕得多。

检查项：版本是否刚变（`meta.app` 对比）、schema 兼容、核心 json 可解析、
配置里的游戏目录是否还在、`update_feed` 是否像个网址、data 目录是否可写
（真写一个临时文件再删）。

结果落 `data/selfcheck.json`，保留最近 10 次流水（看得出什么时候换的版本）。
**只读模式下写不了盘也要在内存里留一份** —— 否则用户看到「数据来自更新版本」
却一个字都没说。

### 顶部提醒（对应迭代要求 3：更新或有自检问题都要在顶部提醒）

- 位置：内容区最上面（`#selfBar`），在「需要注意」和「N 个可更新」两条之前。
  放最前面，因为它回答的是「我原来的数据还在不在」。
- 三级：绿（刚更新过且数据核对正常 / 数据已迁移）、黄（有文件被隔离、
  游戏目录不在了、更新源配错）、红（数据读不出来、降级只读）。
- 绿黄可「知道了」（`ui.json` 的 `dismissed_self`，指纹只由条目本身算，
  不掺时间）；**红条不给 ×** —— 忽略了用户就再也看不到原因。
- **文案只给 `code + params`**，句子由前端按当前语言拼。这是 §30 纪律 5 的
  延伸：后端不产出已经翻译好的句子，切语言时连已显示过的提示都会跟着变。
- 「刚被更新过」必须回答「旧数据还正常吗」：`updated_data_ok`
  带 `{from,to,n}`（n = 成功解析的文件数），或 `updated_data_issue` 带坏掉的条数。
  只说欢迎新版本等于什么都没说。

### 设置页

关于区新增「数据格式：第 X 版（程序支持到第 Y 版）」和「重新自检」按钮
（`POST /api/selfcheck`）。出问题时这两个数字是用户唯一能报清楚的线索。

### 纪律（从 1.2.9 起长期有效）

> **任何增删功能或行为改动，必须同时更新中英文两份说明文字。**

范围：`web/static/guide.html` + `guide.en.html`、`README.md`（双语同文件）、
`CHANGELOG.md` + `CHANGELOG.en.md`、`docs/SPEC.md` + `docs/SPEC.en.md`。
只改一边等于给另一半用户留了个错说明，比不写更糟。

## 32. 陌生人环境复查（发版前，2026-10-30）

判据：**把 exe 交给一个和你毫无关系的玩家，他的机器上不能有任何「只在作者机器上
成立」的假设。** 逐项查过，结论与对策如下。

### 32.1 「程序里是不是塞了我的数据」（用户的质疑，必须自证）

- **打包产物只含 Python 标准库与 `web/`**。核对方式：看
  `build/Waass-<版本>/PKG-00.toc`，来自项目目录的条目全部是 `web\...`，
  没有任何 `data\`。
- **仓库不跟踪运行时数据**：`git ls-files` 里只有 `web/i18n/*.json` 是 json，
  `data/` 全在 `.gitignore`。
- **配置没有内置默认值**：`load_config()` 读不到就是空 dict，游戏目录、下载
  目录、Script Merger 位置全部现场探测。
- **反证实验**：造一个只有 `modFakeAlpha` / `modFakeBeta` 的假游戏目录，
  `WAASS_GAME_DIR` 指过去冷启动 → 接口返回模组数正好 2。真机那 20 个一个都
  没出现，证明列表是扫盘得来的。
- **为什么同一台机器上复制 exe 到空文件夹会「秒出全部模组」**：因为游戏就在
  这台机器上，探测与扫描都是当场做的。这正是设计意图，不是残留数据。

### 32.2 权限（最容易在别人机器上炸的一条）

Steam 默认把游戏装在 `C:/Program Files (x86)`，普通用户对那里只读。原来只有
在用户点「安装」时才撞 `PermissionError: [WinError 5] 拒绝访问`，而那句话对
零基础用户等于天书。

- `paths.dir_writable(dir)`：用 `CreateFileW` 以 `FILE_ADD_FILE`（+ 
  `FILE_FLAG_BACKUP_SEMANTICS` 才打得开目录）打开目录本身，让系统算一遍 ACL。
  **全程不创建任何文件** —— 游戏目录一根汗毛都不动。
- 自检新增 `game_readonly`（黄条）：`params.path`。只在真的写不进去时出现。
- 接口层 `_err(exc)`：`PermissionError` → `srv.need_admin`，`FileNotFoundError`
  → `srv.file_gone`，并带稳定 `code`（`need_admin` / `file_gone`）。
  **不再把 `type: str(e)` 原样返回给界面。**

### 32.3 其他复查项

| 项 | 结论 |
| --- | --- |
| 中文 / 空格路径 | 通过（`_work` 里造 `中文 路径\巫师3`，扫描正常） |
| 干净的、没有 `mods\` 目录的安装 | 通过（0 个模组，不报错） |
| 没有 7-Zip / WinRAR / Bandizip | `.zip` 走标准库；`.7z`/`.rar` 报「不支持的格式」并指向缺失工具 |
| 网络不可用 | 所有 `urlopen` 都带 timeout（4 处）；Nexus / 翻译失败只影响该面板 |
| Edge / Chrome 不在标准位置 | 探测补上 `%LOCALAPPDATA%`；全都没有则退回系统默认浏览器 |
| 无 pywebview | 退回 `--app` 独立窗口；再不行退回默认浏览器 |
| exe 放在不可写位置（Program Files） | `data/` 自动落到 `%LOCALAPPDATA%\WaassModManager` |
| 数据目录只读 / 数据来自更新版本 | 只读模式，红条说明，不写一个字节 |

### 32.4 纪律

发布前复查清单里必须有这三条：**打包产物里没有 `data/`**、**源码与文档里没有
作者本机路径**、**权限不足时界面说人话**。

---

## 33. 1.3.0：打包才会犯的错（2026-10-31）

这一版没有新功能。它处理的是「源码里跑得好、打包出来就坏」这一类问题 ——
三次「通过」（构建成功、测试全绿、exe 能启动）都没发现它们。

### 33.1 选目录不能依赖 tkinter

1.2.9 的 exe 里点「浏览选择文件夹」返回 500：`No module named 'tkinter'`。
根因不是代码写错，而是 `build.py` 里一句注释 ——「tkinter 要留着」—— 从来没被
验证过：构建用的 CPython 根本没编 Tcl/Tk，PyInstaller 只是照着 import 图打包，
源码环境有、exe 里没有。

现在的规矩：

* `core/picker.py` 选目录走 `IFileOpenDialog`（`CLSID_FileOpenDialog` +
  `FOS_PICKFOLDERS`），纯 ctypes 走 vtable，不引第三方库；
* 顺序是 原生框 → `SHBrowseForFolderW` → tkinter。**「用不了」和「用户取消」
  必须区分**（`None` vs `""`），否则一取消就会再弹一个 Tk 框；
* 三条都不通时抛 `PickError(code="picker_unavailable")`，界面说「把路径粘贴
  到输入框里」，绝不把 `ImportError` 原文甩给用户；
* `picker.probe()` 只建对象不弹窗，供打包自检使用。

### 33.2 数据版本只有一个推进者

`compat.touch()` 曾经在写「这一版跑过了」时顺手 `setdefault("schema", SCHEMA)`。
后果：备份失败 → 正确地停止迁移并报警 → 但 schema 被标成新版 → 下次启动
`state()` 报 ok，告警消失，失败不会重试。

规矩：**只有成功的 `prepare()` 能改 `meta["schema"]`**。`touch()` 只写
`app` / `last_seen`。迁移失败时 `prepare()` 提前返回、不写 meta，下次启动
继续备份、继续报警。回归测试在 `tests/test_compat.py` 的 [10]。

### 33.3 构建完要问 exe 自己

`build.py` 打包成功后跑 `Waass-x.y.z.exe --check --check-out build/execheck.json`：

| 检查 | 缺失时 |
| --- | --- |
| 界面资源（`templates/index.html`、`app.js`、`style.css`、`settings.js`、两份 `i18n`） | `web_assets` |
| 核心模块能否真的导入（8 个） | `modules_import_failed` |
| 选择框后端（`probe()`） | `no_picker_backend` |

结论写文件（`--windowed` 的 exe 没有 stdout），退出码非 0 就是构建失败。
`--check` 模式不起服务、不开窗口、不建单实例互斥体。

### 33.4 另外两条收口

* **不支持的方法回 JSON 405**（`Allow: GET, POST`）。默认实现是 501 + 一页
  HTML，前端只解析 JSON，那会把「方法不对」变成「解析失败」。
* **全量扫描串行化**：`State.refresh()` 用 `_scan_lock` 排队，并靠 `_scan_gen`
  去重 —— 排队期间别人已经扫完就直接用结果，同一份磁盘不读两遍，
  `scan_result` 也不会被并发覆盖。

### 33.5 控制台编码也是「换台机器才坏」

第一次真正跑 CI（windows-latest 英文镜像）时，28 套测试里 20 多套同时红，
报的都是 `UnicodeEncodeError: 'charmap' codec can't encode ...`。不是逻辑错 ——
Windows 控制台默认用本地代码页，英文系统是 cp1252，编不出中文。

开发机是中文系统（cp936），所以这类错**在本地永远看不到**；而它落在启动
路径上（`print` 是裸的），英文版 Windows 用户跑源码就会死在第一条日志上。

规矩（`core/console.py`，由 `core/__init__.py` 在导入时自动调用一次）：

* 先把控制台代码页切成 65001（`SetConsoleOutputCP` / `SetConsoleCP`）；
* 再把 `stdout/stderr/stdin` 重设为 `utf-8` + `errors="replace"` ——
  老终端仍不支持时最多一个字变问号，**绝不让进程死在一句日志上**；
* 没有控制台（`--windowed` 的 exe）直接跳过，不碰 `_silence_streams()` 的 devnull。

还有一类漏网的：`tools/coldstart.py`、`build.py`、`tools/i18n_merge.py` 等在
**import core 之前**就 print，包入口还没生效，必须各自补一次（前者用
`core.console.enable_utf8()`，不依赖 core 的独立脚本就地 `sys.stdout.reconfigure`）。

放在包入口而不是各入口各调一次：`run.py`、`tests/*`、`tools/*` 都过 `core`，
靠人记着调，迟早漏一个（`test_lifecycle` 就是唯一不 import core 的，已单独补）。

验证：`tests/test_console.py` 用子进程在 cp1252 下跑四组对照（不做处理会崩 /
调过能打 / 只 import core 就够 / 重复调用无害）；CI 里另有一条固定步骤
`PYTHONIOENCODING=cp1252` 跑 `test_console`、`test_lifecycle` 和 `run.py --check`。

## 34. 1.3.1：程序更新检查（2026-10-31）

启动十秒后查一次，结果缓存一天。几个容易写错的地方：

* **缓存文件** `data/update.json`：`{latest, url, notes, etag, ts, checked_at}`。
  `ts` 是上次真正联网的时间，24 小时内直接复用；`etag` 用于条件请求。
* **不缓存「要不要更新」这个结论**。`newer` 每次现算（`track.compare`）——
  程序升级之后缓存里的 latest 可能已经不再是最新，存结论会让顶部提示永远不消失。
* **304 不是「没有更新」**，是「正文没变」。沿用缓存并把 `ts` 刷新即可；
  不要去解析 304 的正文（它是空的）。
* **失败口径分两种**。自动检查（`force=False`）断网时退回上次结论并标 `stale`，
  界面一个字都不说；手动检查（`force=True`）必须 `ok=False` + 一句人话 ——
  用户是点了按钮在等结果，断网时说「已是最新」是骗人。
* **线上比本地旧时不提示**：`compare` 返回 -1 时 `newer=False`，
  避免把降级或测试版本误报成更新。
* 前端在 `app.js` 里 `setTimeout(checkAppUpdate, 10000)`，不 await、不占首屏；
  设置页的手动检查传 `force: true`，并显示这次版本的更新说明。
* 测试 `tests/test_appinfo.py` 用一个本地 HTTP 服务验证：首次联网、24 小时内不重复、
  手动不吃缓存、过期后带 `If-None-Match`、服务器回 304 时结论不变、线上更旧不提示、
  断网的两种口径、关掉检查后完全不联网。
