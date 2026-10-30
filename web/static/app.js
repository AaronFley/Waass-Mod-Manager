/* Waass Mod Manager — 界面逻辑
   无框架、无构建。改完刷新就生效。 */

const TOKEN = window.__WAASS_TOKEN__;
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];

const S = {
  data: null,
  mods: [],
  view: localStorage.getItem('waass.view') || 'grid',
  // 1.2.3 起首次启动默认深色 —— 主视觉是「深灰底 + 金色」，浅色是给白天用的备选。
  // 只在没有任何记录时才取 dark；用户点过切换按钮就永远按他选的来。
  // auto = 跟着 Windows 的明暗走，仍然是三态里的一项。
  theme: localStorage.getItem('waass.theme') || 'dark',
  q: '',
  filter: { type: 'all', value: null },
  sort: localStorage.getItem('waass.sort') || 'priority',
  selected: null,
  // 批量选择：picked 存 key。切筛选/搜索不清空 —— 用户可能先选好再换视图，
  // 但退出批量模式一定要清，否则下次进来会看到一堆莫名其妙的勾。
  picking: false,
  picked: new Set(),
};

/* ---------- 主题：auto / light / dark ---------- */
const THEME_ORDER = ['auto', 'light', 'dark'];
const THEME_LABEL = { auto: '跟随系统', light: '亮色', dark: '暗色' };
const darkMQ = window.matchMedia ? window.matchMedia('(prefers-color-scheme: dark)') : null;
const resolvedTheme = t => (t === 'auto' ? (darkMQ && darkMQ.matches ? 'dark' : 'light') : t);
function applyTheme() {
  document.documentElement.dataset.theme = resolvedTheme(S.theme);
  // 三态图标看的是「用户的原始选择」，不是解析后的明暗
  document.documentElement.dataset.themeMode =
    (S.theme === 'light' || S.theme === 'dark') ? S.theme : 'auto';
  const btn = $('#btnTheme');
  if (btn) btn.title = `外观：${THEME_LABEL[S.theme] || S.theme}（点击切换）`;
}

/* ---------- Nexus 英文分类 → 中文分组 ---------- */
const CAT = {
  'User Interface': '界面',
  'Visuals and Graphics': '画面',
  'Combat': '战斗',
  'Gameplay Changes': '玩法',
  'Items': '装备',
  'Inventory': '装备',
  'Armour': '装备',
  'Weapons': '装备',
  'Bug Fixes': '修复',
  'Performance': '优化',
  'Quests and Adventures': '任务',
  'Immersion': '沉浸',
  'Audio': '音效',
  'Miscellaneous': '杂项',
  'Utilities': '工具',
  'Environment': '环境',
  'Models and Textures': '模型贴图',
  'Magic Spells and Enchantments': '法印',
  'Alchemy, Food, Farming and Housing': '炼金',
  'Characters': '角色',
  'Locations': '地点',
  'Buildings': '建筑',
  'Skills and Perks': '技能',
  'Overhauls': '大修',
};
const catCN = c => CAT[c] || (c ? '其他' : '未分类');

/* 分组色相：同一分类用同一个色相，卡片之间有色差但不会互相打架。
   只在封面底色里透一点点，靠深浅而不是靠鲜艳来区分。 */
const CAT_HUE = {
  界面: 262, 画面: 198, 战斗: 8, 玩法: 152, 装备: 32, 修复: 285,
  优化: 175, 任务: 340, 沉浸: 220, 音效: 48, 杂项: 240, 工具: 205,
  环境: 130, 模型贴图: 310, 法印: 268, 炼金: 92, 角色: 320, 地点: 168,
  建筑: 24, 技能: 246, 大修: 300, 未分类: 240, 其他: 240,
};
const hueOf = m => CAT_HUE[catCN(m?.category)] ?? 240;

/* ---------- 工具 ---------- */
const esc = s => String(s ?? '').replace(/[&<>"']/g, m => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[m]));
const fmtSize = n => {
  if (!n) return '—';
  const u = ['B', 'KB', 'MB', 'GB'];
  let i = 0, v = n;
  while (v >= 1024 && i < u.length - 1) { v /= 1024; i++; }
  return `${v < 10 ? v.toFixed(1) : Math.round(v)} ${u[i]}`;
};
const fmtDate = ts => ts ? new Date(ts * 1000).toLocaleDateString('zh-CN') : '—';
const initials = s => (s || '?').replace(/[^A-Za-z0-9\u4e00-\u9fa5]/g, '').slice(0, 2).toUpperCase() || '?';

async function api(path, body) {
  const opt = {
    headers: { 'X-Waass-Token': TOKEN, 'Content-Type': 'application/json' },
  };
  if (body !== undefined) { opt.method = 'POST'; opt.body = JSON.stringify(body); }
  const r = await fetch(path, opt);
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).error || r.statusText);
  return r.json();
}

/* 浮层提示。第三参数可以带一个动作 —— 写操作成功后直接把「撤销」放在
   提示条里，让「刚做的事能退回去」在发生的那一刻就看得见，而不是等用户
   自己在角落找到撤销按钮。 */
function toast(msg, ms = 2200, action = null) {
  const el = document.createElement('div');
  el.className = 'toast' + (action ? ' has-action' : '');
  const text = document.createElement('span');
  text.textContent = msg;
  el.appendChild(text);
  if (action) {
    const b = document.createElement('button');
    b.className = 'toast-action';
    b.textContent = action.label || '撤销';
    b.onclick = () => { el.remove(); action.fn(); };
    el.appendChild(b);
    ms = Math.max(ms, 5200);          // 有按钮就多留一会儿，别刚看见就消失
  }
  $('#toasts').appendChild(el);
  setTimeout(() => el.remove(), ms);
}

/* 撤销上一步。提示条里的「撤销」和状态栏的按钮走同一条路。 */
async function undoNow() {
  try {
    const r = await api('/api/undo', {});
    toast(r.message ? `已撤销：${r.message}` : '已撤销');
    await load();
  } catch (e) { toast('撤销失败：' + e.message, 4000); }
}

/* ---------- 站内文本输入 ----------
   浏览器自带的 prompt() 在 Edge 的 --app 窗口里样式和整站割裂、拿不到
   输入法的候选框，还可能被企业策略整个屏蔽。这里自己画一个。
   刻意不复用弹窗：它要能在别的弹窗上面用（比如设置页里改路径）。 */
function askText({ title = '请输入', label = '', value = '', placeholder = '', okText = '确定', maxlength = 120 } = {}) {
  return new Promise(resolve => {
    const mask = document.createElement('div');
    mask.className = 'ask-mask';
    mask.innerHTML = `
      <div class="ask-card" role="dialog" aria-modal="true">
        <div class="ask-title">${esc(title)}</div>
        ${label ? `<div class="ask-label">${esc(label)}</div>` : ''}
        <input class="ask-input" id="askInput" value="${esc(value)}"
               placeholder="${esc(placeholder)}" maxlength="${maxlength}" spellcheck="false">
        <div class="ask-actions">
          <button class="btn" id="askCancel">取消</button>
          <button class="btn btn-primary" id="askOk">${esc(okText)}</button>
        </div>
      </div>`;
    document.body.appendChild(mask);
    const input = mask.querySelector('#askInput');
    const done = v => { mask.remove(); document.removeEventListener('keydown', onKey, true); resolve(v); };
    const ok = () => { const v = input.value.trim(); if (v) done(v); else input.focus(); };
    const onKey = e => {
      if (e.key === 'Escape') { e.stopPropagation(); e.preventDefault(); done(null); }
      else if (e.key === 'Enter' && document.activeElement === input) { e.preventDefault(); ok(); }
    };
    document.addEventListener('keydown', onKey, true);
    mask.querySelector('#askOk').onclick = ok;
    mask.querySelector('#askCancel').onclick = () => done(null);
    mask.addEventListener('click', e => { if (e.target === mask) done(null); });
    input.focus();
    input.select();
  });
}

/* 打开外部网页：优先让服务端用系统默认浏览器打开，
   这样不会被弹窗拦截，也不会在程序窗口里跳出新页面。 */
async function openNexus(url) {
  try {
    await api('/api/open-url', { url });
  } catch {
    window.open(url, '_blank', 'noopener');
  }
}

/* ---------- 载入 ---------- */
async function load(refresh = false) {
  $('#btnRefresh').classList.add('is-spinning');
  $('#stGame').textContent = '正在扫描…';
  try {
    if (refresh) await api('/api/refresh', {});
    const d = await api('/api/state');
    S.data = d;
    S.mods = d.scan ? d.scan.mods : [];
    renderAll();
  } catch (e) {
    $('#stGame').textContent = '连接失败：' + e.message;
    toast('扫描失败：' + e.message, 4000);
  } finally {
    $('#btnRefresh').classList.remove('is-spinning');
  }
}

/* ---------- 渲染 ---------- */
function renderAll() {
  renderSidebar();
  renderLock();
  renderList();
  renderStatus();
  renderWarn();
  renderSyncBar();
  if (S.selected) {
    const m = findMod(S.selected);
    m ? renderDetails(m) : showDetailsEmpty();
  }
}

/* 这个 mod 身上有哪些冲突 / 需要注意的地方 */
function conflictsOf(key) {
  return (S.data?.conflicts?.by_mod || {})[key] || [];
}

function renderSidebar() {
  // 只留「全部模组」。Nexus 的英文分类不再自动变成分组 ——
  // 分组归用户自己管，下面「我的分组」才是真正的分类工具。
  $('#navGroups').innerHTML =
    `<button class="nav-item ${S.filter.type === 'all' ? 'is-on' : ''}" data-t="all" data-v="">
      <span>全部模组</span><em>${S.mods.length}</em></button>`;

  // 我的分组（可以把卡片直接拖到这里）
  const lib = S.data?.library || { groups: [], tags: [] };
  const mine = $('#navMine');
  if (mine) {
    if (!lib.groups.length) {
      mine.innerHTML = `<div class="side-empty">点右上「+」新建一个分组，把卡片拖进去。</div>`;
    } else {
      mine.innerHTML = lib.groups.map(gr => {
        const on = S.filter.type === 'group' && S.filter.value === gr.id;
        return `<button class="nav-item ${on ? 'is-on' : ''}" data-t="group" data-v="${esc(gr.id)}"
          data-drop="group" title="拖卡片到这里">
          <span>${esc(gr.name)}</span><em>${gr.count}</em></button>`;
      }).join('');
    }
  }

  const on = S.mods.filter(m => m.enabled).length;
  const upd = S.mods.filter(m => m.update_available).length;
  const withDlc = S.mods.filter(m => m.has_dlc).length;
  const mg = S.data?.scan?.merged || {};
  const st = [
    ['on', '已启用', on],
    ['off', '已禁用', S.mods.length - on],
    ['update', '有更新', upd],
    ['dlc', '含 DLC', withDlc],
    // 合订本不是用户装的 mod，但必须能看见 —— 2026-10-27 的教训：
    // 它排错位置会让游戏报「找不到函数」，而界面上完全无迹可寻。
    ['merged', '已合并', mg.exists ? 1 : 0],
  ];
  $('#navStatus').innerHTML = st.map(([t, label, n]) => {
    const hot = S.filter.type === t;
    return `<button class="nav-item ${hot ? 'is-on' : ''}" data-t="${t}" data-v="">
      <span>${label}</span><em>${n}</em></button>`;
  }).join('');

  $('#gamePath').textContent = S.data?.config?.game_root || (S.data?.game_found ? '已自动识别' : '未设置');
  // 游戏版本：主菜单角标上那个（5.01c）。读不到就整块不显示。
  const gv = S.data?.game_version || {};
  const gvEl = $('#gameVer');
  if (gvEl) {
    gvEl.textContent = gv.version ? `游戏版本 ${gv.version}` : '';
    gvEl.hidden = !gv.version;
    gvEl.title = !gv.version ? ''
      : (gv.src === 'pe'
        ? '从 witcher3.exe 的文件版本读出来的 —— 和主菜单角标可能不是同一套写法'
        : '从 witcher3.exe 里读到的版本号，和主菜单角标一致');
  }
}

/* 合订本伪装成一条只读记录，好让它复用卡片和详情面板的形状。
   数据来自服务端 scan.merged（core/merger.py 的 merged_state + detail）。 */
function mergedRecord() {
  const mg = S.data?.scan?.merged;
  if (!mg?.exists) return null;
  return {
    key: mg.name, folder: mg.name, name: mg.name, name_cn: '', name_nexus: '',
    enabled: !!mg.enabled, priority: mg.priority, is_merged: true,
    category: 'Script Merger', author: '', nexus_id: null,
    version: '', version_src: '', latest_version: '', latest_src: '',
    file_count: mg.files || 0, size_bytes: 0, mtime: 0, thumbnail: '',
    has_scripts: true, has_dlc: false, menu_xmls: [], disabled_menu_xmls: [],
    w3strings: [], dlc_dirs: [], note_cn: '', summary: '',
    update_available: false, downloads: 0,
  };
}

function visibleMods() {
  let list = S.mods.slice();
  const f = S.filter;
  const lib = S.data?.library || { by_key: {} };
  if (f.type === 'merged') {
    const m = mergedRecord();
    list = m ? [m] : [];
  }
  else if (f.type === 'cat') list = list.filter(m => catCN(m.category) === f.value);
  else if (f.type === 'group') list = list.filter(m => (lib.by_key[m.key]?.group?.id) === f.value);
  else if (f.type === 'tag') list = list.filter(m => (lib.by_key[m.key]?.tags || []).includes(f.value));
  else if (f.type === 'on') list = list.filter(m => m.enabled);
  else if (f.type === 'off') list = list.filter(m => !m.enabled);
  else if (f.type === 'update') list = list.filter(m => m.update_available);
  else if (f.type === 'dlc') list = list.filter(m => m.has_dlc);

  const q = S.q.trim().toLowerCase();
  if (q) {
    if (q.startsWith('#')) {
      // #标签 搜索：只在自己的标签里找
      const t = q.slice(1);
      list = list.filter(m => (lib.by_key[m.key]?.tags || [])
        .some(x => x.toLowerCase().includes(t)));
    } else {
      list = list.filter(m =>
        (m.name || '').toLowerCase().includes(q) ||
        (m.key || '').toLowerCase().includes(q) ||
        (m.author || '').toLowerCase().includes(q) ||
        String(m.nexus_id || '').includes(q) ||
        (lib.by_key[m.key]?.tags || []).some(x => x.toLowerCase().includes(q)));
    }
  }

  const cmp = {
    priority: (a, b) => (a.priority ?? 9999) - (b.priority ?? 9999) || a.name.localeCompare(b.name),
    name: (a, b) => a.name.localeCompare(b.name, 'zh'),
    size: (a, b) => b.size_bytes - a.size_bytes,
    mtime: (a, b) => b.mtime - a.mtime,
    category: (a, b) => catCN(a.category).localeCompare(catCN(b.category), 'zh') || a.name.localeCompare(b.name),
  }[S.sort];
  list.sort(cmp);

  // 已禁用的钉在列表最后。**只是显示层，一个 priority 都不改** ——
  // 改了就等于「禁用一下，加载位置就丢了」，再启用还得重排（1.2.1 之前只能手动拖）。
  // 只在「按加载顺序」时这么做：按名称/大小/时间排的时候插一脚就不像排序了。
  if (S.sort === 'priority') {
    const live = [], off = [];
    for (const m of list) (m.enabled ? live : off).push(m);
    return live.concat(off);
  }
  return list;
}

/* ---------- 游戏运行中：整站只读 ----------
   写入本来就过 guard，游戏跑着的时候一定被拒。问题是界面事先不说，
   用户点二十下开关，最后只看到一句「切换失败」。这里改成提前拦：
   顶栏下面一条常驻提示，所有写按钮在捕获阶段就被吃掉。 */
const isLocked = () => !!S.data?.game_running;

const WRITE_SEL = [
  '[data-act="toggle"]', '[data-act="savePrio"]', '[data-act="uninstall"]',
  '#instGo', '#ordSave', '#saveFull', '#saveInc', '#stgCleanSafe', '#setAddDir',
  '#setGameDir', '#optNexusMeta', '#optNexusCover',
  '[data-pkg]', '[data-save]', '[data-stg]', '[data-rmdir]', '#btnUndo',
  '#pickOn', '#pickOff',
].join(',');

function renderLock() {
  const on = isLocked();
  if (document.body.dataset.locked !== (on ? '1' : '0')) {
    document.body.dataset.locked = on ? '1' : '0';
  }
  const bar = $('#lockBar');
  if (!bar) return;
  bar.hidden = !on;
  if (on && !bar.dataset.done) {
    bar.dataset.done = '1';
    bar.innerHTML = `<b>游戏正在运行</b>
      <span>所有会改游戏文件的按钮都锁住了 —— 游戏正读着这些文件，这时候改轻则白改，重则配置或存档出问题。
      关掉游戏后这里会自动恢复。</span>`;
  }
  if (!on) delete bar.dataset.done;
}

document.addEventListener('click', e => {
  if (!isLocked()) return;
  const hit = e.target.closest?.(WRITE_SEL);
  if (!hit) return;
  e.preventDefault();
  e.stopPropagation();
  toast('游戏正在运行，先关掉它再来改', 3200);
}, true);

/* 游戏是不是还开着，界面得自己知道 —— 起游戏、关游戏都不会通知我们。
   15 秒一次的问一下很便宜（一次进程快照，几毫秒），只在状态变化时动 DOM。 */
setInterval(async () => {
  try {
    const j = await api('/api/journal');
    if (!!j.game_running !== !!S.data?.game_running) {
      if (S.data) S.data.game_running = !!j.game_running;
      renderLock();
      renderStatus();
      toast(j.game_running ? '检测到游戏已启动，写入已锁定' : '游戏已关闭，可以继续操作', 3000);
    }
  } catch { /* 服务还没起来或者正在退出，下一轮再说 */ }
}, 15000);

function renderList() {
  const wrap = $('#mods');
  wrap.dataset.view = S.view;
  $$('#viewSeg button').forEach(b => b.classList.toggle('is-on', b.dataset.view === S.view));

  const list = visibleMods();
  const on = S.mods.filter(m => m.enabled).length;
  $('#viewMeta').textContent = `已启用 ${on} / ${S.mods.length}`;
  const lib = S.data?.library || { groups: [] };
  const grp = lib.groups.find(x => x.id === S.filter.value);
  $('#viewTitle').textContent = S.filter.type === 'all' ? '全部模组'
    : S.filter.type === 'cat' ? S.filter.value
    : S.filter.type === 'group' ? (grp ? grp.name : '分组')
    : S.filter.type === 'tag' ? `#${S.filter.value}`
    : { on: '已启用', off: '已禁用', update: '有更新', dlc: '含 DLC', merged: '已合并' }[S.filter.type] || '全部模组';

  $('#empty').hidden = list.length > 0;
  if (!list.length) {
    wrap.innerHTML = '';
    renderEmptyState();
    return;
  }

  // 选中某个 mod 时，把跟它冲突的另外几个在列表里也标出来。
  // 只给当前卡片挂角标是不够的 —— 用户还得自己记住名字再去搜一遍。
  const related = new Set();
  if (S.selected) {
    for (const it of conflictsOf(S.selected)) {
      for (const k of (it.mods || [])) if (k !== S.selected) related.add(k);
    }
  }

  wrap.innerHTML = list.map(m => {
    // 封面只放一个最要紧的标记，多了就是噪音
    const badges = [];
    const cf = conflictsOf(m.key);
    if (m.update_available) badges.push(`<span class="badge badge-danger">可更新</span>`);
    else if (cf.length) badges.push(`<span class="badge badge-warn">注意</span>`);

    const cover = m.thumbnail
      ? `<img src="/${esc(m.thumbnail)}" alt="" loading="lazy" onerror="this.remove()">`
      : '';
    const sel = S.selected === m.key ? 'is-on' : '';
    // 合订本只读：不给开关、不许拖。它能生效的前提就是「排第一且启用」，
    // 任何手动操作都只会把合并结果弄失效（2026-10-27 的事故）。
    const ro = !!m.is_merged;
    const picked = S.picked.has(m.key);

    return `<article class="card ${m.enabled ? '' : 'is-off'} ${sel} ${ro ? 'is-merged' : ''} ${related.has(m.key) ? 'is-related' : ''} ${picked ? 'is-picked' : ''}"
      data-key="${esc(m.key)}" draggable="${ro || S.picking ? 'false' : 'true'}" style="--h:${hueOf(m)}">
      ${S.picking && !ro ? `<button class="pick ${picked ? 'is-on' : ''}" data-act="pick" title="${picked ? '取消选择' : '选中'}"></button>` : ''}
      ${ro ? '' : `<button class="switch ${m.enabled ? 'is-on' : ''}" data-act="toggle" title="${m.enabled ? '点击禁用' : '点击启用'}"></button>`}
      <div class="cover">
        ${cover}
        ${m.thumbnail ? '' : `<div class="cover-initial">${esc(ro ? 'MG' : initials(m.name))}</div>`}
        <div class="cover-badges">${ro ? '<span class="badge badge-ok">只读</span>' : badges.join('')}</div>
      </div>
      <div class="card-body">
        <div class="card-name">${esc(m.name_cn || m.name)}</div>
        <div class="card-sub">${ro
          ? `合订本 · ${m.file_count} 个脚本`
          : (m.name_cn ? esc(m.name) : esc(catCN(m.category)) + (m.author ? ' · ' + esc(m.author) : ''))}</div>
      </div>
      <span class="list-cat">${esc(catCN(m.category))}</span>
      <span class="list-state">${ro ? '自动置顶' : (m.enabled ? '已启用' : '已禁用')}</span>
      <span class="list-badge">${badges[0] || ''}</span>
      <span class="list-prio">${m.priority ?? '—'}</span>
    </article>`;
  }).join('');
}

/* ---------- 批量选择 ----------
   19 个模组用不上「按条件智能筛选」，但「把这一批外观 mod 全关掉看看」
   是常事。做法故意朴素：进模式 → 点卡片勾选 → 一个按钮同时开或关。
   撤销走现成的 journal：后端是一整个 Operation，点一次全回去。 */
function setPicking(on) {
  S.picking = !!on;
  S.picked.clear();
  $('#pickBar').hidden = !S.picking;
  $('#mods').classList.toggle('is-picking', S.picking);
  $('#btnPick').classList.toggle('is-on', S.picking);
  renderList();
  renderPickBar();
}

function pickableMods() {
  return visibleMods().filter(m => !m.is_merged);
}

function togglePick(key) {
  if (S.picked.has(key)) S.picked.delete(key);
  else S.picked.add(key);
  renderList();
  renderPickBar();
}

function renderPickBar() {
  if (!S.picking) return;
  const n = S.picked.size;
  const all = pickableMods();
  $('#pickCount').textContent = n;
  $('#pickOn').disabled = !n;
  $('#pickOff').disabled = !n;
  $('#pickAll').disabled = !all.length;
  $('#pickAll').textContent = n && n >= all.length ? '全不选' : '全选';
}

async function batchToggle(enabled) {
  const keys = [...S.picked];
  if (!keys.length) return;
  const btn = enabled ? $('#pickOn') : $('#pickOff');
  const old = btn.textContent;
  btn.disabled = true;
  btn.textContent = '处理中…';
  try {
    const r = await api('/api/mods/batch_toggle', { keys, enabled });
    setPicking(false);
    await load(true);
    // 禁用/启用都是改文件夹名（加或去掉 `~`），不再动 mods.settings
    const tail = r.renamed ? `（${r.renamed} 个文件夹已改名）` : '';
    toast(`${enabled ? '启用' : '禁用'}了 ${r.count} 个模组${tail}`, 4200,
      { label: '撤销', fn: undoNow });
    refreshJournal();
  } catch (e) {
    toast(e.message, 5200);
    btn.disabled = false;
    btn.textContent = old;
  }
}

$('#btnPick').onclick = () => setPicking(!S.picking);
$('#pickDone').onclick = () => setPicking(false);
$('#pickOn').onclick = () => batchToggle(true);
$('#pickOff').onclick = () => batchToggle(false);
$('#pickAll').onclick = () => {
  const all = pickableMods();
  if (S.picked.size && S.picked.size >= all.length) S.picked.clear();
  else all.forEach(m => S.picked.add(m.key));
  renderList();
  renderPickBar();
};

/* 空列表不能只说一句「没有」。要说清楚为什么空，并给出下一步该点哪里 ——
   原来只有一句话，用户得自己清搜索框、自己回左侧点「全部模组」。 */
function renderEmptyState() {
  const q = S.q.trim();
  const f = S.filter.type;
  // 没有游戏目录时，列表空不是「筛选没结果」，而是根本没东西可扫。
  // 这条以前从没被走过：开发机上永远是「已自动识别」。发布版第一个用户
  // 撞上的就是它，所以它得说清楚下一步点哪儿。
  const noGame = !S.data?.game_found;
  let title = '没有匹配的模组';
  let sub = '换个关键词试试。';
  if (noGame) {
    title = '没找到《巫师 3》';
    sub = '选游戏根目录：那一层里要有 bin 文件夹（不是 bin 里面那一层）。'
        + ' Steam 版一般是 …\\steamapps\\common\\The Witcher 3';
  } else if (q) {
    title = `没有找到「${q}」`;
    sub = '换个关键词，或者把搜索清掉。';
  } else if (f === 'group') {
    title = '这个分组还是空的';
    sub = '把卡片直接拖到左侧的分组名字上就能加进来。';
  } else if (f === 'update') {
    title = '没有可更新的模组';
    sub = '所有模组都已经是 Nexus 上的最新版。';
  } else if (f === 'merged') {
    title = '还没有合并过的脚本';
    sub = '两个模组改同一个脚本时，Script Merger 才会生成合订本。';
  } else if (f !== 'all') {
    title = '这个分类里是空的';
    sub = '回「全部模组」看看其它模组。';
  }
  $('#emptyTitle').textContent = title;
  $('#emptySub').textContent = sub;
  $('#emptyPickGame').hidden = !noGame;
  $('#emptyRedetect').hidden = !noGame;
  $('#emptyClear').hidden = noGame || !q;
  $('#emptyNexus').hidden = noGame || !q;
  $('#emptyAll').hidden = noGame || (f === 'all' && !q);
}

/* 启动自动同步的结果条。
   同步是后台跑的，界面早就能用了 —— 这条只负责在有结果时说一句。
   「已读」也走服务端 data/ui.json：同一批更新忽略过就不再出现，
   等真的又冒出新版本（名单变了，指纹跟着变），它自己回来。 */
function renderSyncBar() {
  const bar = $('#syncBar');
  if (!bar) return;
  const u = S.data?.ui?.updates || {};
  const ids = S.data?.ui?.ids || {};
  const showUpd = u.count && !u.dismissed;
  const showIds = ids.unknown && !S.hideIdBar;

  if (!showUpd && !showIds) { bar.hidden = true; bar.innerHTML = ''; return; }
  bar.hidden = false;

  // 两条会同时成立（有模组能更新、又有模组没对上 Nexus）。1.2.6 之前是
  // 二选一，「缺 ID」永远被「有新版本」盖住 —— 而缺 ID 恰恰是那个
  // 「点一次以后就不用再管」的事，更该摆出来。
  // 两条同时成立时都只留「标题 + 按钮」：一行里塞两段解释会把两边都挤成省略号。
  const both = showIds && showUpd;
  const parts = [];
  if (showIds) {
    parts.push(`<b>${ids.unknown} 个模组还没对上 Nexus</b>
      ${both ? '' : `<span>${ids.guessed
        ? `已找到 ${ids.guessed} 组候选，点一下确认`
        : '对上号才能自动查更新、显示封面和作者'}</span>`}
      <button class="link-btn" id="syncIds">去确认</button>`);
  }
  if (showUpd) {
    const names = (u.names || []).join('、');
    parts.push(`<b>${u.count} 个模组有新版本</b>
      ${both ? '' : `<span>${esc(names)}${u.count > (u.names || []).length ? ' 等' : ''}</span>`}
      <button class="link-btn" id="syncOpen">看看有哪些</button>`);
  }
  bar.innerHTML = parts.join('<i class="bar-div"></i>')
    + '<button class="bar-x" id="syncX" title="这次先不看">忽略</button>';

  const go = $('#syncIds');
  if (go) go.onclick = () => openTrackDialog('missing');
  const up = $('#syncOpen');
  if (up) up.onclick = () => openTrackDialog('new');
  const x = $('#syncX');
  if (x) x.onclick = () => {
    // 忽略只管这次运行：缺 ID 是「功能没接通」，不该被永久消掉。
    if (showIds) S.hideIdBar = true;
    if (showUpd) { ignoreUpdates(); return; }
    renderSyncBar();
  };
}

function renderWarn() {
  /* 只显示「还没被忽略」的警告。忽略记录存在服务端 data/ui.json，
     同一条警告下次开软件不会再出现；冒出新警告时才重新亮。 */
  const ui = S.data?.ui || {};
  const ws = ui.warns || [];
  const bar = $('#warnBar');
  if (!ws.length) { bar.hidden = true; bar.innerHTML = ''; return; }
  bar.hidden = false;
  bar.innerHTML = `<b>${ws.length} 处需要注意</b><span>${esc(ws[0].message)}</span>
    <button class="link-btn" id="warnMore">查看全部</button>
    <button class="bar-x" id="warnIgnore" title="忽略这些提示，以后内容变了才再提醒">忽略</button>`;
  $('#warnMore').onclick = () => {
    toast(ws.map(w => '· ' + w.message).join('\n'), 6000);
  };
  $('#warnIgnore').onclick = async () => {
    try {
      const r = await api('/api/ui/dismiss', { kind: 'warn', keys: ws.map(w => w.key) });
      if (r.ui) S.data.ui = r.ui;
      renderWarn();
      toast('已忽略。以后只有新的问题才会再提醒');
    } catch (e) { toast(e.message, 3500); }
  };
}

/* 底部状态栏最左边那句：游戏关了没有。
   和上面的「只读」提示条读同一个判断，否则会出现「上面说别改、
   下面说可以安全部署」的自相矛盾（1.1.0 验收时踩到过）。 */
function renderGameDot() {
  const d = S.data;
  const live = $('#stGame');
  if (!d || !live) return;
  if (!d.game_found) {
    live.innerHTML = `<span class="dot-idle"></span>未找到游戏目录`;
  } else if (d.game_running) {
    live.innerHTML = `<span class="dot-warn"></span>游戏正在运行，先别改文件`;
  } else {
    live.innerHTML = `<span class="dot-live"></span>游戏已关闭，可以安全部署`;
  }
}

function renderStatus() {
  const d = S.data;
  if (!d) return;
  renderGameDot();
  $('#stScan').textContent = d.scan ? `上次扫描 ${d.scan.scanned_at.replace('T', ' ').slice(5, 16)}` : '';

  // 「下载目录有新包」这条提示 1.0.7 起撤掉了：包的数量不代表任何需要处理的事，
  // 真正要装的时候从侧边栏「导入压缩包」进去就行。侧边栏的入口一直在。

  renderUpdatesChip();
  refreshJournal();
}

/* 顶栏那个「N 个可更新」。两个可点区域分工：
   点数字 → 看是哪几个模组；点 × → 忽略这一批。
   × 写的是 data/ui.json 的 dismissed_upd，和启动同步那条提示条同一个指纹，
   所以从哪边忽略、另一边也跟着消失 —— 不会出现「关了一个、另一个还挂着」。
   判「是不是同一批」只看名单，不含时间：名单真变了（有新版冒出来）它自己回来。 */
function renderUpdatesChip() {
  const pill = $('#updPill');
  if (!pill) return;
  const upd = S.mods.filter(m => m.update_available).length;
  const dis = !!S.data?.ui?.updates?.dismissed;
  if (!upd || dis) { pill.hidden = true; return; }
  pill.hidden = false;
  const txt = $('#btnUpdates');
  if (txt) txt.textContent = `${upd} 个可更新`;
}

async function ignoreUpdates() {
  const sig = S.data?.ui?.updates?.sig || '';
  if (!sig) { toast('暂时没有可忽略的提示', 2500); return; }
  try {
    const r = await api('/api/ui/dismiss', { kind: 'upd', sig });
    if (r.ui) S.data.ui = r.ui;
    renderUpdatesChip();
    renderSyncBar();
    toast('已忽略这一批。等哪天真又冒出新版本，它会再提醒你', 3600);
  } catch (e) { toast(e.message, 3500); }
}

let JOURNAL = [];
async function refreshJournal() {
  try {
    const j = await api('/api/journal');
    JOURNAL = j.items || [];
    if (S.data) S.data.game_running = !!j.game_running;
    const btn = $('#btnUndo');
    btn.disabled = JOURNAL.length === 0 || j.game_running;
    btn.title = j.game_running ? '游戏运行中，不能撤销' : (JOURNAL[0] ? `撤销「${JOURNAL[0].label}」` : '没有可撤销的操作');

    // 状态栏的「最近一条」：刚发生了什么，不用点开日志也能看见。
    const rec = $('#stRecent');
    if (rec) {
      if (JOURNAL[0]) {
        rec.hidden = false;
        const t = (JOURNAL[0].time || '').slice(11, 16);
        rec.innerHTML = `<span class="st-tick">✓</span>`
          + `<span>${esc(JOURNAL[0].label)}</span>`
          + (t ? `<em>${esc(t)}</em>` : '');
        rec.title = '点开看最近的操作记录（可逐条撤销）';
      } else {
        rec.hidden = true;
        rec.innerHTML = '';
      }
    }
    renderLock();
    renderGameDot();          // 游戏中途开/关，状态栏这句要跟着变
  } catch { /* 忽略 */ }
}

/* 弹窗的开关都走这两个函数。中间那层钩子是为了让面板能说「等等，
   我还有没保存的改动」—— 加载顺序的拖动如果被 ✕ 静默吃掉，用户会以为
   顺序已经改了（1.1.0 之前就是这样）。 */
let MODAL_HOOKS = [];

function onModalClose(fn) { MODAL_HOOKS.push(fn); }
function runModalHooks() {
  const hooks = MODAL_HOOKS;
  MODAL_HOOKS = [];
  for (const fn of hooks) {
    try { fn(); } catch { /* 单个钩子出错不该拖累关窗 */ }
  }
}

function openModal(title, html, opts = {}) {
  runModalHooks();
  clearModalGuards();          // 上一个面板的「未保存」拦截不该管到新面板
  $('#modalTitle').textContent = title;
  $('#modalBody').innerHTML = html;
  $('#modalMask').hidden = false;
  const box = $('.modal', $('#modalMask'));
  if (box) box.classList.toggle('is-wide', !!opts.wide);
}

/* 返回 false 表示「被拦住了，没关成」。force 用于保存成功后主动收起。 */
let MODAL_GUARDS = [];
function guardModalClose(fn) { MODAL_GUARDS.push(fn); }
function clearModalGuards() { MODAL_GUARDS = []; }

function closeModal(force = false) {
  if (!force) {
    for (const fn of MODAL_GUARDS.slice()) {
      try { if (fn() === false) return false; } catch { /* 同上 */ }
    }
  }
  runModalHooks();
  $('#modalMask').hidden = true;
  return true;
}


function showDetailsEmpty() {
  $('#detailsEmpty').hidden = false;
  $('#detailsBody').hidden = true;
}

function findMod(key) {
  return S.mods.find(x => x.key === key)
    || (S.data?.scan?.merged?.name === key ? mergedRecord() : null);
}

/* 合订本详情：只读。重点不是「它多大」，而是「它合了谁、还在不在、位置对不对」。 */
function renderMergedDetails(m) {
  $('#detailsEmpty').hidden = true;
  const body = $('#detailsBody');
  body.hidden = false;
  const mg = S.data?.scan?.merged || {};
  const detail = mg.detail || [];

  const rows = [
    ['状态', mg.ok
      ? '<span class="ok">正常生效</span>'
      : `<span class="bad">${esc(mg.problem || '不生效')}</span>`],
    ['加载顺序', `${m.priority ?? '—'}<span class="drow-note">比所有模组都靠前才算对</span>`],
    ['合并文件', `${m.file_count} 个脚本`],
    ['登记状态', mg.entry ? 'mods.settings 里有' : 'mods.settings 里<b>没有</b>'],
  ];

  const groups = detail.map(d => {
    const srcs = (d.sources || []).map(s =>
      `<span class="tag ${s.present ? '' : 'tag-bad'}">${esc(s.name)}${s.present ? '' : ' · 已不在硬盘'}</span>`).join('');
    return `<div class="mg-group">
      <div class="mg-rel"><code>${esc(d.rel)}</code>${d.in_product ? '' : '<span class="mg-miss">产物里找不到这个文件</span>'}</div>
      <div class="tag-row">${srcs || '<span class="muted">台账没记来源</span>'}</div>
    </div>`;
  }).join('');

  body.innerHTML = `
    <div class="detail-cover" style="--h:262">
      <div class="cover-initial">MG</div>
    </div>
    <div class="detail-name">${esc(m.name)}</div>
    <div class="detail-sub">Script Merger 合订本 · 不是模组</div>

    <div class="detail-rows">
      ${rows.map(([k, v]) => `<div class="drow"><span>${k}</span><span>${v}</span></div>`).join('')}
    </div>

    <div class="detail-section">
      <h3>合并了哪些脚本</h3>
      <div class="mg-list">${groups || '<div class="muted" style="font-size:11px">台账里没有合并记录。</div>'}</div>
    </div>

    <div class="detail-section">
      <h3>为什么动不得</h3>
      <div class="detail-summary">这个文件夹里放的是「几个模组改同一个脚本」的合成结果。
      它必须排在所有模组之前加载，否则原文件会把合成结果盖掉 ——
      游戏启动就报「Could not find function ...」，但报错只会指向来源模组，看不出真正原因。
      这里只做展示，启用/禁用/排序交给下面的按钮。</div>
    </div>

    <div class="detail-actions">
      <button class="btn" data-act="conflicts">去冲突检查</button>
      <button class="btn btn-ghost" data-act="mergerOpen">打开 Script Merger</button>
    </div>`;
}

function renderDetails(m) {
  if (m?.is_merged) return renderMergedDetails(m);
  $('#detailsEmpty').hidden = true;
  const body = $('#detailsBody');
  body.hidden = false;

  const srcTag = m.version_src
    ? `<span class="drow-src">${esc({ manual: '你填的', manifest: '安装记录', legacy: '历史记录', rmmr: '历史记录', archive: '下载的包', folder: 'mod 自带' }[m.version_src] || '')}</span>`
    : '';
  const liveTag = m.latest_src === 'nexus'
    ? '<span class="drow-src is-live">刚查到</span>'
    : (m.latest_src === 'legacy' ? '<span class="drow-src">历史记录</span>' : '');
  const latest = m.latest_version
    ? `${esc(m.latest_version)}${liveTag}${m.latest_at
      ? `<span class="drow-src">${esc(m.latest_at.slice(0, 10))}</span>` : ''}`
    : '<span class="muted">还没查到</span>';

  // Nexus 页面顶部那个版本号是作者手填的展示值，经常落后于他自己传的主文件。
  // 两个数不一样时把页面值也摆出来，免得你去网页一看对不上就以为算错了。
  // 注意用 span 而不是 div：这一格本身就在 <span> 里，div 会造成非法嵌套。
  const pageNote = (m.page_version && m.latest_version
    && m.page_version !== m.latest_version)
    ? `<span class="drow-note">页面上显示的是 ${esc(m.page_version)}，那是作者手填的；
       这里用的是他上传的主文件版本${m.latest_file ? '（' + esc(m.latest_file) + '）' : ''}。</span>`
    : '';

  // 只留「决定我现在要不要动它」的几行。文件数/体积/安装时间/下载量
  // 是查档案时才看的，塞在这里会把状态和版本挤到需要滚动的位置。
  const rows = [
    ['状态', m.enabled ? '<span class="ok">已启用</span>' : '已禁用'],
    ['本地版本', m.version ? esc(m.version) + srcTag : '<span class="muted">未记录</span>'],
    ['Nexus 版本', latest + pageNote],
    ...(m.author ? [['作者', esc(m.author)]] : []),
    ['Nexus ID', m.nexus_id
      ? `<span class="mono">${esc(m.nexus_id)}</span>`
      : '<span class="muted">未识别</span>'],
  ];

  const techRows = [
    ['文件数', m.file_count],
    ['体积', fmtSize(m.size_bytes)],
    ['安装时间', fmtDate(m.mtime)],
    ...(m.downloads ? [['下载量', Number(m.downloads).toLocaleString()]] : []),
  ];

  const tags = [];
  if (m.has_scripts) tags.push('脚本');
  if (m.has_dlc) tags.push('含 DLC');
  if (m.menu_xmls.length) tags.push(`${m.menu_xmls.length} 个菜单`);
  if (m.disabled_menu_xmls.length) tags.push(`${m.disabled_menu_xmls.length} 个停用菜单`);
  if (m.w3strings.length) tags.push(`${m.w3strings.length} 个语言文件`);
  if (!tags.length) tags.push('纯资源');

  const cf = conflictsOf(m.key);
  // 详情面板里只放「会出事」的那几条，标题一行就够 —— 完整说明去冲突检查看。
  // 原来把每条正文都铺开，三条就能把版本信息和开关挤出屏幕。
  const cfMain = cf.filter(it => it.level !== 'info');
  const cfRest = cf.length - cfMain.length;
  const pkg = (S.data?.packages?.active || [])
    .find(p => (p.mods || []).includes(m.key));
  const cfHtml = (cfMain.length || cfRest) ? `    <div class="detail-section">
      <h3>冲突与注意</h3>
      ${cfMain.length ? `<div class="cf-list cf-compact">
        ${cfMain.slice(0, 3).map(it => `<div class="cf-item cf-${it.level}">
          <div class="cf-title">${esc(it.title)}</div>
        </div>`).join('')}
      </div>` : ''}
      <button class="btn btn-sm" data-act="conflicts" style="margin-top:8px;width:100%">
        打开冲突检查${cfRest ? `（另有 ${cfRest} 条说明）` : ''}</button>
    </div>` : '';

  const lib = S.data?.library || { by_key: {}, groups: [] };
  const mine = lib.by_key[m.key] || {};
  const myTags = mine.tags || [];
  const myGroup = mine.group;
  const groupHtml = `
    <div class="detail-section">
      <h3>我的分组</h3>
      <div class="tag-row">${myGroup
        ? `<span class="tag is-mine">${esc(myGroup.name)}</span>`
        : '<span class="muted" style="font-size:11px">未分组 · 也可以把卡片拖到左侧分组里</span>'}</div>
      ${lib.groups.length ? `<select class="group-sel" id="groupSel">
        <option value="">— 不分组 —</option>
        ${lib.groups.map(g => `<option value="${esc(g.id)}" ${myGroup && myGroup.id === g.id ? 'selected' : ''}>${esc(g.name)}</option>`).join('')}
      </select>` : ''}
    </div>
    <div class="detail-section">
      <h3>标签</h3>
      <div class="tag-row">${myTags.length
        ? myTags.map(t => `<span class="tag tag-rm" data-tag="${esc(t)}" title="点击删除">${esc(t)} ×</span>`).join('')
        : '<span class="muted" style="font-size:11px">还没有标签</span>'}</div>
      <div class="tag-edit"><input id="tagInput" placeholder="加标签，回车确认" maxlength="40"></div>
    </div>`;

  body.innerHTML = `
    <div class="detail-cover" style="--h:${hueOf(m)}">
      ${m.thumbnail ? `<img src="/${esc(m.thumbnail)}" alt="">` : `<div class="cover-initial">${esc(initials(m.name))}</div>`}
    </div>
    <div class="detail-name">${esc(m.name_cn || m.name)}</div>
    <div class="detail-sub">${m.name_cn ? esc(m.name) + (m.author ? ' · ' + esc(m.author) : '') : esc(m.author || '未知作者') + (m.category ? ' · ' + esc(m.category) : '')}</div>
    ${m.note_cn ? `<div class="detail-note">${esc(m.note_cn)}</div>` : ''}

    <div class="detail-quick">
      <button class="btn ${m.enabled ? '' : 'btn-primary'}" data-act="toggle">${m.enabled ? '禁用' : '启用'}</button>
      <button class="btn" data-act="folder">打开目录</button>
      ${m.nexus_id
        ? `<button class="btn" data-act="nexus">模组页</button>
           <button class="btn btn-ghost" data-act="nexusFiles">下载页</button>`
        : `<button class="btn" data-act="findNexus">搜一下是哪个</button>`}
    </div>

    ${cfHtml}

    <div class="detail-rows">
      ${rows.map(([k, v]) => `<div class="drow"><span>${k}</span><span>${v}</span></div>`).join('')}
    </div>

    <div class="detail-section">
      <h3>包含内容</h3>
      <div class="tag-row">${tags.map(t => `<span class="tag">${esc(t)}</span>`).join('')}</div>
    </div>

    ${m.summary ? `<div class="detail-section">
      <h3>Nexus 上的简介</h3>
      <div class="detail-summary">${esc(m.summary)}</div>
    </div>` : ''}

    ${m.name_nexus && m.name_nexus !== m.name && m.name_nexus !== m.name_cn
      ? `<div class="detail-section">
      <h3>Nexus 上的完整标题</h3>
      <div class="detail-summary">${esc(m.name_nexus)}</div>
    </div>` : ''}

    ${groupHtml}

    <div class="detail-section">
      <h3>加载顺序</h3>
      <div class="prio-row">
        <input type="number" id="prioInput" value="${m.priority ?? ''}" placeholder="—" step="10" min="0">
        <button class="btn" data-act="savePrio" style="height:26px;padding:0 10px">保存</button>
        <span class="muted" style="font-size:11px">数字越小越先加载</span>
      </div>
    </div>

    <details class="detail-fold">
      <summary>技术细节</summary>
      <div class="detail-rows">
        ${techRows.map(([k, v]) => `<div class="drow"><span>${k}</span><span>${v}</span></div>`).join('')}
      </div>
      <div class="detail-section">
        <h3>文件夹</h3>
        <div class="tag-row">
          <span class="tag">${esc(m.folder)}</span>
          ${m.dlc_dirs.map(d => `<span class="tag">${esc(d)}</span>`).join('')}
        </div>
      </div>
    </details>

    ${pkg ? `<div class="detail-danger">
      <button class="btn btn-ghost" data-act="uninstall"
        title="卸载在「已安装的包」里做，会先让你确认">卸载这个模组…</button>
      <div class="detail-danger-note">卸载以「包」为单位 —— 当初一起装进来的模组会一起移走</div>
    </div>` : ''}`;
}

function select(key) {
  S.selected = key;
  const m = findMod(key);
  $$('.card').forEach(c => c.classList.toggle('is-on', c.dataset.key === key));
  if (m) renderDetails(m);
}
window.__select = select;

/* ---------- 分组与标签 ---------- */
function tagsOf(key) {
  return (S.data?.library?.by_key?.[key]?.tags) || [];
}

async function saveTags(key, tags) {
  try {
    await api('/api/library', { action: 'set_tags', key, tags });
    S.data.library = await api('/api/library');
    renderSidebar();
    if (S.selected === key) {
      const m = S.mods.find(x => x.key === key);
      if (m) renderDetails(m);
    }
    toast(tags.length ? '标签已更新' : '标签已清空');
  } catch (e) { toast('保存失败：' + e.message, 4000); }
}

async function addToGroup(gid, key) {
  if (!gid || !key) return;
  const g = (S.data?.library?.groups || []).find(x => x.id === gid);
  try {
    await api('/api/library', { action: 'set_member', id: gid, key, on: true });
    S.data.library = await api('/api/library');
    renderSidebar();
    if (S.selected === key) {
      const m = S.mods.find(x => x.key === key);
      if (m) renderDetails(m);
    }
    toast(`已加入「${g ? g.name : '分组'}」`);
  } catch (e) { toast('加入失败：' + e.message, 4000); }
}

async function newGroup() {
  const name = await askText({
    title: '新建分组',
    label: '给分组起个名字，比如「画面增强」「必备」「整合包 A」',
    placeholder: '分组名字',
    okText: '建这个分组',
    maxlength: 40,
  });
  if (!name) return;
  try {
    const r = await api('/api/library', { action: 'create_group', name });
    S.data.library = await api('/api/library');
    S.filter = { type: 'group', value: r.group.id };
    renderSidebar(); renderList();
    toast(`分组「${r.group.name}」建好了，把卡片拖进来`);
  } catch (e) { toast('新建失败：' + e.message, 4000); }
}

/* ---------- 交互 ---------- */
document.addEventListener('click', async (e) => {
  if (e.target.closest('#btnNewGroup')) { e.stopPropagation(); newGroup(); return; }

  const nav = e.target.closest('.nav-item');
  if (nav) {
    S.filter = { type: nav.dataset.t, value: nav.dataset.v || null };
    renderSidebar();
    renderList();
    return;
  }

  const seg = e.target.closest('#viewSeg button');
  if (seg) {
    S.view = seg.dataset.view;
    localStorage.setItem('waass.view', S.view);
    renderList();
    return;
  }

  const sw = e.target.closest('[data-act="toggle"]');
  if (sw) {
    e.stopPropagation();
    const card = sw.closest('[data-key]') || $('.card.is-on');
    const key = sw.closest('[data-key]')?.dataset.key || S.selected;
    if (!key) return;
    await toggleMod(key);
    return;
  }

  const pickBox = e.target.closest('[data-act="pick"]');
  if (pickBox) {
    e.stopPropagation();
    const key = pickBox.closest('[data-key]')?.dataset.key;
    if (key) togglePick(key);
    return;
  }

  const rmTag = e.target.closest('.tag-rm');
  if (rmTag && S.selected) {
    const key = S.selected;
    const left = tagsOf(key).filter(t => t !== rmTag.dataset.tag);
    await saveTags(key, left);
    return;
  }

  const card = e.target.closest('.card');
  if (card) {
    const key = card.dataset.key;
    if (S.picking) {
      // 批量模式下点整张卡片就是勾选，不用非得点中左上角那个小方块
      const m = findMod(key);
      if (m && !m.is_merged) togglePick(key);
    } else {
      select(key);
    }
  }

  const act = e.target.closest('#detailsBody [data-act]');
  if (act && S.selected) {
    const m = findMod(S.selected);
    const a = act.dataset.act;
    if (a === 'conflicts') {
      openConflictsDialog(true);
    } else if (a === 'mergerOpen') {
      act.disabled = true;
      api('/api/merger/open', {})
        .then(() => toast('Script Merger 已启动', 4000))
        .catch(err => toast(err.message, 6000))
        .finally(() => { act.disabled = false; });
    } else if (a === 'nexus' && m?.nexus_id) {
      openNexus(`https://www.nexusmods.com/witcher3/mods/${m.nexus_id}`);
    } else if (a === 'nexusFiles' && m?.nexus_id) {
      openNexus(`https://www.nexusmods.com/witcher3/mods/${m.nexus_id}?tab=files`);
    } else if (a === 'findNexus' && m) {
      // 交给追踪面板：那里能搜、能看候选、能确认关联
      if (typeof openTrackDialog === 'function') openTrackDialog('missing', m.key);
      else openNexus(`https://www.nexusmods.com/witcher3/search/?gsearch=${encodeURIComponent(m.name || '')}&gsearchtype=mods`);
    } else if (a === 'folder' && m) {
      try { await api('/api/open-folder', { key: m.key }); }
      catch (err) { toast('打不开：' + err.message); }
    } else if (a === 'uninstall' && m) {
      if (typeof openPackagesDialog === 'function') {
        const p = (S.data?.packages?.active || [])
          .find(x => (x.mods || []).includes(m.key));
        openPackagesDialog(p ? p.id : '');
      }
    } else if (a === 'savePrio' && m) {
      const v = parseInt($('#prioInput').value, 10);
      if (Number.isNaN(v)) return toast('请填一个数字');
      try {
        await api('/api/mods/priority', { key: m.key, priority: v });
        m.priority = v;
        renderList(); renderDetails(m);
        toast(`「${m.name}」顺序改为 ${v}`, 3200, { label: '撤销', fn: undoNow });
        refreshJournal();
      } catch (err) { toast('保存失败：' + err.message, 4000); }
    }
  }
});

/* ---------- 冲突检查面板 ---------- */
let CF_TIMER = null;
let CF_DIGEST = '';
let CF_STATS = { merge: 0, warn: 0, merged: 0 };

function cfDigest(s) {
  return [s.mtime, s.count, s.merged?.ok, s.merged?.priority, s.merged?.files].join('|');
}

/* 用户去 Script Merger 里合完，会切回来 —— 以前要他自己记得点「重新检查」。
   这里在面板开着的时候盯着台账，一变就重算，并且直接给结论。 */
async function pollMerger() {
  if ($('#modalMask').hidden) { stopMergerWatch(); return; }
  let s;
  try { s = await api('/api/merger/state'); } catch { return; }
  const dg = cfDigest(s);
  if (dg === CF_DIGEST) return;
  CF_DIGEST = dg;

  const prev = { ...CF_STATS };
  await openConflictsDialog(true, true);
  const now = { ...CF_STATS };

  let msg;
  if (now.merge !== prev.merge) {
    msg = now.merge < prev.merge
      ? `合并完成 · 待合并 ${prev.merge} → ${now.merge}`
      : `台账变了 · 待合并 ${prev.merge} → ${now.merge}`;
  } else if (now.merged !== prev.merged) {
    msg = `合并记录更新了 · 已合并 ${now.merged} 组`;
  } else {
    msg = 'Script Merger 的台账变了，已经重新检查';
  }
  if (!now.merge && !now.warn) msg += '，现在没有要处理的东西';
  toast(msg, 5600);
}

function stopMergerWatch() {
  if (CF_TIMER) { clearInterval(CF_TIMER); CF_TIMER = null; }
}

function startMergerWatch() {
  stopMergerWatch();
  onModalClose(stopMergerWatch);
  CF_TIMER = setInterval(pollMerger, 3000);
}

async function openConflictsDialog(force = false, quiet = false) {
  const rep = await api('/api/conflicts' + (force ? '?force=1' : ''));
  const items = rep.items || [];
  const st = rep.stats || {};

  const stat = (label, v, cls = '') =>
    `<div class="cf-stat ${cls}"><b>${v}</b><span>${label}</span></div>`;

  // 1.1.1 起条目变多了（资源包竞争、注解叠加、文字表版本…），平铺一列会看不清。
  // 按「这是哪一类问题」分组，组内按严重程度排。分类表放前端，后端只管产出。
  const CF_CAT = {
    merge: '脚本合并', res: '界面与资源', script: '脚本行为',
    text: '文字显示', other: '其它',
  };
  const CF_MAP = {
    script_overlap: 'merge', script_merged: 'merge', merge_stale: 'merge',
    merge_empty: 'merge', merge_unregistered: 'merge',
    bundle_ui: 'res', bundle_data: 'res',
    anno_wrap: 'script', anno_replace: 'script', ws_encoding: 'script',
    w3strings_stale: 'text', w3strings_clash: 'text',
  };
  const LV_ORDER = { merge: 0, warn: 1, info: 2, ok: 3 };

  const cardHtml = it => `
    <div class="cf-item cf-${it.level}">
      <div class="cf-title">${esc(it.title)}</div>
      <div class="cf-detail">${esc(it.detail)}</div>
      ${(it.mods || []).length ? `<div class="cf-mods">${it.mods
        .map(k => `<button class="tag cf-mod" data-jump="${esc(k)}">${esc(k)}</button>`).join('')}</div>` : ''}
      ${(it.files || []).length ? `<div class="cf-files">${it.files.slice(0, 6)
        .map(f => `<code>${esc(f)}</code>`).join('')}${it.files.length > 6
        ? `<span class="muted">…共 ${it.files.length} 个</span>` : ''}</div>` : ''}
    </div>`;

  const grouped = {};
  items.forEach(it => {
    const cat = CF_MAP[it.code] || 'other';
    (grouped[cat] = grouped[cat] || []).push(it);
  });

  const rows = items.length ? ['merge', 'res', 'script', 'text', 'other']
    .filter(c => grouped[c])
    .map(c => `
      <div class="cf-group">
        <div class="cf-group-title">${CF_CAT[c]}<em>${grouped[c].length}</em></div>
        ${grouped[c]
          .sort((a, b) => (LV_ORDER[a.level] ?? 9) - (LV_ORDER[b.level] ?? 9))
          .map(cardHtml).join('')}
      </div>`).join('') : `
    <div class="cf-empty">
      <div class="cf-empty-title">没有发现冲突</div>
      <div class="cf-empty-sub">${st.script_files || 0} 个脚本之间没有同名覆盖，
        ${st.bundle_mods || 0} 个资源包里也没有抢同一个界面文件，菜单和加载顺序都没撞车。</div>
    </div>`;

  // Script Merger 只在真的还有未合并的脚本、或合并记录失效时才值得启动。
  // 只是想看「已合并」的详情，就不该弹它出来。
  // 注意：merger 段在 stats 里面，不在顶层。
  const mg = st.merger || {};
  const needMerge = (st.merge || 0) > 0 || (mg.stale || 0) > 0;
  // 合订本不生效是「什么冲突都没有、但游戏一定启动失败」的那一类，
  // 比有待合并的脚本更紧急，所以单独一条红条，按钮直接送去修。
  const bad = !!mg.problem;
  const gone = mg.custom_path || '';
  const mergeBar = !mg.found ? `
    <div class="merge-bar is-miss">
      <div class="merge-bar-txt">
        <b>没检测到 Script Merger</b>
        ${gone ? `<span>之前指定过 <code>${esc(gone)}</code>，但现在这个文件找不到了 —— 是被删了还是盘符变了？</span>
          ` : `<span>两个模组改同一个脚本文件时，游戏只会加载其中一个，要用它合成一份。</span>`}
        <span class="merge-why">次世代版的新模组一般不用合并，装了很多旧模组或启动报脚本错误时才用它。</span>
      </div>
      <div class="merge-bar-acts">
        <button class="btn btn-sm" id="cfMergeNexus">去 Nexus 下载</button>
        <button class="btn btn-sm ${gone ? 'btn-primary' : ''}" id="cfMergeLocate">${gone ? '重新指定位置' : '我已装好，指定位置'}</button>
      </div>
    </div>` : (bad ? `
    <div class="merge-bar is-bad">
      <div class="merge-bar-txt">
        <b>合订本不生效</b>
        <span>${esc(mg.problem)}</span>
      </div>
      <button class="btn btn-sm btn-primary" id="cfMergeFix">去加载顺序修</button>
    </div>` : `
    <div class="merge-bar">
      <div class="merge-bar-txt">
        <b>Script Merger 已就位${mg.custom ? '（你指定的位置）' : ''}</b>
        <span>合并由它自己做，会直接改写 mods 目录。本程序只负责启动。
          ${mg.custom ? '<button class="link-btn" id="cfMergeReset">改回自动查找</button>' : ''}</span>
      </div>
      <button class="btn btn-sm ${needMerge ? 'btn-primary' : ''}" id="cfMergeOpen">打开 Script Merger</button>
    </div>`);

  openModal('冲突检查', `
    <div class="cf-stats">
      ${stat('脚本文件', st.script_files || 0)}
      ${stat('界面撞车', st.bundle_ui || 0, st.bundle_ui ? 'is-warn' : '')}
      ${stat('已合并', st.merged_groups || 0, st.merged_groups ? 'is-ok' : '')}
      ${stat('待合并', st.merge || 0, st.merge ? 'is-merge' : '')}
      ${stat('需要注意', st.warn || 0, st.warn ? 'is-warn' : '')}
    </div>
    ${mergeBar}
    <div class="cf-list">${rows}</div>
    <div class="set-actions" style="margin-top:12px">
      <button class="btn btn-sm" id="cfRecheck">重新检查</button>
      <span class="pv-foot" style="align-self:center">
        ${rep.checked_at ? '检查于 ' + esc(rep.checked_at.replace('T', ' ').slice(5, 16)) : ''}</span>
    </div>`, { wide: true });

  $('#cfRecheck').onclick = () => openConflictsDialog(true);

  // 装在别处的 Script Merger：让用户自己指一次，记住路径。
  // 这里必须走服务端的原生文件框（core/picker.py）—— 浏览器页面里没有
  // 拿完整本地路径的办法，`<input type=file>` 只给个假路径。
  const nx = $('#cfMergeNexus');
  if (nx) {
    nx.onclick = () => openNexus('https://www.nexusmods.com/witcher3/mods/13076');
  }
  const loc = $('#cfMergeLocate');
  if (loc) {
    loc.onclick = async () => {
      const old = loc.textContent;
      loc.disabled = true;
      loc.textContent = '选择中…';
      try {
        const r = await api('/api/merger/locate', {});
        if (r.cancelled) {
          loc.disabled = false;
          loc.textContent = old;
          return;
        }
        if (r.suspicious) {
          toast('这个文件名看着不像 Script Merger，装错了的话点「重新指定位置」再选一次', 7000);
        }
        openConflictsDialog(true);          // 带着新路径重新检查
      } catch (e) {
        toast(e.message, 6500);
        loc.disabled = false;
        loc.textContent = old;
      }
    };
  }
  const rst = $('#cfMergeReset');
  if (rst) {
    rst.onclick = async () => {
      try {
        await api('/api/merger/locate', { clear: true });
        openConflictsDialog(true);
      } catch (e) {
        toast(e.message, 5500);
      }
    };
  }
  if ($('#cfMergeFix')) {
    $('#cfMergeFix').onclick = () => {
      closeModal(true);
      if (typeof openOrderDialog === 'function') openOrderDialog();
    };
  }
  if ($('#cfMergeOpen')) {
    $('#cfMergeOpen').onclick = async () => {
      const btn = $('#cfMergeOpen');
      btn.disabled = true;
      try {
        await api('/api/merger/open', {});
        toast('Script Merger 已启动。合完切回来就行，这里会自动重新检查。', 5200);
      } catch (err) {
        toast(err.message, 6000);
      } finally {
        btn.disabled = false;
      }
    };
  }
  $$('#modalBody [data-jump]').forEach(b => {
    b.onclick = () => {
      closeModal(true);
      S.filter = { type: 'all', value: null };
      renderSidebar(); renderList();
      select(b.dataset.jump);
    };
  });

  CF_STATS = { merge: st.merge || 0, warn: st.warn || 0, merged: st.merged_groups || 0 };
  // 记下台账当前的样子，后面靠它判断「用户是不是刚在 Script Merger 里合完」
  api('/api/merger/state')
    .then(s => { CF_DIGEST = cfDigest(s); })
    .catch(() => { /* 没装 Script Merger 就不盯了，按钮那边本来也不给 */ });
  startMergerWatch();
}

$('#navConflicts').addEventListener('click', () => openConflictsDialog());

/* ---------- 拖拽：排序（列表视图） + 拖进分组 ---------- */
let dragKey = null;
const canReorder = () => S.view === 'list' && S.filter.type === 'all' && S.sort === 'priority';

document.addEventListener('dragstart', e => {
  const card = e.target.closest?.('.card');
  if (!card) return;
  // 列表视图里拖卡片只有一个用途：调加载顺序。条件不满足时直接说明白，
  // 别让它看起来像拖了没反应。
  if (S.view === 'list' && !canReorder()) {
    e.preventDefault();
    toast('当前不是「全部模组 + 按加载顺序」，拖不动。要调顺序点左侧「加载顺序」。', 4200);
    return;
  }
  // 已禁用的模组不占加载顺序（它压根不加载），列表里也是钉在最后的，
  // 所以不给拖。但要说清楚，不然看起来像拖坏了。
  if (S.view === 'list' && card.classList.contains('is-off')) {
    e.preventDefault();
    toast('已禁用的模组不占加载顺序，启用之后才需要排位置', 3600);
    return;
  }
  dragKey = card.dataset.key;
  card.classList.add('is-dragging');
  if (e.dataTransfer) e.dataTransfer.effectAllowed = 'move';
});
document.addEventListener('dragend', () => {
  $$('.card').forEach(c => c.classList.remove('is-dragging', 'is-dropzone'));
  $$('.nav-item').forEach(n => n.classList.remove('is-drop-target'));
  dragKey = null;
});
document.addEventListener('dragover', e => {
  if (!dragKey) return;
  const grp = e.target.closest?.('[data-drop="group"]');
  if (grp) {
    e.preventDefault();
    $$('.nav-item').forEach(n => n.classList.remove('is-drop-target'));
    grp.classList.add('is-drop-target');
    return;
  }
  if (!canReorder()) return;
  const card = e.target.closest?.('.card');
  if (!card || card.dataset.key === dragKey) return;
  e.preventDefault();
  $$('.card').forEach(c => c.classList.remove('is-dropzone'));
  card.classList.add('is-dropzone');
});

document.addEventListener('drop', async e => {
  if (!dragKey) return;
  const moved = dragKey;

  const grp = e.target.closest?.('[data-drop="group"]');
  if (grp) {
    e.preventDefault();
    dragKey = null;
    await addToGroup(grp.dataset.v, moved);
    return;
  }

  if (!canReorder()) return;
  const card = e.target.closest?.('.card');
  if (!card || card.dataset.key === dragKey) return;
  e.preventDefault();

  const order = visibleMods().map(m => m.key);
  const from = order.indexOf(moved);
  if (from < 0) return;
  order.splice(from, 1);
  order.splice(order.indexOf(card.dataset.key), 0, moved);
  dragKey = null;

  const byKey = new Map(S.mods.map(m => [m.key, m]));
  // 只有「已启用」的模组真的在加载顺序里。已禁用的那几个钉在列表底部，
  // 它们的 priority 一个都不许动 —— 动了就等于「禁用一次，位置丢了」。
  // 拖到某个已禁用的卡片上，等于把这张放到已启用那一段的最后。
  const liveKeys = order.filter(k => byKey.get(k)?.enabled);
  if (!liveKeys.includes(moved)) {
    toast('已禁用的模组不占加载顺序，启用之后才需要排位置', 3600);
    return;
  }
  const prios = reorderSlots(liveKeys, byKey);
  liveKeys.forEach((k, i) => { byKey.get(k).priority = prios[i]; });
  renderList();

  try {
    await api('/api/reorder', { keys: liveKeys, prios });
    toast('加载顺序已保存', 3200, { label: '撤销', fn: undoNow });
    refreshJournal();
  } catch (err) { toast('排序失败：' + err.message, 4000); load(); }
});

/* 重排时发什么号：只发这批模组**原来就占着的**那些号，从小到大按新顺序配上。
   为什么不干脆 10、20、30 全重排？因为没进这次重排的模组（已禁用的）号得原样
   留着 —— 用它们原来的号既不会撞上、也不用凭空造新号，改完还是原来那批数字。
   用 Set 去重：万一原本就有两个模组共用一个优先级（冲突面板会报这一条），
   重排正好把它们分开，而不是把重复的号再发一遍被服务端拒掉。 */
function reorderSlots(keys, byKey) {
  const used = [...new Set(keys
    .map(k => byKey.get(k)?.priority)
    .filter(v => Number.isFinite(v)))].sort((a, b) => a - b);
  const out = used.slice(0, keys.length);
  let next = out.length ? out[out.length - 1] : 0;
  while (out.length < keys.length) { next += 10; out.push(next); }
  return out;
}

/* ---------- 撤销与日志 ---------- */
$('#btnUndo').addEventListener('click', () => { undoNow(); });

$('#stRecent').addEventListener('click', () => { openLogDialog(); });

async function openLogDialog() {
  await refreshJournal();
  const html = JOURNAL.length
    ? JOURNAL.map(i => `<div class="log-row"><time>${esc(i.time)}</time><span>${esc(i.label)}</span></div>`).join('')
    : `<div class="muted">还没有操作记录。启用、禁用、调顺序都会记在这里，可以随时撤销。</div>`;
  openModal('操作日志', html);
}

$('#btnLog').addEventListener('click', () => { openLogDialog(); });

$('#modalClose').addEventListener('click', () => { closeModal(); });
$('#modalMask').addEventListener('click', e => {
  if (e.target.id === 'modalMask') closeModal();
});

async function toggleMod(key) {
  const m = S.mods.find(x => x.key === key);
  if (!m) return;
  // 先动界面，失败再回滚，避免等待感
  m.enabled = !m.enabled;
  renderList();
  if (S.selected === key) renderDetails(m);
  try {
    await api('/api/mods/toggle', { key, enabled: m.enabled });
    // 提示条里直接带撤销：让「这一步能退」在发生的那一刻被看见
    toast(`${m.name} 已${m.enabled ? '启用' : '禁用'}`, 3200,
      { label: '撤销', fn: undoNow });
    renderSidebar();
    renderStatus();
  } catch (err) {
    m.enabled = !m.enabled;
    renderList();
    if (S.selected === key) renderDetails(m);
    toast('切换失败：' + err.message, 4000);
  }
}

$('#q').addEventListener('input', e => { S.q = e.target.value; renderList(); });
$('#sortBy').addEventListener('change', e => {
  S.sort = e.target.value;
  localStorage.setItem('waass.sort', S.sort);
  renderList();
  // 只有「列表视图 + 全部模组 + 按加载顺序」时才拖得动。以前另外两种情况
  // 是静默拒绝，用户拖半天以为软件坏了。
  if (S.sort !== 'priority' && S.view === 'list') {
    toast('现在按别的规则排显示顺序，拖不动。要改游戏加载顺序，点左侧「加载顺序」。', 4200);
  }
});

/* 空列表里的出口。前两个只在「根本没找到游戏」时出现 */
$('#emptyPickGame').addEventListener('click', async () => {
  const picked = await api('/api/pick-file', { kind: 'dir' }).catch(e => ({ error: e.message }));
  // 原生选目录框可能被系统策略挡住，退回手输（站内输入框，不用 prompt()）
  const p = picked.path || await askText({
    title: '游戏目录在哪',
    label: '选到有 bin 文件夹的那一层，不是 bin 里面',
    value: S.data?.config?.game_root || '',
    placeholder: 'D:\\game\\steam\\steamapps\\common\\The Witcher 3',
  });
  if (!p) return;
  try {
    await api('/api/settings', { action: 'set_game_root', path: p });
    await load();
    toast('游戏目录已设置');
  } catch (e) { toast('设置失败：' + e.message, 4000); }
});
$('#emptyRedetect').addEventListener('click', async () => {
  try {
    const r = await api('/api/config', { redetect: true });
    await load();
    toast(r.game_root ? `找到了：${r.game_root}` : '还是没找到，手动选一下目录吧', 5000);
  } catch (e) { toast('查找失败：' + e.message, 4000); }
});
$('#emptyClear').addEventListener('click', () => {
  S.q = ''; $('#q').value = ''; renderList(); $('#q').focus();
});
$('#emptyAll').addEventListener('click', () => {
  S.q = ''; $('#q').value = '';
  S.filter = { type: 'all', value: null };
  renderSidebar(); renderList();
});
$('#emptyNexus').addEventListener('click', () => {
  const q = S.q.trim();
  if (q) openNexus(`https://www.nexusmods.com/witcher3/search/?gsearch=${encodeURIComponent(q)}&gsearchtype=mods`);
});
$('#btnRefresh').addEventListener('click', () => load(true));

// 顶栏「N 个可更新」：左边看清单，右边 × 忽略这一批（两个独立的可点区域）
$('#btnUpdates').addEventListener('click', () => openTrackDialog('new'));
$('#btnUpdatesX').addEventListener('click', () => ignoreUpdates());
$('#btnNexus').addEventListener('click', () => {
  // 交给服务端用系统默认浏览器打开（页面里开新窗口会被 Edge 的 --app 接住）
  openNexus('https://www.nexusmods.com/games/witcher3');
});
$('#btnTheme').addEventListener('click', () => {
  // 三态循环：跟随系统 → 亮 → 暗 → 跟随系统…。点过之后就变成显式选择，
  // 不再跟系统走；想回到跟随系统再点两下。
  const i = THEME_ORDER.indexOf(S.theme);
  S.theme = THEME_ORDER[(i < 0 ? 0 : i + 1) % THEME_ORDER.length];
  localStorage.setItem('waass.theme', S.theme);
  applyTheme();
  toast(`外观：${THEME_LABEL[S.theme]}`, 1800);
});

// 系统在设置里换明暗时，界面跟着变（只在「跟随系统」时生效）
if (darkMQ && darkMQ.addEventListener) {
  darkMQ.addEventListener('change', () => { if (S.theme === 'auto') applyTheme(); });
}

$('#btnPickGame').addEventListener('click', () => {
  if (typeof openSettingsDialog === 'function') openSettingsDialog();
  else {
    askText({
      title: '设置游戏目录',
      label: '粘贴游戏根目录（含 bin 文件夹的那个）',
      value: S.data?.config?.game_root || '',
      placeholder: 'D:\\game\\steam\\steamapps\\common\\The Witcher 3',
    }).then(p => {
      if (!p) return;
      api('/api/settings', { action: 'set_game_root', path: p })
        .then(() => load()).then(() => toast('游戏目录已更新'))
        .catch(e => toast('设置失败：' + e.message, 4000));
    });
  }
});
$('#btnSettings').addEventListener('click', () => {
  if (typeof openSettingsDialog === 'function') openSettingsDialog();
});

document.addEventListener('keydown', e => {
  if (e.target.id === 'tagInput' && e.key === 'Enter') {
    const v = e.target.value.trim();
    if (!v || !S.selected) return;
    const key = S.selected;
    const list = tagsOf(key);
    if (!list.includes(v)) saveTags(key, [...list, v]);
    e.target.value = '';
    return;
  }
  if (e.key === 'Escape') {
    if (!$('#modalMask').hidden) { closeModal(); return; }
    $('#q').blur();
  }
});

document.addEventListener('change', async e => {
  if (e.target.id !== 'groupSel' || !S.selected) return;
  const gid = e.target.value;
  const key = S.selected;
  try {
    if (gid) {
      await api('/api/library', { action: 'set_member', id: gid, key, on: true });
      const g = (S.data?.library?.groups || []).find(x => x.id === gid);
      toast(`已归入「${g ? g.name : '分组'}」`);
    } else {
      // 找到它当前所在的分组再移出
      const cur = S.data?.library?.by_key?.[key]?.group;
      if (cur) await api('/api/library', { action: 'set_member', id: cur.id, key, on: false });
      toast('已移出分组');
    }
    S.data.library = await api('/api/library');
    renderSidebar();
    const m = S.mods.find(x => x.key === key);
    if (m) renderDetails(m);
  } catch (err) { toast('操作失败：' + err.message, 4000); }
});

/* ---------- 启动 ---------- */
const _qs = new URLSearchParams(location.search);
if (_qs.get('view')) S.view = _qs.get('view');
if (_qs.get('theme')) {
  // 截图/排错用：?theme=dark 或 ?theme=auto。只影响这一次，不写 localStorage。
  const t = _qs.get('theme');
  S.theme = THEME_ORDER.includes(t) ? t : S.theme;
}
// 直接落到某个状态分类（截图/排错用，平时不用管）
if (_qs.get('filter')) S.filter = { type: _qs.get('filter'), value: null };
if (_qs.get('q')) { S.q = _qs.get('q'); $('#q').value = S.q; }

applyTheme();
$('#sortBy').value = S.sort;
showDetailsEmpty();

/* 关窗时说一声，后台进程好在几秒后自己退出。
   用 sendBeacon：它在页面已经开始销毁时也能发出去，普通的 fetch 会被掐断。
   刷新也会触发这里，但新页面会立刻请求页面本身，把倒计时取消掉。 */
window.addEventListener('pagehide', () => {
  try {
    navigator.sendBeacon(`/api/bye?t=${encodeURIComponent(TOKEN)}`, '');
  } catch { /* 老浏览器就算了，进程会留着，下次启动会提示已在运行 */ }
});

/* 每 15 秒跟后台打一声招呼。
   后台靠「窗口不见了 + 有一阵子没人说话」判断该不该退出 —— 只看 pagehide
   那一枪不够可靠：关窗瞬间这一枪有机会发不出去，进程就留在后台了（1.2.6 实测
   关窗后活了四十多分钟）。这里只请求 /api/health，很小。 */
setInterval(() => {
  fetch('/api/health', { cache: 'no-store' }).catch(() => {});
}, 15000);

load().then(() => {
  const sel = _qs.get('select');
  // 合订本不在 S.mods 里（它单独走 scan.merged），要单独放行
  if (sel && (S.mods.some(m => m.key === sel) || S.data?.scan?.merged?.name === sel)) select(sel);
  if (_qs.get('dialog') === 'conflicts') openConflictsDialog();
  // 截图用：?dialog=settings / localize / order / track 直接开对应弹窗
  if (_qs.get('dialog') === 'settings') openSettingsDialog();
  if (_qs.get('dialog') === 'localize') openLocalizeDialog();
  if (_qs.get('dialog') === 'order') openOrderDialog();
  if (_qs.get('dialog') === 'track') openTrackDialog(_qs.get('trk') || 'all');
  if (_qs.get('dialog') === 'guide') openGuide();
  // 截图/排错用：?pick=1 直接进批量模式，?picked=a,b 顺带预选几个
  if (_qs.get('pick') === '1') {
    setPicking(true);
    (_qs.get('picked') || '').split(',').filter(Boolean)
      .forEach(k => S.picked.add(k));
    renderList();
    renderPickBar();
  }
  autoSync();
});

/* 启动后悄悄同步一次 Nexus：补封面、查有没有新版本。
   刻意不 await：界面这时已经能用了，同步只是为了过后把角标和提示条点亮。
   ?nosync=1 可以关掉（截图和排错时不想等网络就带上）。
   网络不通、没配 ID 都不会打扰用户 —— 静默失败，下次启动再试。 */
function autoSync() {
  if (_qs.get('nosync') === '1') return;
  const needId = S.mods.filter(m => !m.nexus_id && !isMergedMod(m));
  if (S.mods.some(m => m.nexus_id)) {
    api('/api/nexus/autosync', {}).then(r => {
      if (r && r.ui && S.data) {
        S.data.ui = r.ui;
        renderSyncBar();
        renderStatus();                           // 顶部「N 个可更新」角标
      }
      if (r && r.covers) {
        renderList();                               // 新封面已经落地，重画一遍卡片
      }
    }).catch(() => { /* 静默：同步失败不该在界面上留下任何痕迹 */ });
  }
  /* 认不出 Nexus ID 的那几个：把候选先备好（服务端有三天缓存）。
     1.2.6 之前这里是一句 `if (!S.mods.some(m => m.nexus_id)) return;` ——
     新用户 ID 一个都没有，于是请求都不发，界面上一片安静，看起来就是
     「自动同步根本没实现」。现在至少让他知道有几个没对上号。 */
  if (needId.length) {
    api('/api/nexus/guess', {}).then(r => {
      if (r && r.ui && S.data) { S.data.ui = r.ui; renderSyncBar(); }
    }).catch(() => { /* 联网失败就算了，下次开程序再试 */ });
  }
}

/* 合订本（Script Merger 产出的 mod0000_MergedFiles）本来就不在 Nexus 上，
   别把它算进「没认出 ID」里，否则永远有一条消不掉的提示。 */
function isMergedMod(m) {
  return !!m.is_merged || /^mod0000_/i.test(m.folder || m.key || '');
}
