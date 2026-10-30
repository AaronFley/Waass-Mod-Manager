/* 加载顺序：一个能直接拖的列表，改完一次写进 mods.settings。
   依赖 app.js 的 api / openModal / esc / toast / load / S */

let ORD = [];                    // 当前顺序（全是 mod key）
let ORD_MODS = new Map();        // key → 记录
let ORD_BASE = '';               // 打开时的顺序，用来判断「改过没有」
let DRAG_KEY = null;

const ordDirty = () => ORD.join('|') !== ORD_BASE;

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
  ORD_MODS = new Map(mods.map(m => [m.key, m]));
  ORD = mods.slice()
    .sort((a, b) => (a.priority ?? 1e9) - (b.priority ?? 1e9) ||
      (a.name_cn || a.name).localeCompare(b.name_cn || b.name, 'zh'))
    .map(m => m.key);
  ORD_BASE = ORD.join('|');

  // 关窗前提醒：拖完直接按 ✕ 会把改动悄悄丢掉（以前就是这样）
  guardModalClose(() => {
    if (!ordDirty()) return true;
    return confirm(T('order.confirm_close'));
  });

  renderOrder();
}

function renderOrder() {
  const rows = ORD.map((key, i) => {
    const m = ORD_MODS.get(key) || {};
    const name = m.name_cn || m.name || key;
    const sub = m.name_cn ? m.name : (m.category || '');
    return `<div class="ord-row ${m.enabled ? '' : 'is-off'}" data-key="${esc(key)}" draggable="true">
      <span class="ord-idx">${i + 1}</span>
      <span class="ord-grip" aria-hidden="true">
        <svg viewBox="0 0 16 16"><path d="M6 4h.01M6 8h.01M6 12h.01M10 4h.01M10 8h.01M10 12h.01"></path></svg>
      </span>
      <span class="ord-name">${esc(name)}${sub ? `<em>${esc(sub)}</em>` : ''}</span>
      <span class="ord-tag ${m.enabled ? 'is-on' : 'is-off'}">${m.enabled ? T('common.enabled') : T('common.disabled')}</span>
      <span class="ord-btns">
        <button class="ord-mv" data-move="-1" title="${T('order.move_up')}" ${i === 0 ? 'disabled' : ''}>↑</button>
        <button class="ord-mv" data-move="1" title="${T('order.move_down')}" ${i === ORD.length - 1 ? 'disabled' : ''}>↓</button>
      </span>
    </div>`;
  }).join('');

  const on = ORD.filter(k => ORD_MODS.get(k)?.enabled).length;
  const hasMerge = !!S.data?.scan?.merged?.exists;
  const dirty = ordDirty();

  $('#modalBody').innerHTML = `
    <div class="ord-hint">
      ${T('order.hint_html', { on: on })}
      ${hasMerge ? `<br>${T('order.hint_merge')}` : ''}
    </div>
    <div class="ord-list" id="ordList">${rows}</div>
    <div class="ord-foot">
      <button class="btn btn-primary btn-sm" id="ordSave">${T('order.btn_save')}</button>
      <button class="btn btn-sm" id="ordReset">${T('order.btn_reset')}</button>
      <span class="ord-state ${dirty ? 'is-dirty' : ''}">
        ${dirty ? T('order.state_dirty') : T('order.state_clean')}
      </span>
    </div>`;

  bindOrder();
}

function bindOrder() {
  const list = $('#ordList');

  /* ---- ↑ ↓ ---- */
  $$('#ordList [data-move]').forEach(b => {
    b.onclick = () => {
      const key = b.closest('.ord-row').dataset.key;
      const i = ORD.indexOf(key);
      const j = i + Number(b.dataset.move);
      if (j < 0 || j >= ORD.length) return;
      ORD.splice(j, 0, ORD.splice(i, 1)[0]);
      renderOrder();
    };
  });

  /* ---- 拖拽（只在弹窗内部，不影响主界面）---- */
  list.addEventListener('dragstart', e => {
    const row = e.target.closest('.ord-row');
    if (!row) return;
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
    if (!row || row.dataset.key === DRAG_KEY) return;
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
    if (!row) return;
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
      await api('/api/reorder', { keys: ORD });
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
