/* 加载顺序：一个能直接拖的列表，改完一次写进 mods.settings。
   依赖 app.js 的 api / openModal / openSheet / esc / toast / load / S

   1.4 起只排「真正会加载」的那几个：禁用的模组不参与排序，折叠在列表底部。
   面板上只留一行提示，原来那四段说明挪到「?」里。 */

let ORD = [];                    // 启用中的 key，按显示顺序
let ORD_OFF = [];                // 禁用中的 key，按原优先级（不排序，只为保存时带上）
let ORD_MODS = new Map();        // key → 记录
let ORD_BASE = '';               // 打开时的顺序，用来判断「改过没有」
let ORD_QUERY = '';              // 搜索词，只影响显示
let ORD_SHOW_OFF = false;        // 禁用那一段展开没有
let DRAG_KEY = null;

const ordDirty = () => ORD.join('|') !== ORD_BASE;

/* 一行的显示名：中文名优先，副标题放分类。
   搜索要能把这三种都匹配上，用户记得哪个就打哪个。 */
function ordLabel(m, key) {
  return {
    name: m.name_cn || m.name || key,
    sub: m.name_cn ? (m.name || m.category || '') : (m.category || ''),
  };
}

async function openOrderDialog() {
  openModal(T('ui.nav_order'), `<div class="upd-loading">${T('common.loading')}</div>`, { wide: true });
  if (!S.data?.scan) {
    try { await load(); } catch { /* 下面会提示 */ }
  }
  const mods = S.data?.scan?.mods || [];
  if (!mods.length) {
    $('#modalBody').innerHTML = `<div class="pkg-hint">${T('order.empty')}</div>`;
    return;
  }
  const sorted = mods.slice().sort((a, b) => (a.priority ?? 1e9) - (b.priority ?? 1e9) ||
    (a.name_cn || a.name).localeCompare(b.name_cn || b.name, 'zh'));
  ORD_MODS = new Map(mods.map(m => [m.key, m]));
  ORD = sorted.filter(m => m.enabled).map(m => m.key);
  ORD_OFF = sorted.filter(m => !m.enabled).map(m => m.key);
  ORD_BASE = ORD.join('|');
  ORD_QUERY = '';
  ORD_SHOW_OFF = false;

  // 关窗前提醒：拖完直接按 ✕ 会把改动悄悄丢掉（以前就是这样）
  guardModalClose(() => {
    if (!ordDirty()) return true;
    return confirm(T('order.confirm_close'));
  });

  renderOrder();
}

function renderOrder() {
  const q = ORD_QUERY.trim().toLowerCase();
  const match = key => {
    if (!q) return true;
    const m = ORD_MODS.get(key) || {};
    return [m.name_cn, m.name, m.category, key]
      .filter(Boolean).some(s => String(s).toLowerCase().includes(q));
  };

  // 序号用真实位置（ORD 里的下标），搜索时不重编号 ——
  // 看到「7」就该知道它排在第七个加载，不是「筛出来的第七个」。
  const row = key => {
    const m = ORD_MODS.get(key) || {};
    const lab = ordLabel(m, key);
    const i = ORD.indexOf(key);
    return `<div class="ord-row" data-key="${esc(key)}" draggable="true">
      <span class="ord-idx">${i + 1}</span>
      <span class="ord-grip" aria-hidden="true">
        <svg viewBox="0 0 16 16"><path d="M6 4h.01M6 8h.01M6 12h.01M10 4h.01M10 8h.01M10 12h.01"></path></svg>
      </span>
      <span class="ord-name">${esc(lab.name)}${lab.sub ? `<em>${esc(lab.sub)}</em>` : ''}</span>
      <span class="ord-btns">
        <button class="ord-mv" data-top title="${T('order.pin')}">⇈</button>
        <button class="ord-mv" data-move="-1" title="${T('order.move_up')}" ${i === 0 ? 'disabled' : ''}>↑</button>
        <button class="ord-mv" data-move="1" title="${T('order.move_down')}" ${i === ORD.length - 1 ? 'disabled' : ''}>↓</button>
      </span>
    </div>`;
  };

  const visible = ORD.filter(match);
  const rows = visible.map(row).join('');

  // 禁用的那一段：默认折叠成一行。它们不参与加载，排哪儿都一样 ——
  // 展开是为了让用户确认「我确实关掉了这几个」，不是用来排序的。
  const offRows = ORD_SHOW_OFF ? ORD_OFF.map(key => {
    const m = ORD_MODS.get(key) || {};
    const lab = ordLabel(m, key);
    return `<div class="ord-row is-off" data-key="${esc(key)}">
      <span class="ord-idx">${m.priority ?? '—'}</span>
      <span class="ord-grip" aria-hidden="true"></span>
      <span class="ord-name">${esc(lab.name)}${lab.sub ? `<em>${esc(lab.sub)}</em>` : ''}</span>
      <span class="ord-tag is-off">${T('common.disabled')}</span>
    </div>`;
  }).join('') : '';
  const offFold = ORD_OFF.length ? `
    <button class="ord-offfold" id="ordOffFold">
      ${T('order.off_fold', { n: ORD_OFF.length })}
      <em>${ORD_SHOW_OFF ? T('order.fold_close') : T('order.fold_open')}</em>
    </button>
    ${offRows}` : '';

  const dirty = ordDirty();

  $('#modalBody').innerHTML = `
    <div class="ord-head">
      <div class="ord-hint">
        ${T('order.hint_line')}
        <button class="ord-why" id="ordWhy" title="${esc(T('order.why_title'))}">?</button>
      </div>
      <input class="inst-input ord-search" id="ordSearch" value="${esc(ORD_QUERY)}"
        placeholder="${esc(T('order.search_ph'))}" spellcheck="false">
    </div>
    <div class="ord-list" id="ordList">
      ${q && !visible.length ? `<div class="ord-none">${T('order.search_none')}</div>` : rows}
      ${offFold}
    </div>
    <div class="ord-foot">
      <button class="btn btn-primary btn-sm" id="ordSave">${T('order.btn_save')}</button>
      <button class="btn btn-sm" id="ordReset">${T('order.btn_reset')}</button>
      <span class="ord-state ${dirty ? 'is-dirty' : ''}">
        ${dirty ? T('order.state_dirty') : T('order.state_clean')}
      </span>
      <span class="ord-count">${T('order.count_on', { n: ORD.length })}</span>
    </div>`;

  bindOrder();
}

function bindOrder() {
  const list = $('#ordList');

  /* ---- 上移 / 下移 / 置顶（只在 hover 的那一行出现） ---- */
  $$('#ordList [data-move]').forEach(b => {
    b.onclick = () => {
      const key = b.closest('.ord-row').dataset.key;
      const i = ORD.indexOf(key);
      const j = i + Number(b.dataset.move);
      if (i < 0 || j < 0 || j >= ORD.length) return;
      ORD.splice(j, 0, ORD.splice(i, 1)[0]);
      renderOrder();
      flash(key);
    };
  });
  $$('#ordList [data-top]').forEach(b => {
    b.onclick = () => {
      const key = b.closest('.ord-row').dataset.key;
      const i = ORD.indexOf(key);
      if (i <= 0) return;
      ORD.splice(0, 0, ORD.splice(i, 1)[0]);
      renderOrder();
      flash(key);
    };
  });
  function flash(key) {
    const el = $(`#ordList .ord-row[data-key="${CSS.escape(key)}"]`);
    if (el) el.classList.add('is-flash');
  }

  /* ---- 搜索：只筛显示，不动顺序 ---- */
  const box = $('#ordSearch');
  if (box) {
    box.addEventListener('input', () => {
      ORD_QUERY = box.value;
      const pos = box.selectionStart;
      renderOrder();
      const again = $('#ordSearch');
      if (again) { again.focus(); again.setSelectionRange(pos, pos); }
    });
  }

  // 展开后要跟着滚过去：新增的那几行在列表下方，不滚就等于「点了没反应」
  const fold = $('#ordOffFold');
  if (fold) {
    fold.onclick = () => {
      ORD_SHOW_OFF = !ORD_SHOW_OFF;
      renderOrder();
      const again = $('#ordList');
      // 直接滚到底：展开的那几行在折叠行之后，scrollIntoView 停在折叠行
      // 上就等于什么都没看见
      if (ORD_SHOW_OFF && again) again.scrollTop = again.scrollHeight;
    };
  }

  $('#ordWhy').onclick = () => openSheet(T('order.why_title'), `
    <p class="sheet-lead">${T('order.hint_html')}</p>`);

  /* ---- 拖拽（只在弹窗内部，不影响主界面）---- */
  list.addEventListener('dragstart', e => {
    const row = e.target.closest('.ord-row');
    if (!row || row.classList.contains('is-off')) return;
    DRAG_KEY = row.dataset.key;
    row.classList.add('is-dragging');
    e.dataTransfer.effectAllowed = 'move';
    e.dataTransfer.setData('text/plain', DRAG_KEY);
  });
  list.addEventListener('dragend', () => {
    DRAG_KEY = null;
    $$('.ord-row').forEach(r => r.classList.remove('is-dragging', 'is-before', 'is-after'));
  });
  list.addEventListener('dragover', e => {
    if (!DRAG_KEY) return;
    const row = e.target.closest('.ord-row');
    if (!row || row.dataset.key === DRAG_KEY || row.classList.contains('is-off')) return;
    e.preventDefault();
    e.dataTransfer.dropEffect = 'move';
    const box = row.getBoundingClientRect();
    const after = e.clientY > box.top + box.height / 2;
    $$('.ord-row').forEach(r => r.classList.remove('is-before', 'is-after'));
    row.classList.add(after ? 'is-after' : 'is-before');
  });
  list.addEventListener('drop', e => {
    if (!DRAG_KEY) return;
    const row = e.target.closest('.ord-row');
    if (!row || row.classList.contains('is-off')) return;
    e.preventDefault();
    const box = row.getBoundingClientRect();
    const after = e.clientY > box.top + box.height / 2;
    const from = ORD.indexOf(DRAG_KEY);
    if (from < 0) return;
    ORD.splice(from, 1);
    let to = ORD.indexOf(row.dataset.key);
    if (after) to += 1;
    ORD.splice(to, 0, DRAG_KEY);
    DRAG_KEY = null;
    renderOrder();
  });

  /* ---- 保存 / 还原 ---- */
  $('#ordSave').onclick = async () => {
    const btn = $('#ordSave');
    btn.disabled = true;
    btn.textContent = T('order.saving');
    try {
      // 禁用的跟在启用后面一起发：不给它们号，它们就会和重新编号的
      // 启用模组撞号（dup_priority）。它们本来也不加载，排在尾部没有代价。
      await api('/api/reorder', { keys: [...ORD, ...ORD_OFF] });
      ORD_BASE = ORD.join('|');          // 保存成功，不再算「未保存」
      closeModal(true);
      toast(T('order.saved'), 3400, { label: T('order.undo'), fn: undoNow });
      await load();
    } catch (err) {
      toast(T('order.save_failed', { error: err.message }), 4000);
      btn.disabled = false;
      btn.textContent = T('order.btn_save');
    }
  };
  $('#ordReset').onclick = () => {
    if (ordDirty() && !confirm(T('order.confirm_reset'))) return;
    openOrderDialog();
  };
}

document.addEventListener('DOMContentLoaded', () => {
  const n = $('#navOrder');
  if (n) n.onclick = () => openOrderDialog();
  if (new URLSearchParams(location.search).get('dialog') === 'order') openOrderDialog();
});
