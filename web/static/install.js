/* 安装 / 卸载界面
   依赖 app.js 里的全局工具：api / toast / openModal / esc / fmtSize / load */

async function apiRaw(path, body) {
  const opt = { headers: { 'X-Waass-Token': TOKEN, 'Content-Type': 'application/json' } };
  if (body !== undefined) { opt.method = 'POST'; opt.body = JSON.stringify(body); }
  const r = await fetch(path, opt);
  let data = {};
  try { data = await r.json(); } catch { /* 空体 */ }
  return { ok: r.ok, status: r.status, data };
}

const KIND_LABEL = {
  mod_folder: 'install.kind_mod',
  dlc_folder: 'install.kind_dlc',
  menu_xml: 'install.kind_menu',
  bin_config: 'install.kind_game_config',
  bin_binary: 'install.kind_game_binary',
};

/* ---------- 安装弹窗 ---------- */
let ARC_DIR = '';          // '' = 看所有目录

async function openInstallDialog() {
  openModal(T('ui.import'), `
    <div class="inst">
      <div class="inst-label">${T('install.archive_path')}</div>
      <div class="inst-inputrow">
        <input id="instPath" class="inst-input" spellcheck="false"
               placeholder="${T('install.path_ph')}">
        <button class="btn" id="instBrowse">${T('install.browse')}</button>
      </div>
      <div class="inst-hint">${T('install.path_hint')}</div>
      <div class="inst-label" style="margin-top:18px">${T('install.download_dir_packs')}</div>
      <div class="dir-chips" id="instDirs"></div>
      <div class="inst-recent" id="instRecent">${T('install.searching')}</div>
      <div class="inst-preview" id="instPreview" hidden></div>
    </div>`, { wide: true });

  $('#instBrowse').onclick = async () => {
    const r = await apiRaw('/api/pick-file', {});
    if (r.ok && r.data.path) {
      $('#instPath').value = r.data.path;
      doPreview(r.data.path);
    } else if (!r.ok) {
      toast(r.data.error || T('install.pick_failed'), 3500);
    }
  };

  $('#instPath').addEventListener('keydown', e => {
    if (e.key === 'Enter') doPreview($('#instPath').value.trim());
  });

  loadRecentArchives();
}

async function loadRecentArchives() {
  const box = $('#instRecent');
  const chips = $('#instDirs');
  const q = ARC_DIR ? '?dir=' + encodeURIComponent(ARC_DIR) : '';
  const r = await apiRaw('/api/archives' + q);
  const items = (r.data.items || []).slice(0, 40);
  const dirs = r.data.dirs || [];

  // 目录筛选：只有两个以上目录时才值得显示
  if (chips) {
    if (dirs.length > 1) {
      chips.innerHTML = [`<button class="chip ${ARC_DIR ? '' : 'is-on'}" data-dir="">${T('install.all_dirs')}</button>`]
        .concat(dirs.map(d => `<button class="chip ${ARC_DIR === d.path ? 'is-on' : ''}"
          data-dir="${esc(d.path)}" title="${esc(d.path)}">${esc(d.name)}</button>`))
        .join('');
      chips.hidden = false;
      $$('#instDirs .chip').forEach(b => {
        b.onclick = () => { ARC_DIR = b.dataset.dir; loadRecentArchives(); };
      });
    } else chips.hidden = true;
  }

  if (!items.length) {
    box.innerHTML = `<div class="inst-hint" style="padding:10px 11px">
      ${T('install.no_archives')}</div>`;
    return;
  }

  box.innerHTML = items.map(it => `
    <button class="arc-row ${it.known ? 'is-known' : ''}" data-path="${esc(it.path)}">
      <span class="arc-name">${esc(it.name)}</span>
      ${it.version ? `<span class="arc-ver">${esc(it.version)}</span>` : ''}
      <span class="arc-id">${it.nexus_id ? '#' + it.nexus_id : ''}</span>
      <span class="arc-size">${fmtSize(it.size)}</span>
      ${it.known
        ? `<span class="arc-flag">${T('install.installed_before')}</span>`
        : `<span class="arc-flag is-new">${T('install.new_flag')}</span>`}
    </button>`).join('');

  box.querySelectorAll('.arc-row').forEach(b => {
    b.onclick = () => {
      box.querySelectorAll('.arc-row').forEach(x => x.classList.remove('is-on'));
      b.classList.add('is-on');
      $('#instPath').value = b.dataset.path;
      doPreview(b.dataset.path);
    };
  });
}

let PREVIEW = null;

async function doPreview(path) {
  const box = $('#instPreview');
  box.hidden = false;
  box.innerHTML = `<div class="inst-hint">${T('install.scanning')}</div>`;
  const r = await apiRaw('/api/preview', { archive: path });
  if (!r.ok) {
    box.innerHTML = `<div class="pv-error">${esc(r.data.error || T('install.read_failed'))}</div>`;
    return;
  }
  PREVIEW = r.data;
  const pv = r.data;

  const top = pv.entries.filter(e => e.kind !== 'bin_config');
  const shown = top.slice(0, 12);
  const rows = shown.map(e => `
    <div class="pv-row">
      <span class="pv-kind k-${e.kind}">${KIND_LABEL[e.kind] ? T(KIND_LABEL[e.kind]) : T('install.kind_other')}</span>
      <span class="pv-dst">${esc(e.dst)}</span>
      ${e.conflict ? `<span class="pv-flag">${T('install.file_exists')}</span>` : ''}
    </div>`).join('');
  const more = top.length > shown.length
    ? `<div class="pv-more">${T('install.more_items', { n: top.length - shown.length })}</div>` : '';

  const riskHtml = pv.risk === 'high' ? `
    <div class="pv-risk">
      <b>${T('install.risk_bin_title')}</b>
      <div>${pv.bin_binaries.map(x => esc(x)).join('<br>')}</div>
      <div class="pv-risk-sub">${T('install.risk_bin_sub')}</div>
    </div>` : '';

  /* bin/config 下的配置文件（典型：SmartAutoLoot 的 autoloot.ini）。
     它不写游戏程序、风险低于 dll，但确实是改游戏自身的配置，得让人装前看见。 */
  const cfgs = pv.bin_configs || [];
  const mergeMap = new Map((pv.merges || []).map(m => [m.dst, m]));
  /* 有合并信息的，把「加了几项、改了几项、跳过了什么」摊开说。
     用户最怕的是「装个 mod 把我按键弄没了」，这里必须让他能看明白。 */
  const mergeDetail = (() => {
    const ms = (pv.merges || []);
    if (!ms.length) return '';
    const lines = ms.map(m => {
      const bits = [];
      if (m.added?.length) bits.push(T('install.merge_added',
        { items: m.added.map(esc).join(T('install.list_sep')) }));
      if (m.modified?.length) bits.push(T('install.merge_modified',
        { items: m.modified.map(esc).join(T('install.list_sep')) }));
      const skipped = (m.skipped || []).map(s =>
        `<span class="pv-skip">${T('install.merge_skipped', { key: esc(s.key), why: esc(s.why) })}</span>`);
      return `<div class="pv-merge-row">
        <div class="pv-merge-file">${esc(m.dst)}</div>
        <div class="pv-merge-what">${bits.length ? bits.join(T('install.clause_sep')) : T('install.merge_none')}</div>
        ${skipped.join('')}
      </div>`;
    }).join('');
    return `<div class="pv-merge">${lines}</div>`;
  })();
  const cfgHtml = cfgs.length ? `
    <div class="pv-cfg">
      <b>${T('install.cfg_title')}</b>
      <div>${cfgs.map(x => esc(x)).join('<br>')}</div>
      ${mergeDetail}
      <div class="pv-cfg-sub">${T('install.cfg_sub')}</div>
    </div>` : '';

  // 高危那段已经单独说了，这里不再重复
  // 警告有两种形态：纯字符串，或 {code, message} 对象。code 是给程序看的
  // 稳定标识（比如 inside_game_bin = 要往游戏 bin 目录写文件，高危区已单独
  // 提示过，这里不再重复），message 才是给人看的 —— 后端已按界面语言译好。
  // 以前按中文文本过滤，英文界面一开就会重复显示，改成按 code 判。
  const warns = (pv.warnings || []).filter(w =>
    !(w && typeof w === 'object' && w.code === 'inside_game_bin'));
  const warnText = w => (w && typeof w === 'object') ? String(w.message || '') : String(w);
  const warnHtml = warns.length
    ? `<div class="pv-warn">${warns.map(w => esc(warnText(w))).join('<br>')}</div>` : '';

  // 覆盖清单不能只报个数。「装之前能看见将失去什么」是这一步的全部意义，
  // 所以给可展开的完整清单，而不是一句「其中 N 项已经有了」。
  const conflictNote = pv.conflicts.length ? `
    <details class="pv-note pv-conflicts">
      <summary>${T('install.conflicts_summary', { n: pv.conflicts.length })}</summary>
      <div class="pv-cf-list">
        ${pv.conflicts.slice(0, 60).map(c => `<div class="pv-cf-row">
          <span class="pv-kind k-${esc(c.kind || '')}">${KIND_LABEL[c.kind] ? T(KIND_LABEL[c.kind]) : T('install.kind_other')}</span>
          <code>${esc(c.dst)}</code>
        </div>`).join('')}
        ${pv.conflicts.length > 60
          ? `<div class="pv-more">${T('install.more_items', { n: pv.conflicts.length - 60 })}</div>` : ''}
      </div>
      <div class="pv-cf-foot">${T('install.conflict_foot')}</div>
    </details>`
    : '';

  // 这段最后整段插进 innerHTML，来源是 Nexus 返回的字段（作者名、版本号）。
  // 别处都过了 esc，这里漏一次就等于给注入留了个口子 —— 一律先转义再拼。
  const metaLine = (() => {
    const bits = [];
    if (pv.nexus_id) bits.push('Nexus #' + esc(pv.nexus_id));
    if (pv.meta?.author) bits.push(T('install.meta_author', { name: esc(pv.meta.author) }));
    if (pv.meta?.version) bits.push(T('install.meta_version', { version: esc(pv.meta.version) }));
    return bits.length ? bits.join(' · ') + ' · ' : '';
  })();
  const officialName = (pv.meta?.name && pv.meta.name !== pv.name)
    ? `<div class="pv-official">${T('install.official_title', { name: esc(pv.meta.name) })}</div>` : '';

  box.innerHTML = `
    <div class="pv-head">
      <div class="pv-title">${esc(pv.name)}
        <span class="pv-ver">${esc(pv.version || '')}</span></div>
      <div class="pv-meta">${metaLine}${esc(pv.summary)}</div>
    </div>
    ${officialName}
    ${riskHtml}
    ${cfgHtml}
    ${conflictNote}
    ${warnHtml}
    <div class="pv-list">${rows}${more}</div>
    <div class="pv-actions">
      <button class="btn btn-primary" id="instGo">${pv.conflicts.length
        ? T('install.overwrite_install') : T('install.install_btn')}</button>
      <span class="pv-foot">${T('install.foot_hint')}</span>
    </div>`;

  $('#instGo').onclick = doInstall;
}

async function doInstall() {
  if (!PREVIEW) return;
  const btn = $('#instGo');
  btn.disabled = true;
  btn.textContent = T('install.installing');

  const body = {
    archive: PREVIEW.archive,
    allow_risk: PREVIEW.risk === 'high',
    on_conflict: PREVIEW.conflicts.length ? 'replace' : 'abort',
  };
  const r = await apiRaw('/api/install', body);
  btn.disabled = false;

  if (!r.ok) {
    btn.textContent = T('common.retry');
    toast(r.data.error || T('install.install_failed'), 5000);
    return;
  }

  closeModal(true);
  const meta = r.data.meta || {};
  const got = meta.author && meta.version
    ? T('install.nexus_meta_both', { author: meta.author, version: meta.version })
    : meta.author
      ? T('install.nexus_meta_author', { author: meta.author })
      : meta.version
        ? T('install.nexus_meta_version', { version: meta.version })
        : '';
  toast(T(r.data.mods.length ? 'install.done_with_mods' : 'install.done_plain', {
    name: r.data.name,
    mods: r.data.mods.join(T('install.list_sep')),
    meta: got,
  }), meta.version ? 4200 : 3200);
  await load();
}

/* ---------- 已安装包 ---------- */
async function openPackagesDialog(focusId = '') {
  const r = await apiRaw('/api/packages');
  const g = r.data || { active: [], removed: [], superseded: [] };

  const what = p => {
    const parts = [...(p.mods || []), ...(p.dlc || [])];
    if (parts.length) return parts.join(T('install.list_sep'));
    return TN('install.file_count_one', 'install.file_count_many', p.file_count || 0);
  };

  const row = (p, mode) => `
    <div class="pkg-row ${focusId && p.id === focusId ? 'is-focus' : ''}" data-pkgrow="${esc(p.id)}">
      <div class="pkg-main">
        <div class="pkg-name">${esc(p.name)}
          <span class="pkg-ver">${esc(p.version || '')}</span>
          ${p.risk === 'high' ? `<span class="pkg-risk">${T('install.writes_bin')}</span>` : ''}
        </div>
        <div class="pkg-sub">
          ${p.nexus_id ? 'Nexus #' + p.nexus_id + ' · ' : ''}${esc(what(p))}
        </div>
      </div>
      <button class="btn btn-sm" data-pkg="${esc(p.id)}" data-mode="${mode}">
        ${mode === 'active' ? T('install.uninstall') : T('install.restore')}</button>
    </div>`;

  const block = (title, items, mode, hint) => items.length ? `
    <div class="pkg-group">
      <div class="pkg-group-title">${title}<em>${items.length}</em></div>
      ${hint ? `<div class="pkg-hint">${hint}</div>` : ''}
      ${items.map(p => row(p, mode)).join('')}
    </div>` : '';

  const unknown = (S.data?.unknown_mods || []);
  const unknownBlock = unknown.length ? `
    <div class="pkg-group">
      <div class="pkg-group-title">${T('install.unknown_mods_title')}<em>${unknown.length}</em></div>
      <div class="pkg-hint">${T('install.unknown_mods_hint')}</div>
      ${unknown.map(n => `<div class="pkg-row"><div class="pkg-main">
        <div class="pkg-name">${esc(n)}</div></div></div>`).join('')}
    </div>` : '';

  const focused = g.active.find(p => p.id === focusId);

  openModal(T('install.packages_title'), `
    <div class="pkg">
      ${focused ? `<div class="pkg-focus-hint">
        ${T('install.focus_hint', { n: (focused.mods || []).length })}
      </div>` : ''}
      ${block(T('install.group_installed'), g.active, 'active', '')}
      ${block(T('install.group_removed'), g.removed, 'removed',
              T('install.removed_hint'))}
      ${unknownBlock}
      ${!g.active.length && !g.removed.length && !unknown.length
        ? `<div class="inst-hint">${T('install.empty_packages')}</div>` : ''}
    </div>`, { wide: true });

  if (focusId) {
    const el = $$('#modalBody [data-pkgrow]').find(x => x.dataset.pkgrow === focusId);
    if (el) el.scrollIntoView({ block: 'center' });
  }

  $$('#modalBody [data-pkg]').forEach(b => {
    b.onclick = async () => {
      const mode = b.dataset.mode;
      b.disabled = true;
      b.textContent = mode === 'active' ? T('install.uninstalling') : T('install.restoring');
      const res = await apiRaw(mode === 'active' ? '/api/uninstall' : '/api/restore',
                               { package_id: b.dataset.pkg });
      if (!res.ok) {
        toast(res.data.error || T('install.action_failed'), 5000);
        b.disabled = false;
        b.textContent = mode === 'active' ? T('install.uninstall') : T('install.restore');
        return;
      }
      if (mode === 'active') {
        const d = res.data || {};
        if (d.merge_conflicts?.length) {
          // 事故级信息，不能用 7 秒后自己消失的浮层说完。先刷列表，
          // 再用一个必须点一下才走的对话框讲清楚（1.1.0 之前就是这条被漏掉的）。
          await load();
          showUninstallConflicts(d);
          return;
        }
        if (d.reverted?.length) {
          toast(T('install.uninstalled_reverted'));
        } else if (d.moved?.length) {
          toast(T('install.moved_to_quarantine'));
        } else {
          toast(T('install.uninstalled'));
        }
      } else {
        toast(T('install.restored'));
      }
      await load();
      openPackagesDialog();
    };
  });
}

/* 卸载完了，但有几行配置没还原。
   这件事的后果是「游戏里按键可能还留着上一个 mod 的绑定」，属于用户会真的
   丢东西却完全不知道的那一类，所以必须停下来讲，而不是角标里飘一句。
   三条出路都不新增写路径：撤销用的是现成的 journal。 */
function showUninstallConflicts(d) {
  const list = d.merge_conflicts || [];
  const rows = list.map(c => `
    <div class="uc-row">
      <div class="uc-file">${esc(c.dst || '')}</div>
      <div class="uc-key">
        ${c.section ? `<span class="uc-sec">[${esc(c.section)}]</span>` : ''}
        <code>${esc(c.key || '')}</code>
      </div>
      <div class="uc-vals">
        <span>${T('install.uc_expected', { value: `<code>${esc(c.expected ?? '')}</code>` })}</span>
        <span>${T('install.uc_found', { value: `<code>${esc(c.found ?? '')}</code>` })}</span>
      </div>
      ${c.why ? `<div class="uc-why">${esc(c.why)}</div>` : ''}
    </div>`).join('');

  openModal(T('install.uc_title'), `
    <div class="uc">
      <div class="uc-lead">${T('install.uc_lead', {
        name: esc(d.name || T('install.this_package')),
        n: list.length,
      })}</div>
      <div class="uc-list">${rows}</div>
      <div class="uc-hint">${T('install.uc_hint')}</div>
      <div class="set-actions" style="margin-top:12px;flex-wrap:wrap">
        <button class="btn btn-sm btn-primary" id="ucUndo">${T('install.uc_undo')}</button>
        <button class="btn btn-sm" id="ucOpen">${T('install.uc_open')}</button>
        <button class="btn btn-sm btn-ghost" id="ucOk">${T('install.uc_ok')}</button>
      </div>
    </div>`, { wide: list.length > 2 });

  $('#ucOk').onclick = () => { closeModal(true); openPackagesDialog(); };
  $('#ucOpen').onclick = async () => {
    try { await api('/api/open-folder', { key: '@userconfig' }); }
    catch (e) { toast(T('install.open_failed', { msg: e.message }), 3500); }
  };
  $('#ucUndo').onclick = async () => {
    const b = $('#ucUndo');
    b.disabled = true;
    b.textContent = T('install.undoing');
    try {
      await api('/api/undo', {});
      closeModal(true);
      toast(T('install.undo_done'));
      await load();
      openPackagesDialog();
    } catch (e) {
      toast(T('install.undo_failed', { msg: e.message }), 4000);
      b.disabled = false;
      b.textContent = T('install.uc_undo');
    }
  };
}

/* ---------- 挂钩 ---------- */
document.addEventListener('DOMContentLoaded', () => {
  const imp = $('#btnImport');
  if (imp) imp.onclick = openInstallDialog;

  const sv = $('#navSaves');
  if (sv) sv.onclick = openSavesDialog;

  // 便于截图和自测：?dialog=install / packages / saves
  // ?dialog=install&path=<完整路径> 直接预览某个包（以后 nxm:// 接管也走这条路）
  // ?dialog=packages&pkg=<包 id> 直接定位到某个包（详情面板里「卸载这个模组」走的就是这条）
  const params = new URLSearchParams(location.search);
  const want = params.get('dialog');
  if (want === 'install') {
    openInstallDialog().then(() => {
      const pre = params.get('path');
      if (pre) {
        $('#instPath').value = pre;
        doPreview(pre);
      }
    });
  } else if (want === 'packages') openPackagesDialog(params.get('pkg') || '');
  else if (want === 'saves') openSavesDialog();
});

/* ---------- 存档备份 ---------- */
async function openSavesDialog() {
  const r = await apiRaw('/api/saves');
  const items = (r.data.items || []);
  const dir = r.data.dir || '';

  const rows = items.map(it => `
    <div class="pkg-row">
      <div class="pkg-main">
        <div class="pkg-name">${esc(it.time.replace('T', ' '))}
          <span class="pkg-ver">${it.mode === 'full'
            ? T('install.save_mode_full') : T('install.save_mode_inc')}</span></div>
        <div class="pkg-sub">${TN('install.save_row_one', 'install.save_row_many',
          it.count, { size: fmtSize(it.bytes) })}${it.note ? ' · ' + esc(it.note) : ''}</div>
      </div>
      <button class="btn btn-sm" data-save="${esc(it.id)}">${T('install.restore')}</button>
    </div>`).join('');

  openModal(T('ui.nav_saves'), `
    <div class="pkg">
      <div class="pkg-hint" style="margin-top:0">${T('install.saves_dir', { path: esc(dir) })}</div>
      <div class="save-actions">
        <button class="btn btn-primary" id="saveFull">${T('install.backup_all')}</button>
        <button class="btn" id="saveInc">${T('install.backup_new')}</button>
      </div>
      <div class="pkg-group">
        <div class="pkg-group-title">${T('install.group_backups')}<em>${items.length}</em></div>
        ${rows || `<div class="inst-hint">${T('install.no_backups')}</div>`}
      </div>
    </div>`, { wide: true });

  const doBackup = async (mode, btn) => {
    btn.disabled = true;
    const old = btn.textContent;
    btn.textContent = T('install.backing_up');
    const res = await apiRaw('/api/saves/backup', { mode });
    btn.disabled = false;
    btn.textContent = old;
    if (!res.ok) return toast(res.data.error || T('install.backup_failed'), 4000);
    toast(res.data.empty
      ? T('install.backup_empty')
      : TN('install.backup_done_one', 'install.backup_done_many', res.data.count));
    openSavesDialog();
  };
  $('#saveFull').onclick = e => doBackup('full', e.target);
  $('#saveInc').onclick = e => doBackup('incremental', e.target);

  $$('#modalBody [data-save]').forEach(b => {
    b.onclick = async () => {
      b.disabled = true;
      b.textContent = T('install.restoring');
      const res = await apiRaw('/api/saves/restore', { id: b.dataset.save });
      if (!res.ok) {
        toast(res.data.error || T('install.restore_failed'), 4000);
        b.disabled = false;
        b.textContent = T('install.restore');
        return;
      }
      toast(TN('install.restore_done_one', 'install.restore_done_many', res.data.restored));
      openSavesDialog();
    };
  });
}
