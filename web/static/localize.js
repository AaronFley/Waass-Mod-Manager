/* 汉化面板（1.2.0 重做）
   旧流程：按模组提取 → 导出 JSON → 交给 AI → 粘回来 → 应用。五个步骤里
   中间三步是因为程序不会翻译，硬塞给用户的。现在程序自己翻：
   勾模组 → 一键补翻 → 表格里改 → 一次写入。翻译在后台跑，进度可轮询。

   依赖 app.js：api / openModal / closeModal / esc / toast / load / $ / $$
   数据来自：
     GET  /api/localize/panel   模组清单 + 类别 + 引擎状态 + 历次记录
     POST /api/localize/run     {mods:[key], cats:[id]} → 任务快照
                                首次会回 {need_consent:true, engines:[...]}
     POST /api/localize/consent 记下「同意把文字发出去」，然后重发 run
     GET  /api/localize/job?id= 进度
     POST /api/localize/write   {id, edits, only}
     POST /api/localize/restore {record, mode, force}
     POST /api/localize/config  {order, engines:{id:{on,keys}}}
     POST /api/localize/probe   真发一次请求测连通性
*/

let LOC = null;          // 面板数据
let SEL = new Set();     // 勾中的模组 key
let CATS = new Set();    // 勾中的内容类别
let JOB = null;          // 当前任务快照
let POLL = null;         // 轮询定时器
let EDITS = new Map();   // 手改过的译文：itemId → text
let SHOW = 'all';        // 结果表筛选：all / todo

const CAT_ICON = { menu: '▤', mod_name: '◈', item: '◍' };

async function openLocalizeDialog() {
  openModal(T('ui.nav_localize'), `<div class="upd-loading">${T('localize.loading_panel')}</div>`, { wide: true });
  SEL = new Set();
  EDITS = new Map();
  JOB = null;
  SHOW = 'all';
  try {
    LOC = await api('/api/localize/panel');
  } catch (e) {
    $('#modalBody').innerHTML = `<div class="pv-error">${esc(e.message)}</div>`;
    return;
  }
  CATS = new Set((LOC.cats || []).filter(c => c.default).map(c => c.id));
  renderLocalize();
}

/* ---------------- 第一屏：选什么 ---------------- */

function engLine() {
  const list = LOC.engines || [];
  const live = list.filter(e => e.on && e.ready);
  if (!live.length) {
    return `<span class="loc-eng-bad">${T('localize.engine_none')}</span>
      <button class="btn btn-sm" data-locact="engine">${T('localize.go_settings')}</button>`;
  }
  const top = live.find(e => e.default) || live[0];
  const more = live.length > 1 ? ` <span class="loc-eng-more">${T('localize.engine_more', { n: live.length - 1 })}</span>` : '';
  return `<b>${esc(top.label)}</b>${more}
    <span class="loc-eng-note">${T('localize.engine_note')}</span>
    <button class="btn btn-sm" data-locact="probe">${T('localize.probe')}</button>
    <button class="btn btn-sm" data-locact="engine">${T('localize.switch_engine')}</button>`;
}

function modRow(m) {
  const on = SEL.has(m.key);
  const c = m.counts || {};
  const cnt = [];
  if (m.menu) cnt.push(`<span class="loc-cnt loc-cnt-menu">${T('localize.cnt_menu', { n: m.menu })}</span>`);
  if (c.mod_name) cnt.push(`<span class="loc-cnt">${T('localize.cnt_name', { n: c.mod_name })}</span>`);
  if (c.item) cnt.push(`<span class="loc-cnt">${T('localize.cnt_item', { n: c.item })}</span>`);
  if (m.stale) {
    cnt.push(`<span class="loc-cnt loc-cnt-stale" title="${T('localize.stale_title')}">${T('localize.cnt_stale', { n: m.stale })}</span>`);
  }
  if (!cnt.length) cnt.push(`<span class="loc-cnt loc-cnt-off">${T('localize.cnt_none')}</span>`);
  const status = m.blocked
    ? `<span class="loc-why">${esc(m.blocked)}</span>`
    : (m.menu ? ''
       : (m.docs ? `<span class="loc-why">${esc(m.why || T('localize.why_names_only'))}</span>`
                 : `<span class="loc-why">${esc(m.why || '')}</span>`));
  return `
    <label class="loc-mod ${on ? 'is-on' : ''} ${m.ready ? '' : 'is-off'}">
      <input type="checkbox" data-locmod="${esc(m.key)}" ${on ? 'checked' : ''} ${m.ready ? '' : 'disabled'}>
      <span class="loc-mod-main">
        <span class="loc-mod-name">${esc(m.name)}
          ${m.enabled ? '' : `<span class="loc-tag">${T('common.disabled')}</span>`}</span>
        <span class="loc-mod-sub">${cnt.join('')}${status}</span>
      </span>
    </label>`;
}

function historyBlock() {
  const recs = LOC.records || [];
  if (!recs.length) {
    return `<div class="loc-hist-empty">${T('localize.hist_empty')}</div>`;
  }
  return recs.map(r => `
    <div class="loc-hist">
      <div class="loc-hist-main">
        <div class="loc-hist-name">${esc(r.label || T('ui.nav_localize'))}</div>
        <div class="loc-hist-sub">${esc(r.time)} · ${T('localize.hist_meta', { n: r.count || 0, f: (r.files || []).length })}</div>
      </div>
      <div class="loc-hist-act">
        <button class="btn btn-sm" data-restore="${esc(r.id)}" data-mode="hist">${T('localize.restore_batch')}</button>
        <button class="btn btn-sm" data-restore="${esc(r.id)}" data-mode="orig">${T('localize.restore_orig')}</button>
      </div>
    </div>`).join('');
}

function renderLocalize() {
  const rows = (LOC.mods || []).filter(m => m.ready || m.docs || m.blocked);
  const ready = rows.filter(m => m.ready);
  const total = ready.reduce((s, m) => s + m.menu, 0);
  const picked = rows.filter(m => SEL.has(m.key));
  const pickedCount = picked.reduce((s, m) => s + m.menu, 0);

  const catBox = (LOC.cats || []).map(c => `
    <label class="loc-cat ${CATS.has(c.id) ? 'is-on' : ''}" title="${esc(c.hint || '')}">
      <input type="checkbox" data-loccat="${esc(c.id)}" ${CATS.has(c.id) ? 'checked' : ''}>
      <span>${CAT_ICON[c.id] || '▫'} ${esc(c.label)}</span>
    </label>`).join('');

  $('#modalBody').innerHTML = `
    <div class="pkg">
      <div class="loc-hero${total ? '' : ' is-done'}">
        <div class="loc-hero-num">${total}</div>
        <div class="loc-hero-txt">
          ${total ? `<b>${T('localize.hero_left')}</b>
          <span>${TN('localize.hero_sub_one', 'localize.hero_sub_many', ready.length)}</span>`
        : `<b>${T('localize.hero_done')}</b>
          <span>${rows.length
              ? T('localize.hero_done_listed')
              : T('localize.hero_done_none')}</span>`}
        </div>
      </div>

      <div class="loc-eng">${engLine()}</div>

      <div class="loc-sec">
        <div class="loc-sec-title">${T('localize.sec_cats')}</div>
        <div class="loc-cats">${catBox}</div>
        <div class="loc-cat-hint">${
          CATS.has('mod_name') || CATS.has('item')
            ? T('localize.cat_hint_warn')
            : T('localize.cat_hint_default')
        }</div>
      </div>

      <div class="loc-sec">
        <div class="loc-sec-title">${T('localize.sec_mods')}
          <span class="loc-sec-act">
            <button class="btn btn-sm" data-locact="pickready">${T('localize.pick_ready')}</button>
            <button class="btn btn-sm" data-locact="picknone">${T('localize.pick_none')}</button>
          </span>
        </div>
        <div class="loc-mods">${rows.map(modRow).join('')}</div>
      </div>

      <div class="loc-go loc-go-sticky">
        <button class="btn btn-primary" id="locGo" ${picked.length ? '' : 'disabled'}>
          ${picked.length
            ? TN('localize.run_scope_one', 'localize.run_scope_many', picked.length, { s: pickedCount })
            : T('localize.run')}
        </button>
        <span class="loc-go-note">${picked.length
          ? T('localize.run_note', { n: Math.max(3, Math.round(pickedCount * 0.25)) })
          : T('localize.run_none')}</span>
      </div>

      <details class="loc-hist-box" ${(LOC.records || []).length ? '' : 'open'}>
        <summary>${T('localize.hist_title')} <em>${(LOC.records || []).length}</em></summary>
        <div class="loc-hist-note">${T('localize.hist_note')}</div>
        ${historyBlock()}
      </details>

      <div class="pv-note">
        ${T('localize.why_title')}<br>
        ${T('localize.why_manual')}<br>
        ${T('localize.why_unreadable')}<br>
        ${T('localize.why_script')}<br>
        ${T('localize.why_length')}
      </div>
    </div>`;

  $$('[data-locmod]').forEach(el => {
    el.onchange = () => {
      if (el.checked) SEL.add(el.dataset.locmod); else SEL.delete(el.dataset.locmod);
      renderLocalize();
    };
  });
  $$('[data-loccat]').forEach(el => {
    el.onchange = () => {
      if (el.checked) CATS.add(el.dataset.loccat); else CATS.delete(el.dataset.loccat);
      renderLocalize();
    };
  });
  $$('[data-locact]').forEach(b => { b.onclick = () => locAct(b.dataset.locact); });
  $$('[data-restore]').forEach(b => {
    b.onclick = () => doRestore(b.dataset.restore, b.dataset.mode, b);
  });
  const go = $('#locGo');
  if (go) go.onclick = startRun;
}

function locAct(act) {
  if (act === 'pickready') {
    SEL = new Set((LOC.mods || []).filter(m => m.ready).map(m => m.key));
    renderLocalize();
  } else if (act === 'picknone') {
    SEL = new Set();
    renderLocalize();
  } else if (act === 'engine') {
    openEngineDialog();
  } else if (act === 'probe') {
    probeEngines();
  }
}

/* ---------------- 第二屏：翻 ---------------- */

async function startRun() {
  if (!SEL.size) return toast(T('localize.err_no_mods'));
  if (!CATS.size) return toast(T('localize.err_no_cats'));
  const box = $('#modalBody');
  const go = box.querySelector('.loc-go');
  if (go) go.innerHTML = `<span class="loc-run-note">${T('localize.run_starting')}</span>`;
  let job;
  try {
    job = await api('/api/localize/run', { mods: [...SEL], cats: [...CATS] });
  } catch (e) {
    toast(e.message, 5000);
    renderLocalize();
    return;
  }
  // 没同意过就先说明再启动。任务这会儿还没建，服务端一个字都没发出去。
  if (job && job.need_consent) {
    openConsentDialog(job.engines || []);
    return;
  }
  JOB = job;
  EDITS = new Map();
  renderProgress();
  POLL = setInterval(pollJob, 600);
}

/* 首次同意：一张说明，讲清三件事 —— 发给谁、发什么、不发什么。
   同意一次就记住（服务端存在 data/translate/config.json）；
   关掉模态就当没点过：不建任务，也不偷偷记成同意。 */
function openConsentDialog(engines) {
  const rows = engines.length
    ? engines.map(e => `<li>${T('localize.consent_engine_row', {
        name: esc(e.label || ''), target: esc(e.target || ''),
      })}</li>`).join('')
    : `<li>${T('localize.engine_none')}</li>`;
  openModal(T('localize.consent_title'), `
    <div class="pkg">
      <div class="pv-note">${T('localize.consent_lead')}</div>
      <div class="loc-sec">
        <div class="loc-sec-title">${T('localize.consent_to_title')}</div>
        <ul class="loc-consent-list" style="margin:0 0 8px 18px;padding:0">${rows}</ul>
      </div>
      <div class="loc-sec">
        <div class="loc-sec-title">${T('localize.consent_send_title')}</div>
        <div>${T('localize.consent_send')}</div>
        <div class="loc-sec-title">${T('localize.consent_not_send_title')}</div>
        <div>${T('localize.consent_not_send')}</div>
      </div>
      <div class="pv-note">${T('localize.consent_once')}</div>
      <div class="set-actions">
        <button class="btn btn-primary" id="locConsentGo">${T('localize.consent_agree')}</button>
      </div>
    </div>`, { wide: true });

  $('#locConsentGo').onclick = async () => {
    const b = $('#locConsentGo');
    b.disabled = true;
    try {
      await api('/api/localize/consent', {});
    } catch (e) {
      toast(e.message, 5000);
      b.disabled = false;
      return;
    }
    const t = $('#modalTitle');
    if (t) t.textContent = T('ui.nav_localize');
    startRun();
  };
}

function renderProgress() {
  const d = JOB.done || 0, t = JOB.total || 1;
  const pct = Math.round(d * 100 / t);
  $('#modalBody').innerHTML = `
    <div class="pkg">
      <div class="loc-run">
        <div class="loc-run-title">${T('localize.run_progress', { d: d, t: t })}</div>
        <div class="loc-run-bar"><i style="width:${pct}%"></i></div>
        <div class="loc-run-sub">${T('localize.run_sub')}</div>
        <button class="btn btn-sm" id="locCancel">${T('localize.stop')}</button>
      </div>
    </div>`;
  $('#locCancel').onclick = async () => {
    try { await api('/api/localize/job/cancel', { id: JOB.id }); } catch (e) { /* 忽略 */ }
  };
}

async function pollJob() {
  if (!JOB) return;
  try {
    const s = await api('/api/localize/job?id=' + encodeURIComponent(JOB.id));
    JOB = s;
  } catch (e) {
    clearInterval(POLL); POLL = null;
    toast(T('localize.err_progress', { msg: e.message }), 5000);
    return;
  }
  if (JOB.status === 'running') { renderProgress(); return; }
  clearInterval(POLL); POLL = null;
  renderResults();
}

/* ---------------- 第三屏：看与改 ---------------- */

function effBad(r) {
  // 用户改过之后就以「当前输入」为准，别还挂着「放不下」——
  // 改短了就该立刻变成能写，不然人以为自己白改了。
  const dst = EDITS.has(r.id) ? EDITS.get(r.id) : (r.dst || '');
  if (!dst) return r.status === 'long' || r.status === 'fail';
  return !!(r.max && new TextEncoder().encode(dst).length > r.max);
}

function rowStatus(r) {
  if (effBad(r)) {
    return `<span class="loc-st loc-st-bad">${r.dst || EDITS.has(r.id) ? T('localize.st_too_long') : T('localize.st_failed')}</span>`;
  }
  if (String(r.status).startsWith('cancel')) return `<span class="loc-st">${T('localize.st_cancelled')}</span>`;
  if (r.engine === 'memory') return `<span class="loc-st loc-st-ok">${T('localize.st_memory')}</span>`;
  if (r.engine === 'glossary') return `<span class="loc-st loc-st-ok">${T('localize.st_glossary')}</span>`;
  if (r.shortened) return `<span class="loc-st loc-st-warn">${T('localize.st_shortened')}</span>`;
  return `<span class="loc-st loc-st-ok">${T('localize.st_done')}</span>`;
}

function resultRow(r) {
  const dst = EDITS.has(r.id) ? EDITS.get(r.id) : (r.dst || '');
  const bad = effBad(r);
  if (SHOW === 'todo' && !bad) return '';
  const used = new TextEncoder().encode(dst).length;
  const room = r.max ? `${used}/${r.max}B` : '';
  return `
    <tr class="${bad ? 'is-bad' : ''}">
      <td class="loc-td-mod" title="${esc(r.mod_name)}">${esc(r.mod_name)}</td>
      <td class="loc-td-src">${esc(r.src)}</td>
      <td class="loc-td-dst">
        <input class="loc-input" data-edit="${esc(r.id)}" value="${esc(dst)}"
               placeholder="${bad ? T('localize.ph_shorter') : ''}">
        <div class="loc-td-meta">${rowStatus(r)} <span class="loc-bytes">${room}</span>
          ${r.why ? `<span class="loc-why-inline">${esc(r.why)}</span>` : ''}</div>
      </td>
    </tr>`;
}

function willWrite(r) {
  // 真正会被写进去的才算：有译文、没取消、长度没超
  const dst = EDITS.has(r.id) ? EDITS.get(r.id) : (r.dst || '');
  if (!dst || String(r.status).startsWith('cancel')) return false;
  if (r.max && new TextEncoder().encode(dst).length > r.max) return false;
  return true;
}

function renderResults() {
  const items = JOB.items || [];
  const c = JOB.counts || {};
  const writable = items.filter(willWrite);
  const bad = items.filter(effBad).length;

  $('#modalBody').innerHTML = `
    <div class="pkg">
      <div class="loc-sum2">
        <div><b>${writable.length}</b><span>${T('localize.sum_writable')}</span></div>
        <div class="${bad ? 'is-warn' : ''}"><b>${bad}</b><span>${T('localize.sum_bad')}</span></div>
        <div><b>${JOB.total}</b><span>${T('localize.sum_total')}</span></div>
      </div>

      ${bad ? `<div class="loc-tip">${T('localize.tip_bad')}</div>` : ''}

      <div class="loc-filter">
        <button class="btn btn-sm ${SHOW === 'all' ? 'is-on' : ''}" data-show="all">${T('localize.filter_all', { n: items.length })}</button>
        <button class="btn btn-sm ${SHOW === 'todo' ? 'is-on' : ''}" data-show="todo">${T('localize.filter_todo', { n: bad })}</button>
      </div>

      <div class="loc-table-wrap">
        <table class="loc-table">
          <thead><tr><th>${T('localize.th_mod')}</th><th>${T('localize.th_src')}</th><th>${T('localize.th_dst')}</th></tr></thead>
          <tbody>${items.map(resultRow).join('')}</tbody>
        </table>
      </div>

      <div class="loc-go loc-go-sticky">
        <button class="btn btn-primary" id="locWrite" ${writable.length ? '' : 'disabled'}>
          ${TN('localize.write_n_one', 'localize.write_n_many', writable.length)}
        </button>
        <button class="btn btn-sm" data-locact2="back">${T('localize.back')}</button>
        <span class="loc-go-note">${T('localize.write_note')}</span>
      </div>
    </div>`;

  $$('#modalBody [data-edit]').forEach(el => {
    el.oninput = () => { EDITS.set(el.dataset.edit, el.value); };
    el.onchange = () => { renderResults(); };
  });
  $$('#modalBody [data-show]').forEach(b => {
    b.onclick = () => { SHOW = b.dataset.show; renderResults(); };
  });
  $$('#modalBody [data-locact2]').forEach(b => {
    if (b.dataset.locact2 === 'back') b.onclick = () => renderLocalize();
  });
  $('#locWrite').onclick = doWrite;
}

async function doWrite() {
  const btn = $('#locWrite');
  btn.disabled = true;
  btn.textContent = T('localize.writing');
  const edits = [...EDITS.entries()].map(([id, dst]) => ({ id, dst }));
  try {
    const r = await api('/api/localize/write', { id: JOB.id, edits });
    const box = $('#modalBody');
    if (!r.ok) {
      const why = r.error || T('localize.err_nothing_written');
      const list = (r.skipped || []).slice(0, 5).map(s =>
        `<div class="loc-skip">${esc(s.text || '')} ${T('localize.skip_bytes', { n: (s.need || 0) - (s.room || 0) })}</div>`).join('');
      box.querySelector('.loc-go-sticky').insertAdjacentHTML('afterend',
        `<div class="pv-warn">${esc(why)}${list ? `<div class="loc-skipbox">${list}</div>` : ''}</div>`);
      btn.disabled = false;
      btn.textContent = T('common.retry');
      return;
    }
    await load(true);
    LOC = await api('/api/localize/panel');
    box.innerHTML = `
      <div class="pkg">
        <div class="loc-done2">
          <div class="loc-done2-num">${r.changed}</div>
          <div class="loc-done2-txt"><b>${TN('localize.done_written_one', 'localize.done_written_many', r.changed)}</b>
            <span>${TN('localize.done_files_one', 'localize.done_files_many', (r.files || []).length)}</span></div>
        </div>
        ${r.skipped_count ? `<div class="loc-tip">${TN('localize.done_skipped_one', 'localize.done_skipped_many', r.skipped_count)}</div>` : ''}
        <div class="pv-warn">${T('localize.done_restart')}</div>
        <div class="set-actions">
          <button class="btn btn-sm btn-primary" id="locAgain">${T('localize.again')}</button>
          <button class="btn btn-sm" data-restore-back="1">${T('localize.restore_orig_btn')}</button>
        </div>
      </div>`;
    $('#locAgain').onclick = () => { JOB = null; SEL = new Set(); renderLocalize(); };
    const back = $('[data-restore-back]');
    if (back) back.onclick = () => doRestore(r.record, 'orig', back);
  } catch (e) {
    toast(e.message, 5000);
    btn.disabled = false;
    btn.textContent = T('common.retry');
  }
}

/* ---------------- 还原 ---------------- */

async function doRestore(recordId, mode, btn) {
  const what = mode === 'orig' ? T('localize.confirm_orig') : T('localize.confirm_batch');
  if (!confirm(what)) return;
  btn.disabled = true;
  const old = btn.textContent;
  btn.textContent = T('localize.restoring');
  try {
    let r = await api('/api/localize/restore', { record: recordId || null, mode, force: false });
    if (r.ok && (r.skipped || []).length) {
      const drift = r.skipped.filter(s => s.code === 'changed_again');
      if (drift.length) {
        const go = confirm(T('localize.confirm_drift', { n: drift.length }));
        if (go) r = await api('/api/localize/restore', { record: recordId || null, mode, force: true });
      }
    }
    if (r.ok) {
      const n = (r.restored || []).length;
      toast(n ? TN('localize.restored_one', 'localize.restored_many', n) : T('localize.restored_none'), 3500);
      await load(true);
      LOC = await api('/api/localize/panel');
      JOB = null;
      renderLocalize();
    } else {
      toast(r.why || r.error || T('localize.err_restore'), 5000);
    }
  } catch (e) {
    toast(e.message, 5000);
  } finally {
    btn.disabled = false;
    btn.textContent = old;
  }
}

/* ---------------- 引擎设置 ---------------- */

async function probeEngines() {
  toast(T('localize.probing'), 2500);
  try {
    const r = await api('/api/localize/probe', {});
    const lines = (r.engines || []).map(e =>
      `<div class="loc-probe ${e.ok ? 'is-ok' : 'is-bad'}">
        <b>${esc(e.label)}</b>
        <span>${e.ok ? `${e.ms} ms · ${esc(String(e.dst || '').slice(0, 24))}` : esc(e.why || T('localize.engine_down'))}</span>
      </div>`).join('');
    LOC.engines = LOC.engines.map(e => {
      const hit = (r.engines || []).find(x => x.id === e.id);
      return hit ? { ...e, ready: hit.ok, why: hit.why } : e;
    });
    const box = $('.loc-eng');
    if (box) box.innerHTML = `${engLine()}<div class="loc-probe-box">${lines}</div>`;
    $$('[data-locact="engine"]').forEach(b => { b.onclick = () => openEngineDialog(); });
    const p = $('[data-locact="probe"]');
    if (p) p.onclick = probeEngines;
  } catch (e) {
    toast(e.message, 5000);
  }
}

async function openEngineDialog() {
  const rows = (LOC.engines || []).map(e => `
    <div class="loc-engrow">
      <label class="loc-engrow-head">
        <input type="checkbox" data-eng="${esc(e.id)}" ${e.on ? 'checked' : ''}>
        <b>${esc(e.label)}</b>
        ${e.ready ? `<span class="loc-tag loc-tag-ok">${T('localize.engine_up')}</span>`
                  : `<span class="loc-tag">${esc(e.why || T('localize.engine_down'))}</span>`}
      </label>
      ${e.needs_key ? `<textarea class="loc-keys" data-keys="${esc(e.id)}" rows="2"
        placeholder="${T('localize.keys_ph')}">${
          esc((e.keys_list || []).join('\n'))}</textarea>` : ''}
    </div>`).join('');

  openModal(T('localize.engine_dialog_title'), `
    <div class="pkg">
      <div class="loc-eng-note2">${T('localize.engine_dialog_note')}</div>
      ${rows}
      <div class="set-actions" style="margin-top:10px">
        <button class="btn btn-sm btn-primary" id="locEngSave">${T('common.save')}</button>
        <button class="btn btn-sm" id="locEngProbe">${T('localize.probe')}</button>
      </div>
      <div class="pv-note">${T('localize.engine_key_note')}</div>
    </div>`, { wide: true });

  $('#locEngSave').onclick = async () => {
    const engines = {};
    $$('[data-eng]').forEach(el => {
      engines[el.dataset.eng] = engines[el.dataset.eng] || {};
      engines[el.dataset.eng].on = el.checked;
    });
    $$('[data-keys]').forEach(el => {
      const id = el.dataset.keys;
      engines[id] = engines[id] || {};
      engines[id].keys = el.value.split('\n').map(s => s.trim()).filter(Boolean);
    });
    try {
      const r = await api('/api/localize/config', { engines });
      LOC.engines = r.engines || LOC.engines;
      toast(T('localize.saved'));
      openLocalizeDialog();
    } catch (e) {
      toast(e.message, 5000);
    }
  };
  $('#locEngProbe').onclick = probeEngines;
}

document.addEventListener('DOMContentLoaded', () => {
  const n = $('#navLocalize');
  if (n) n.onclick = openLocalizeDialog;
  const q = new URLSearchParams(location.search);
  if (q.get('dialog') !== 'localize') return;
  openLocalizeDialog().then(() => {
    // 调试用：?locsel=key1,key2 预勾选，?locauto=1 直接开翻。
    // 截图和自动化验收要靠它，省得每次手点。
    const sel = (q.get('locsel') || '').split(',').filter(Boolean);
    if (sel.length) { SEL = new Set(sel); renderLocalize(); }
    if (q.get('locauto')) setTimeout(startRun, 300);
    // ?locjob=<id> 直接看某个已完成任务的结果表（截图用）
    if (q.get('locjob')) {
      api('/api/localize/job?id=' + encodeURIComponent(q.get('locjob')))
        .then(s => { JOB = s; renderResults(); })
        .catch(e => toast(e.message, 4000));
    }
  });
});
