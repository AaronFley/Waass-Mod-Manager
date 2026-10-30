/* 设置面板：游戏目录、下载目录、Nexus 资料、存储与清理、关于
   依赖 app.js 的 api / openModal / esc / toast / load / S */

let SET = { config: {}, downloads: { dirs: [] } };
let ABOUT = null;
let STG = null;

const DIR_KIND_LABEL = () => ({ system: T('set.dir_kind_system'), custom: T('set.dir_kind_custom'), suggested: T('set.dir_kind_suggested') });
const RISK_LABEL = () => ({ safe: T('set.risk_safe'), caution: T('set.risk_caution'), danger: T('set.risk_danger') });

/* 使用说明：把 static/guide.html 读进来塞进弹窗。
   不单开浏览器窗口（--app 窗口没有地址栏，用户回不来），也不把九节正文
   硬塞进 JS 字符串里 —— 那样改文案要动代码。读不到就只少这一页，
   其余功能一概不受影响。 */
async function openGuide() {
  openModal(T('set.guide_title'), `<div class="upd-loading">${T('common.loading')}</div>`, { wide: true });
  try {
    const r = await fetch(`/static/${I18N_LANG === 'zh' ? 'guide.html' : 'guide.en.html'}`, { cache: 'no-store' });
    if (!r.ok) throw new Error(T('set.guide_load_fail', { code: r.status }));
    $('#modalBody').innerHTML = await r.text();
    $('#modalBody').scrollTop = 0;
  } catch (e) {
    $('#modalBody').innerHTML =
      `<div class="pkg-hint">${T('set.guide_fail_hint', { msg: esc(e.message) })}</div>`;
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
    || (S.data?.game_found ? T('set.auto_detected') : T('ui.unset'));
  const dirs = SET.downloads.dirs || [];

  const dirRows = dirs.map(d => `
    <div class="set-row">
      <div class="set-main">
        <div class="set-name">
          ${esc(d.name)}
          <span class="set-tag ${d.kind === 'system' ? 'is-system' : ''}">${DIR_KIND_LABEL()[d.kind] || ''}</span>
          ${d.exists ? '' : `<span class="set-tag is-missing">${T('set.dir_missing')}</span>`}
        </div>
        <div class="set-path">${esc(d.path)}</div>
      </div>
      <span class="set-count">${TN('set.archives_one', 'set.archives_many', d.archives || 0)}</span>
      <button class="btn btn-sm" data-rmdir="${esc(d.path)}">${T('set.remove')}</button>
    </div>`).join('');

  const on = (k, dflt) => (cfg[k] === undefined ? dflt : !!cfg[k]);
  const ver = ABOUT ? ABOUT.version : (S.data?.app?.version || '—');

  const stgRows = (STG?.groups || []).map(g => `
    <div class="stg-row">
      <div class="stg-main">
        <div class="stg-name">${esc(g.title)}
          <span class="stg-risk is-${g.risk}">${RISK_LABEL()[g.risk] || ''}</span>
        </div>
        <div class="stg-desc">${esc(g.desc)}</div>
      </div>
      <span class="stg-size">${fmtSize(g.bytes)}<em>${TN('set.files_one', 'set.files_many', g.files)}</em></span>
      ${g.actionable === false ? '' : `<button class="btn btn-sm" data-stg="${g.id}">${T('set.clean')}</button>`}
    </div>`).join('');

  openModal(T('ui.settings'), `
    <div class="set">

      <div>
        <div class="set-sec-title">${T('ui.game_dir')}</div>
        <div class="set-row">
          <div class="set-main">
            <div class="set-name">${T('set.game_name')}</div>
            <div class="set-path">${esc(game)}</div>
          </div>
          <button class="btn btn-sm" id="setGameDir">${T('ui.change')}</button>
        </div>
      </div>

      <div>
        <div class="set-sec-title">${T('set.downloads_title')}</div>
        <div class="pkg-hint" style="margin-top:-2px">
          ${T('set.downloads_hint')}
        </div>
        <div id="setDirs">${dirRows || `<div class="pkg-hint">${T('set.dirs_empty')}</div>`}</div>
        <div class="set-actions" style="margin-top:8px">
          <button class="btn btn-sm btn-primary" id="setAddDir">${T('set.add_dir')}</button>
          <button class="btn btn-sm" id="setPickDir">${T('set.browse')}</button>
          <button class="btn btn-sm btn-ghost" id="setResetDir">${T('set.restore_defaults')}</button>
        </div>
        <div class="inst-hint" style="margin-top:6px" id="setAddManualWrap">
          ${T('set.paste_path')}<input id="setAddManual" class="inst-input" style="height:28px;margin-top:5px"
            placeholder="${T('set.paste_path_ph')}">
        </div>
      </div>

      <div>
        <div class="set-sec-title">${T('set.nexus_section')}</div>
        <label class="set-check">
          <input type="checkbox" id="optNexusMeta" ${on('nexus_auto_meta', true) ? 'checked' : ''}>
          <div>${T('set.nexus_auto_meta_label')}
            <small>${T('set.nexus_auto_meta_hint')}</small></div>
        </label>
        <label class="set-check">
          <input type="checkbox" id="optNexusCover" ${on('nexus_auto_cover', true) ? 'checked' : ''}>
          <div>${T('set.nexus_cover_label')}<small>${T('set.nexus_cover_hint')}</small></div>
        </label>
        <div class="set-fixed">
          ${T('set.nexus_cached', { n: ABOUT?.nexus?.cached ?? 0 })}
          ${ABOUT?.nexus?.last_ok ? T('set.nexus_last_ok', { time: esc(ABOUT.nexus.last_ok.replace('T', ' ')) }) : ''}
          ${ABOUT?.nexus?.last_error ? `<br><span class="muted">${T('set.nexus_last_error', { msg: esc(ABOUT.nexus.last_error) })}</span>` : ''}
        </div>
      </div>

      <div>
        <div class="set-sec-title">${T('set.storage_section')}</div>
        <div class="stg-head">
          <div>
            <div class="stg-total">${fmtSize(STG?.total_bytes || 0)}</div>
            <div class="stg-sub">${TN('set.storage_total_one', 'set.storage_total_many', STG?.total_files || 0)}</div>
          </div>
          <button class="btn btn-sm btn-primary" id="stgCleanSafe" ${(STG?.safe_bytes || 0) > 0 ? '' : 'disabled'}>
            ${(STG?.safe_bytes || 0) > 0 ? T('set.clean_safe_btn', { size: fmtSize(STG.safe_bytes) }) : T('set.already_clean')}
          </button>
        </div>
        <div class="set-path" title="${esc(STG?.data_dir || '')}">${esc(STG?.data_dir || '')}</div>
        <div class="stg-list">${stgRows || `<div class="pkg-hint">${T('set.storage_empty')}</div>`}</div>
        <div class="stg-core">
          ${T('set.storage_core', { size: fmtSize(STG?.core_bytes || 0) })}
        </div>
      </div>

      <div>
        <div class="set-sec-title">${T('set.help_section')}</div>
        <div class="set-row">
          <div class="set-main">
            <div class="set-name">${T('set.guide_title')}</div>
            <div class="set-note">${T('set.guide_note')}</div>
          </div>
          <button class="btn btn-sm btn-primary" id="setGuide">${T('set.open')}</button>
        </div>
      </div>

      <div>
        <div class="set-sec-title">${T('set.about_section')}</div>
        <div class="about-box">
          <div class="about-head">
            <img class="brand-mark" src="/static/icon-128.png" alt="" width="34" height="34">
            <div>
              <div class="about-name">Waass Mod Manager</div>
              <div class="about-ver">${T('set.about_version', { ver: esc(ver) })}${ABOUT?.date ? ' · ' + esc(ABOUT.date) : ''}${ABOUT?.frozen ? ' · ' + T('set.build_packaged') : ' · ' + T('set.build_source')}</div>
            </div>
          </div>
          <div class="about-rows">
            <div><span>${T('set.license')}</span><b>${esc(ABOUT?.license || 'MIT')}</b></div>
            <div><span>${T('set.mods_enabled')}</span><b>${ABOUT?.counts?.mods ?? S.mods.length} / ${ABOUT?.counts?.enabled ?? '—'}</b></div>
            <div><span>${T('set.install_records')}</span><b>${TN('set.archives_one', 'set.archives_many', ABOUT?.counts?.packages ?? 0)}</b></div>
            <div><span>${T('set.runtime')}</span><b>Python ${esc(ABOUT?.python || '')} · ${esc(ABOUT?.os || '')}</b></div>
            <!-- 数据格式版本（1.2.9）。出问题时这两个数字是唯一能问清楚的线索：
                 「你的数据是第几版、程序认到第几版」。 -->
            <div><span>${T('set.data_schema')}</span><b>${T('set.data_schema_val', {
              data: esc(String(ABOUT?.compat?.data_schema ?? '—')),
              app: esc(String(ABOUT?.compat?.app_schema ?? '—')) })}</b></div>
          </div>
          <div class="about-path" title="${esc(ABOUT?.data_dir || '')}">${T('set.data_dir', { path: esc(ABOUT?.data_dir || '') })}</div>
          <div class="set-actions" style="margin-top:9px">
            <button class="btn btn-sm" id="setOpenData">${T('set.open_data_dir')}</button>
            <button class="btn btn-sm" id="setCheckUpd">${T('set.check_update')}</button>
            <button class="btn btn-sm" id="setSelfCheck">${T('set.selfcheck_btn')}</button>
          </div>
          <div class="about-note" id="aboutUpd">
            ${T('set.about_note')}
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
      title: T('set.ask_game_dir_title'),
      label: T('set.ask_game_dir_label'),
      value: cfg.game_root || '',
      placeholder: 'D:\\game\\steam\\steamapps\\common\\The Witcher 3',
    });
    if (!p) return;
    try {
      await api('/api/settings', { action: 'set_game_root', path: p });
      toast(T('set.game_dir_updated'));
      await load();
      openSettingsDialog();
    } catch (e) { toast(T('set.save_failed', { msg: e.message }), 4000); }
  };

  /* ---- 下载目录 ---- */
  const addDir = async (path) => {
    if (!path) return;
    try {
      await api('/api/settings', { action: 'add_dir', path });
      toast(T('set.added'));
      refreshMainSoon();
      openSettingsDialog();
    } catch (e) { toast(T('set.add_failed', { msg: e.message }), 4000); }
  };

  $('#setAddDir').onclick = () => addDir($('#setAddManual').value.trim());
  $('#setPickDir').onclick = async () => {
    const r = await api('/api/pick-file', { kind: 'dir' }).catch(e => ({ error: e.message }));
    if (r.path) return addDir(r.path);
    if (r.error) toast(r.error, 3500);
  };
  $('#setResetDir').onclick = async () => {
    await api('/api/settings', { action: 'reset_dirs' });
    toast(T('set.dirs_restored'));
    refreshMainSoon();
    openSettingsDialog();
  };
  $('#setAddManual').addEventListener('keydown', e => {
    if (e.key === 'Enter') addDir(e.target.value.trim());
  });

  $$('#setDirs [data-rmdir]').forEach(b => {
    b.onclick = async () => {
      const p = b.dataset.rmdir;
      if (!confirm(T('set.confirm_remove_dir', { path: p }))) return;
      await api('/api/settings', { action: 'remove_dir', path: p });
      toast(T('set.removed'));
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
    toast(T('set.saved'));
  };
  $('#optNexusMeta').onchange = () => saveNexusOpt();
  $('#optNexusCover').onchange = () => saveNexusOpt();

  /* ---- 存储与清理 ---- */
  const doClean = async (ids, safeOnly) => {
    try {
      const r = await api('/api/storage/clean', { ids: ids || [], safe_only: !!safeOnly });
      toast(r.freed ? T('set.freed', { size: fmtSize(r.freed) }) : T('set.freed_none'));
      openSettingsDialog();
    } catch (e) { toast(T('set.clean_failed', { msg: e.message }), 4000); }
  };

  const safeBtn = $('#stgCleanSafe');
  if (safeBtn) safeBtn.onclick = () => doClean([], true);

  $$('[data-stg]').forEach(b => {
    b.onclick = () => {
      const g = (STG?.groups || []).find(x => x.id === b.dataset.stg);
      if (!g) return;
      const tip = g.risk === 'danger'
        ? T('set.confirm_danger', { title: g.title, desc: g.desc })
        : T('set.confirm_clean', { title: g.title, desc: g.desc });
      if (!confirm(tip)) return;
      doClean([g.id], false);
    };
  });

  /* ---- 关于 ---- */
  $('#setOpenData').onclick = async () => {
    try { await api('/api/open-folder', { key: '@data' }); }
    catch (e) { toast(T('set.open_failed', { msg: e.message }), 3500); }
  };
  $('#setCheckUpd').onclick = async () => {
    const box = $('#aboutUpd');
    const btn = $('#setCheckUpd');
    btn.disabled = true;
    box.textContent = T('set.checking');
    try {
      const r = await api('/api/app/check', {});
      box.className = 'about-note' + (r.newer ? ' is-new' : '');
      // 句子自己用当前语言拼，不用后端那句 message —— 切语言后这里也要跟着变。
      // 结果只有三种：有新版 / 已是最新 / 没配更新源。
      box.innerHTML = (r.newer
        ? T('self.new_version', { ver: esc(r.latest || '') })
        : (r.ok ? T('set.up_to_date') : T('set.no_feed')))
        + (r.url && r.newer ? ` · <a href="#" id="aboutGo">${T('set.update_link')}</a>` : '');
      const go = $('#aboutGo');
      if (go) go.onclick = (e) => {
        e.preventDefault();
        // 走服务端：--app 窗口会把 window.open 接住，开成一个没有地址栏的怪窗口
        api('/api/open-url', { url: r.url }).catch(err => toast(err.message, 3500));
      };
    } catch (e) {
      box.textContent = T('set.check_failed', { msg: e.message });
    } finally {
      btn.disabled = false;
    }
  };

  /* 重新跑一次启动自检（1.2.9）。只查本地数据，不联网、不迁移。 */
  $('#setSelfCheck').onclick = async () => {
    const box = $('#aboutUpd');
    const btn = $('#setSelfCheck');
    btn.disabled = true;
    box.textContent = T('set.selfchecking');
    try {
      const r = await api('/api/selfcheck', {});
      const items = (r.selfcheck && r.selfcheck.items) || [];
      const bad = items.filter(i => i.level === 'error' || i.level === 'warn');
      box.className = 'about-note' + (bad.length ? ' is-warn' : '');
      box.innerHTML = bad.length
        ? bad.map(i => '· ' + T('self.' + i.code, i.params || {})).join('<br>')
        : T('set.selfcheck_ok');
      if (r.ui && S.data) { S.data.ui = r.ui; renderSelfBar(); }
    } catch (e) {
      box.textContent = T('set.check_failed', { msg: e.message });
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
