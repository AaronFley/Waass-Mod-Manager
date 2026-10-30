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
  mod_folder: '模组',
  dlc_folder: 'DLC',
  menu_xml: '菜单',
  bin_config: '游戏配置',
  bin_binary: '游戏程序',
};

/* ---------- 安装弹窗 ---------- */
let ARC_DIR = '';          // '' = 看所有目录

async function openInstallDialog() {
  openModal('导入压缩包', `
    <div class="inst">
      <div class="inst-label">压缩包路径</div>
      <div class="inst-inputrow">
        <input id="instPath" class="inst-input" spellcheck="false"
               placeholder="粘贴 zip / 7z / rar 的完整路径">
        <button class="btn" id="instBrowse">浏览…</button>
      </div>
      <div class="inst-hint">浏览器读不到拖进来的文件路径，所以请用「浏览」或直接粘贴。</div>
      <div class="inst-label" style="margin-top:18px">下载目录里的包</div>
      <div class="dir-chips" id="instDirs"></div>
      <div class="inst-recent" id="instRecent">正在找…</div>
      <div class="inst-preview" id="instPreview" hidden></div>
    </div>`, { wide: true });

  $('#instBrowse').onclick = async () => {
    const r = await apiRaw('/api/pick-file', {});
    if (r.ok && r.data.path) {
      $('#instPath').value = r.data.path;
      doPreview(r.data.path);
    } else if (!r.ok) {
      toast(r.data.error || '打不开选择框', 3500);
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
      chips.innerHTML = [`<button class="chip ${ARC_DIR ? '' : 'is-on'}" data-dir="">全部</button>`]
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
      这个目录里没有 zip / 7z / rar。<br>
      如果 mod 存在别的地方，去「设置」里加一个下载目录，或者用上面的「浏览」选。</div>`;
    return;
  }

  box.innerHTML = items.map(it => `
    <button class="arc-row ${it.known ? 'is-known' : ''}" data-path="${esc(it.path)}">
      <span class="arc-name">${esc(it.name)}</span>
      ${it.version ? `<span class="arc-ver">${esc(it.version)}</span>` : ''}
      <span class="arc-id">${it.nexus_id ? '#' + it.nexus_id : ''}</span>
      <span class="arc-size">${fmtSize(it.size)}</span>
      ${it.known
        ? '<span class="arc-flag">已装过</span>'
        : '<span class="arc-flag is-new">新</span>'}
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
  box.innerHTML = `<div class="inst-hint">正在解包检查…</div>`;
  const r = await apiRaw('/api/preview', { archive: path });
  if (!r.ok) {
    box.innerHTML = `<div class="pv-error">${esc(r.data.error || '读不了这个包')}</div>`;
    return;
  }
  PREVIEW = r.data;
  const pv = r.data;

  const top = pv.entries.filter(e => e.kind !== 'bin_config');
  const shown = top.slice(0, 12);
  const rows = shown.map(e => `
    <div class="pv-row">
      <span class="pv-kind k-${e.kind}">${KIND_LABEL[e.kind] || '其他'}</span>
      <span class="pv-dst">${esc(e.dst)}</span>
      ${e.conflict ? '<span class="pv-flag">已存在</span>' : ''}
    </div>`).join('');
  const more = top.length > shown.length
    ? `<div class="pv-more">还有 ${top.length - shown.length} 项…</div>` : '';

  const riskHtml = pv.risk === 'high' ? `
    <div class="pv-risk">
      <b>这个包会写进游戏程序目录</b>
      <div>${pv.bin_binaries.map(x => esc(x)).join('<br>')}</div>
      <div class="pv-risk-sub">装错会导致游戏起不来。确认它对应你的游戏版本再继续。</div>
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
      if (m.added?.length) bits.push(`新增 ${m.added.map(esc).join('、')}`);
      if (m.modified?.length) bits.push(`修改 ${m.modified.map(esc).join('、')}`);
      const skipped = (m.skipped || []).map(s =>
        `<span class="pv-skip">跳过 ${esc(s.key)}：${esc(s.why)}</span>`);
      return `<div class="pv-merge-row">
        <div class="pv-merge-file">${esc(m.dst)}</div>
        <div class="pv-merge-what">${bits.length ? bits.join('；') : '无需改动'}</div>
        ${skipped.join('')}
      </div>`;
    }).join('');
    return `<div class="pv-merge">${lines}</div>`;
  })();
  const cfgHtml = cfgs.length ? `
    <div class="pv-cfg">
      <b>会改游戏自带的配置文件（按行合并，不整份替换）</b>
      <div>${cfgs.map(x => esc(x)).join('<br>')}</div>
      ${mergeDetail}
      <div class="pv-cfg-sub">只把 mod 写的那几行并进去：你的画质设置、按键绑定、注释都保留原样。
        卸载时只撤掉这几行，不会删掉整个文件。</div>
    </div>` : '';

  // 高危那段已经单独说了，这里不再重复
  const warns = (pv.warnings || []).filter(w => !w.includes('游戏程序目录'));
  const warnHtml = warns.length
    ? `<div class="pv-warn">${warns.map(w => esc(w)).join('<br>')}</div>` : '';

  // 覆盖清单不能只报个数。「装之前能看见将失去什么」是这一步的全部意义，
  // 所以给可展开的完整清单，而不是一句「其中 N 项已经有了」。
  const conflictNote = pv.conflicts.length ? `
    <details class="pv-note pv-conflicts">
      <summary>其中 ${pv.conflicts.length} 项游戏里已经有了，继续装会覆盖掉 —— 点开看是哪些</summary>
      <div class="pv-cf-list">
        ${pv.conflicts.slice(0, 60).map(c => `<div class="pv-cf-row">
          <span class="pv-kind k-${esc(c.kind || '')}">${KIND_LABEL[c.kind] || '其他'}</span>
          <code>${esc(c.dst)}</code>
        </div>`).join('')}
        ${pv.conflicts.length > 60
          ? `<div class="pv-more">还有 ${pv.conflicts.length - 60} 项…</div>` : ''}
      </div>
      <div class="pv-cf-foot">旧的那份会移到隔离区，可以还原，不会被删除。</div>
    </details>`
    : '';

  // 这段最后整段插进 innerHTML，来源是 Nexus 返回的字段（作者名、版本号）。
  // 别处都过了 esc，这里漏一次就等于给注入留了个口子 —— 一律先转义再拼。
  const metaLine = (() => {
    const bits = [];
    if (pv.nexus_id) bits.push('Nexus #' + esc(pv.nexus_id));
    if (pv.meta?.author) bits.push('作者 ' + esc(pv.meta.author));
    if (pv.meta?.version) bits.push('Nexus 上是 v' + esc(pv.meta.version));
    return bits.length ? bits.join(' · ') + ' · ' : '';
  })();
  const officialName = (pv.meta?.name && pv.meta.name !== pv.name)
    ? `<div class="pv-official">Nexus 上的完整标题：${esc(pv.meta.name)}</div>` : '';

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
      <button class="btn btn-primary" id="instGo">${pv.conflicts.length ? '覆盖安装' : '安装'}</button>
      <span class="pv-foot">装完可随时卸载，文件进隔离区不删除</span>
    </div>`;

  $('#instGo').onclick = doInstall;
}

async function doInstall() {
  if (!PREVIEW) return;
  const btn = $('#instGo');
  btn.disabled = true;
  btn.textContent = '安装中…';

  const body = {
    archive: PREVIEW.archive,
    allow_risk: PREVIEW.risk === 'high',
    on_conflict: PREVIEW.conflicts.length ? 'replace' : 'abort',
  };
  const r = await apiRaw('/api/install', body);
  btn.disabled = false;

  if (!r.ok) {
    btn.textContent = '重试';
    toast(r.data.error || '安装失败', 5000);
    return;
  }

  closeModal(true);
  const meta = r.data.meta || {};
  const got = meta.author || meta.version
    ? `（Nexus 上是 ${meta.author ? meta.author + ' 的' : ''}${
        meta.version ? ' v' + meta.version : ''}）` : '';
  toast(`${r.data.name} 装好了${r.data.mods.length ? '：' + r.data.mods.join('、') : ''}${got}`,
    meta.version ? 4200 : 3200);
  await load();
}

/* ---------- 已安装包 ---------- */
async function openPackagesDialog(focusId = '') {
  const r = await apiRaw('/api/packages');
  const g = r.data || { active: [], removed: [], superseded: [] };

  const what = p => {
    const parts = [...(p.mods || []), ...(p.dlc || [])];
    if (parts.length) return parts.join('、');
    return `${p.file_count || 0} 个文件`;
  };

  const row = (p, mode) => `
    <div class="pkg-row ${focusId && p.id === focusId ? 'is-focus' : ''}" data-pkgrow="${esc(p.id)}">
      <div class="pkg-main">
        <div class="pkg-name">${esc(p.name)}
          <span class="pkg-ver">${esc(p.version || '')}</span>
          ${p.risk === 'high' ? '<span class="pkg-risk">写程序目录</span>' : ''}
        </div>
        <div class="pkg-sub">
          ${p.nexus_id ? 'Nexus #' + p.nexus_id + ' · ' : ''}${esc(what(p))}
        </div>
      </div>
      <button class="btn btn-sm" data-pkg="${esc(p.id)}" data-mode="${mode}">
        ${mode === 'active' ? '卸载' : '恢复'}</button>
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
      <div class="pkg-group-title">档案外的手动模组<em>${unknown.length}</em></div>
      <div class="pkg-hint">这些是 mods 目录里存在、但没有安装记录的。管理器不会替你动它们。</div>
      ${unknown.map(n => `<div class="pkg-row"><div class="pkg-main">
        <div class="pkg-name">${esc(n)}</div></div></div>`).join('')}
    </div>` : '';

  const focused = g.active.find(p => p.id === focusId);

  openModal('已安装的包', `
    <div class="pkg">
      ${focused ? `<div class="pkg-focus-hint">
        你要找的模组在这个包里。这个包一共带了 ${(focused.mods || []).length} 个模组，
        卸载会把它们一起移走（进隔离区，可恢复）。
      </div>` : ''}
      ${block('已安装', g.active, 'active', '')}
      ${block('已卸载（在隔离区，可一键恢复）', g.removed, 'removed',
              '文件没被删除，恢复等于重装，不用重新下载。')}
      ${unknownBlock}
      ${!g.active.length && !g.removed.length && !unknown.length
        ? '<div class="inst-hint">还没有通过 Waass 装过东西。</div>' : ''}
    </div>`, { wide: true });

  if (focusId) {
    const el = $$('#modalBody [data-pkgrow]').find(x => x.dataset.pkgrow === focusId);
    if (el) el.scrollIntoView({ block: 'center' });
  }

  $$('#modalBody [data-pkg]').forEach(b => {
    b.onclick = async () => {
      const mode = b.dataset.mode;
      b.disabled = true;
      b.textContent = mode === 'active' ? '卸载中…' : '恢复中…';
      const res = await apiRaw(mode === 'active' ? '/api/uninstall' : '/api/restore',
                               { package_id: b.dataset.pkg });
      if (!res.ok) {
        toast(res.data.error || '操作失败', 5000);
        b.disabled = false;
        b.textContent = mode === 'active' ? '卸载' : '恢复';
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
          toast('已卸载：mod 文件移进隔离区，游戏配置里只撤掉了它自己加的那几行');
        } else if (d.moved?.length) {
          toast('已移到隔离区，可随时恢复');
        } else {
          toast('已卸载');
        }
      } else {
        toast('已恢复');
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
        <span>当初写进去 <code>${esc(c.expected ?? '')}</code></span>
        <span>现在文件里 <code>${esc(c.found ?? '')}</code></span>
      </div>
      ${c.why ? `<div class="uc-why">${esc(c.why)}</div>` : ''}
    </div>`).join('');

  openModal('卸载完成，但有配置没有还原', `
    <div class="uc">
      <div class="uc-lead">
        <b>${esc(d.name || '这个包')}</b> 已经卸载，文件进了隔离区。
        下面这 <b>${list.length}</b> 处游戏配置<u>没有</u>硬还原 ——
        因为装上它之后这些值又被改过（可能是游戏自己写的，也可能是别的 mod 或你自己改的），
        强行还原会把那些改动一起吃掉，所以留着没动。
      </div>
      <div class="uc-list">${rows}</div>
      <div class="uc-hint">
        现在这些值还停在「装着这个 mod 时」的状态。进游戏发现按键或设置不对，
        要么打开配置目录自己改回来，要么直接把这次卸载撤销掉、让东西原样装回来。
      </div>
      <div class="set-actions" style="margin-top:12px;flex-wrap:wrap">
        <button class="btn btn-sm btn-primary" id="ucUndo">撤销这次卸载</button>
        <button class="btn btn-sm" id="ucOpen">打开配置目录</button>
        <button class="btn btn-sm btn-ghost" id="ucOk">就这样，我知道了</button>
      </div>
    </div>`, { wide: list.length > 2 });

  $('#ucOk').onclick = () => { closeModal(true); openPackagesDialog(); };
  $('#ucOpen').onclick = async () => {
    try { await api('/api/open-folder', { key: '@userconfig' }); }
    catch (e) { toast('打不开：' + e.message, 3500); }
  };
  $('#ucUndo').onclick = async () => {
    const b = $('#ucUndo');
    b.disabled = true;
    b.textContent = '正在撤销…';
    try {
      await api('/api/undo', {});
      closeModal(true);
      toast('已撤销，东西装回来了');
      await load();
      openPackagesDialog();
    } catch (e) {
      toast('撤销失败：' + e.message, 4000);
      b.disabled = false;
      b.textContent = '撤销这次卸载';
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
          <span class="pkg-ver">${it.mode === 'full' ? '全量' : '增量'}</span></div>
        <div class="pkg-sub">${it.count} 个文件 · ${fmtSize(it.bytes)}${it.note ? ' · ' + esc(it.note) : ''}</div>
      </div>
      <button class="btn btn-sm" data-save="${esc(it.id)}">恢复</button>
    </div>`).join('');

  openModal('存档备份', `
    <div class="pkg">
      <div class="pkg-hint" style="margin-top:0">存档位置：${esc(dir)}</div>
      <div class="save-actions">
        <button class="btn btn-primary" id="saveFull">备份全部存档</button>
        <button class="btn" id="saveInc">只备份新增的</button>
      </div>
      <div class="pkg-group">
        <div class="pkg-group-title">已有备份<em>${items.length}</em></div>
        ${rows || '<div class="inst-hint">还没有备份。装 mod 前建议先点一次「备份全部存档」。</div>'}
      </div>
    </div>`, { wide: true });

  const doBackup = async (mode, btn) => {
    btn.disabled = true;
    const old = btn.textContent;
    btn.textContent = '备份中…';
    const res = await apiRaw('/api/saves/backup', { mode });
    btn.disabled = false;
    btn.textContent = old;
    if (!res.ok) return toast(res.data.error || '备份失败', 4000);
    toast(res.data.empty ? '自上次备份以来没有新存档' : `已备份 ${res.data.count} 个文件`);
    openSavesDialog();
  };
  $('#saveFull').onclick = e => doBackup('full', e.target);
  $('#saveInc').onclick = e => doBackup('incremental', e.target);

  $$('#modalBody [data-save]').forEach(b => {
    b.onclick = async () => {
      b.disabled = true;
      b.textContent = '恢复中…';
      const res = await apiRaw('/api/saves/restore', { id: b.dataset.save });
      if (!res.ok) {
        toast(res.data.error || '恢复失败', 4000);
        b.disabled = false;
        b.textContent = '恢复';
        return;
      }
      toast(`已恢复 ${res.data.restored} 个存档文件`);
      openSavesDialog();
    };
  });
}
