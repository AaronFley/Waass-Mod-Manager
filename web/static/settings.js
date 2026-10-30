/* 设置面板：游戏目录、下载目录、Nexus 资料、存储与清理、关于
   依赖 app.js 的 api / openModal / esc / toast / load / S */

let SET = { config: {}, downloads: { dirs: [] } };
let ABOUT = null;
let STG = null;

const DIR_KIND_LABEL = { system: '系统下载目录', custom: '已添加', suggested: '常用位置' };
const RISK_LABEL = { safe: '随便删', caution: '删了少点方便', danger: '删了找不回' };

/* 使用说明：把 static/guide.html 读进来塞进弹窗。
   不单开浏览器窗口（--app 窗口没有地址栏，用户回不来），也不把九节正文
   硬塞进 JS 字符串里 —— 那样改文案要动代码。读不到就只少这一页，
   其余功能一概不受影响。 */
async function openGuide() {
  openModal('使用说明', '<div class="upd-loading">正在读取…</div>', { wide: true });
  try {
    const r = await fetch('/static/guide.html', { cache: 'no-store' });
    if (!r.ok) throw new Error('读不到说明书（' + r.status + '）');
    $('#modalBody').innerHTML = await r.text();
    $('#modalBody').scrollTop = 0;
  } catch (e) {
    $('#modalBody').innerHTML =
      `<div class="pkg-hint">${esc(e.message)}。这不影响其它功能。</div>`;
  }
}

async function openSettingsDialog() {
  // 有可能是直接带着 ?dialog=settings 打开，主数据还没回来，先等它
  if (!S.data) {
    try { await load(); } catch { /* 下面会显示未设置 */ }
  }
  const [r, a, s, ns] = await Promise.all([
    api('/api/settings'),
    api('/api/about').catch(() => null),
    api('/api/storage').catch(() => null),
    api('/api/nexus/status').catch(() => null),
  ]);
  SET = { config: r.config || {}, downloads: r.downloads || { dirs: [] } };
  ABOUT = a || {};
  STG = s;
  if (ns) ABOUT.nexus = ns;

  const cfg = SET.config;
  const game = cfg.game_root || ABOUT?.game_root || S.data?.layout?.root
    || (S.data?.game_found ? '（自动识别）' : '未设置');
  const dirs = SET.downloads.dirs || [];

  const dirRows = dirs.map(d => `
    <div class="set-row">
      <div class="set-main">
        <div class="set-name">
          ${esc(d.name)}
          <span class="set-tag ${d.kind === 'system' ? 'is-system' : ''}">${DIR_KIND_LABEL[d.kind] || ''}</span>
          ${d.exists ? '' : '<span class="set-tag is-missing">目录不见了</span>'}
        </div>
        <div class="set-path">${esc(d.path)}</div>
      </div>
      <span class="set-count">${d.archives || 0} 个包</span>
      <button class="btn btn-sm" data-rmdir="${esc(d.path)}">移除</button>
    </div>`).join('');

  const on = (k, dflt) => (cfg[k] === undefined ? dflt : !!cfg[k]);
  const ver = ABOUT ? ABOUT.version : (S.data?.app?.version || '—');

  const stgRows = (STG?.groups || []).map(g => `
    <div class="stg-row">
      <div class="stg-main">
        <div class="stg-name">${esc(g.title)}
          <span class="stg-risk is-${g.risk}">${RISK_LABEL[g.risk] || ''}</span>
        </div>
        <div class="stg-desc">${esc(g.desc)}</div>
      </div>
      <span class="stg-size">${fmtSize(g.bytes)}<em>${g.files} 个文件</em></span>
      ${g.actionable === false ? '' : `<button class="btn btn-sm" data-stg="${g.id}">清理</button>`}
    </div>`).join('');

  openModal('设置', `
    <div class="set">

      <div>
        <div class="set-sec-title">游戏目录</div>
        <div class="set-row">
          <div class="set-main">
            <div class="set-name">巫师3 重制版</div>
            <div class="set-path">${esc(game)}</div>
          </div>
          <button class="btn btn-sm" id="setGameDir">更改</button>
        </div>
      </div>

      <div>
        <div class="set-sec-title">下载目录 · 从这些地方找 mod 包</div>
        <div class="pkg-hint" style="margin-top:-2px">
          你平时在网页上下载的 mod 会存在这些目录里。装的时候不用再翻文件夹，
          这里列出来的包可以直接点着装。默认已经找到系统下载目录。
        </div>
        <div id="setDirs">${dirRows || '<div class="pkg-hint">还没有任何下载目录。点下面「添加目录」。</div>'}</div>
        <div class="set-actions" style="margin-top:8px">
          <button class="btn btn-sm btn-primary" id="setAddDir">添加目录</button>
          <button class="btn btn-sm" id="setPickDir">浏览选择…</button>
          <button class="btn btn-sm btn-ghost" id="setResetDir">恢复默认</button>
        </div>
        <div class="inst-hint" style="margin-top:6px" id="setAddManualWrap">
          也可以直接粘贴路径：<input id="setAddManual" class="inst-input" style="height:28px;margin-top:5px"
            placeholder="例如 D:\\download\\nexusmods">
        </div>
      </div>

      <div>
        <div class="set-sec-title">Nexus 资料</div>
        <label class="set-check">
          <input type="checkbox" id="optNexusMeta" ${on('nexus_auto_meta', true) ? 'checked' : ''}>
          <div>自动到 Nexus 查作者、封面、最新版本
            <small>从文件名能认出 Nexus ID 就查得到。只读公开信息，不登录、不要 Key、不下载 mod 文件；
            每个 mod 的资料存 7 天，过期才重查。关掉之后界面照样能用，只是版本和封面留空。</small></div>
        </label>
        <label class="set-check">
          <input type="checkbox" id="optNexusCover" ${on('nexus_auto_cover', true) ? 'checked' : ''}>
          <div>顺便下载封面图<small>每张约 20~60 KB（只取缩略图）。关掉只省一点空间，卡片会用纯色底。</small></div>
        </label>
        <div class="set-fixed">
          已经查到 <b>${ABOUT?.nexus?.cached ?? 0}</b> 个模组的资料。
          ${ABOUT?.nexus?.last_ok ? `上次成功：${esc(ABOUT.nexus.last_ok.replace('T', ' '))}` : ''}
          ${ABOUT?.nexus?.last_error ? `<br><span class="muted">上次出错：${esc(ABOUT.nexus.last_error)}</span>` : ''}
        </div>
      </div>

      <div>
        <div class="set-sec-title">存储与清理</div>
        <div class="stg-head">
          <div>
            <div class="stg-total">${fmtSize(STG?.total_bytes || 0)}</div>
            <div class="stg-sub">数据目录总占用 · ${STG?.total_files || 0} 个文件</div>
          </div>
          <button class="btn btn-sm btn-primary" id="stgCleanSafe" ${(STG?.safe_bytes || 0) > 0 ? '' : 'disabled'}>
            ${(STG?.safe_bytes || 0) > 0 ? '清理可再生的（' + fmtSize(STG.safe_bytes) + '）' : '已经很干净'}
          </button>
        </div>
        <div class="set-path" title="${esc(STG?.data_dir || '')}">${esc(STG?.data_dir || '')}</div>
        <div class="stg-list">${stgRows || '<div class="pkg-hint">现在没有可清理的东西，很干净。</div>'}</div>
        <div class="stg-core">
          核心数据 ${fmtSize(STG?.core_bytes || 0)}：配置、Nexus 台账、分组、安装档案、存档备份、汉化原件。
          这些是程序的大脑，任何清理都不会碰。
        </div>
      </div>

      <div>
        <div class="set-sec-title">帮助</div>
        <div class="set-row">
          <div class="set-main">
            <div class="set-name">使用说明</div>
            <div class="set-note">每一项功能怎么用、界面上的标记是什么意思。第一次用建议先扫一遍。</div>
          </div>
          <button class="btn btn-sm btn-primary" id="setGuide">打开</button>
        </div>
      </div>

      <div>
        <div class="set-sec-title">关于</div>
        <div class="about-box">
          <div class="about-head">
            <img class="brand-mark" src="/static/icon-128.png" alt="" width="34" height="34">
            <div>
              <div class="about-name">Waass Mod Manager</div>
              <div class="about-ver">版本 ${esc(ver)}${ABOUT?.date ? ' · ' + esc(ABOUT.date) : ''}${ABOUT?.frozen ? ' · 已打包版' : ' · 源码版'}</div>
            </div>
          </div>
          <div class="about-rows">
            <div><span>许可证</span><b>${esc(ABOUT?.license || 'MIT')}</b></div>
            <div><span>模组 / 已启用</span><b>${ABOUT?.counts?.mods ?? S.mods.length} / ${ABOUT?.counts?.enabled ?? '—'}</b></div>
            <div><span>安装档案</span><b>${ABOUT?.counts?.packages ?? 0} 个包</b></div>
            <div><span>运行环境</span><b>Python ${esc(ABOUT?.python || '')} · ${esc(ABOUT?.os || '')}</b></div>
          </div>
          <div class="about-path" title="${esc(ABOUT?.data_dir || '')}">数据目录　${esc(ABOUT?.data_dir || '')}</div>
          <div class="set-actions" style="margin-top:9px">
            <button class="btn btn-sm" id="setOpenData">打开数据目录</button>
            <button class="btn btn-sm" id="setCheckUpd">检查程序更新</button>
          </div>
          <div class="about-note" id="aboutUpd">
            这个程序只管自己的文件，从不改写你没让它改的游戏内容。
          </div>
        </div>
      </div>

    </div>`);

  /* ---- 帮助 ---- */
  $('#setGuide').onclick = () => openGuide();

  /* ---- 游戏目录 ---- */
  $('#setGameDir').onclick = async () => {
    const picked = await api('/api/pick-file', { kind: 'dir' }).catch(e => ({ error: e.message }));
    // 原生选目录框被系统策略挡住时才退回手输。手输用站内的输入框，
    // 不用浏览器的 prompt()（在 --app 窗口里它拿不到输入法候选框）。
    const p = picked.path || await askText({
      title: '设置游戏目录',
      label: '粘贴游戏根目录（含 bin 文件夹的那个）',
      value: cfg.game_root || '',
      placeholder: 'D:\\game\\steam\\steamapps\\common\\The Witcher 3',
    });
    if (!p) return;
    try {
      await api('/api/settings', { action: 'set_game_root', path: p });
      toast('游戏目录已更新');
      await load();
      openSettingsDialog();
    } catch (e) { toast('设置失败：' + e.message, 4000); }
  };

  /* ---- 下载目录 ---- */
  const addDir = async (path) => {
    if (!path) return;
    try {
      await api('/api/settings', { action: 'add_dir', path });
      toast('已添加');
      refreshMainSoon();
      openSettingsDialog();
    } catch (e) { toast('添加失败：' + e.message, 4000); }
  };

  $('#setAddDir').onclick = () => addDir($('#setAddManual').value.trim());
  $('#setPickDir').onclick = async () => {
    const r = await api('/api/pick-file', { kind: 'dir' }).catch(e => ({ error: e.message }));
    if (r.path) return addDir(r.path);
    if (r.error) toast(r.error, 3500);
  };
  $('#setResetDir').onclick = async () => {
    await api('/api/settings', { action: 'reset_dirs' });
    toast('已恢复默认目录');
    refreshMainSoon();
    openSettingsDialog();
  };
  $('#setAddManual').addEventListener('keydown', e => {
    if (e.key === 'Enter') addDir(e.target.value.trim());
  });

  $$('#setDirs [data-rmdir]').forEach(b => {
    b.onclick = async () => {
      const p = b.dataset.rmdir;
      if (!confirm(`不再扫描这个目录？\n${p}\n\n不会删除里面的任何文件，只是不在这里列出了。`)) return;
      await api('/api/settings', { action: 'remove_dir', path: p });
      toast('已移除');
      refreshMainSoon();
      openSettingsDialog();
    };
  });

  /* ---- Nexus 资料 ---- */
  const saveNexusOpt = async (extra = {}) => {
    await api('/api/settings', {
      action: 'set_option',
      nexus_auto_meta: $('#optNexusMeta').checked,
      nexus_auto_cover: $('#optNexusCover').checked,
      ...extra,
    });
    toast('已保存');
  };
  $('#optNexusMeta').onchange = () => saveNexusOpt();
  $('#optNexusCover').onchange = () => saveNexusOpt();

  /* ---- 存储与清理 ---- */
  const doClean = async (ids, safeOnly) => {
    try {
      const r = await api('/api/storage/clean', { ids: ids || [], safe_only: !!safeOnly });
      toast(r.freed ? `释放了 ${fmtSize(r.freed)}` : '没有可释放的空间（文件可能正被占用）');
      openSettingsDialog();
    } catch (e) { toast('清理失败：' + e.message, 4000); }
  };

  const safeBtn = $('#stgCleanSafe');
  if (safeBtn) safeBtn.onclick = () => doClean([], true);

  $$('[data-stg]').forEach(b => {
    b.onclick = () => {
      const g = (STG?.groups || []).find(x => x.id === b.dataset.stg);
      if (!g) return;
      const tip = g.risk === 'danger'
        ? `永久删除「${g.title}」里的所有文件？\n\n${g.desc}\n\n这一步不可撤销，删了就找不回来了。`
        : `清理「${g.title}」？\n\n${g.desc}`;
      if (!confirm(tip)) return;
      doClean([g.id], false);
    };
  });

  /* ---- 关于 ---- */
  $('#setOpenData').onclick = async () => {
    try { await api('/api/open-folder', { key: '@data' }); }
    catch (e) { toast('打不开：' + e.message, 3500); }
  };
  $('#setCheckUpd').onclick = async () => {
    const box = $('#aboutUpd');
    const btn = $('#setCheckUpd');
    btn.disabled = true;
    box.textContent = '正在检查…';
    try {
      const r = await api('/api/app/check', {});
      box.className = 'about-note' + (r.newer ? ' is-new' : '');
      box.innerHTML = esc(r.message || '') +
        (r.url && r.newer ? ` · <a href="#" id="aboutGo">去打开发布页</a>` : '');
      const go = $('#aboutGo');
      if (go) go.onclick = (e) => { e.preventDefault(); window.open(r.url, '_blank', 'noopener'); };
    } catch (e) {
      box.textContent = '检查失败：' + e.message;
    } finally {
      btn.disabled = false;
    }
  };
}

/* 设置改完要让主界面重新拉一次下载目录信息 */
function refreshMainSoon() {
  setTimeout(() => load(), 150);
}

document.addEventListener('DOMContentLoaded', () => {
  const n = $('#navSettings');
  if (n) n.onclick = openSettingsDialog;
  const params = new URLSearchParams(location.search);
  if (params.get('dialog') === 'settings') openSettingsDialog();
});
