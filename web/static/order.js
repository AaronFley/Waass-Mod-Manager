/* 加载顺序：一个能直接拖的列表，改完一次写进 mods.settings。
   依赖 app.js 的 api / openModal / esc / toast / load / S */

let ORD = [];                    // 当前顺序（全是 mod key）
let ORD_MODS = new Map();        // key → 记录
let ORD_BASE = '';               // 打开时的顺序，用来判断「改过没有」
let DRAG_KEY = null;

const ordDirty = () => ORD.join('|') !== ORD_BASE;

async function openOrderDialog() {
  openModal('加载顺序', '<div class="upd-loading">正在读取…</div>', { wide: true });
  if (!S.data?.scan) {
    try { await load(); } catch { /* 下面会提示 */ }
  }
  const mods = S.data?.scan?.mods || [];
  if (!mods.length) {
    $('#modalBody').innerHTML = '<div class="pkg-hint">还没有扫描到模组。</div>';
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
    return confirm('顺序改了但还没保存。\n\n关掉的话这些改动就丢了 —— 点「取消」回去点一下「保存顺序」。');
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
      <span class="ord-tag ${m.enabled ? 'is-on' : 'is-off'}">${m.enabled ? '已启用' : '已禁用'}</span>
      <span class="ord-btns">
        <button class="ord-mv" data-move="-1" title="上移" ${i === 0 ? 'disabled' : ''}>↑</button>
        <button class="ord-mv" data-move="1" title="下移" ${i === ORD.length - 1 ? 'disabled' : ''}>↓</button>
      </span>
    </div>`;
  }).join('');

  const on = ORD.filter(k => ORD_MODS.get(k)?.enabled).length;
  const hasMerge = !!S.data?.scan?.merged?.exists;
  const dirty = ordDirty();

  $('#modalBody').innerHTML = `
    <div class="ord-hint">
      越靠上越先加载，<b>同名文件撞车时排在前面（数字小）的那份生效</b>。
      想让哪个模组说了算，就把它往上拖。拖动行，或点 ↑ ↓ 调整，
      改完点「保存顺序」—— 会按 10、20、30… 写进 <code>mods.settings</code>，
      也就是游戏的加载优先级。
      <br>只有启用中的 ${on} 个模组真正互相影响，禁用的排在哪儿都无所谓。
      这里显示的是<b>真实顺序</b>（禁用的一行不藏起来），主页「全部模组」把禁用的
      排在最后只是看着顺眼，不改这里的数字。
      ${hasMerge ? `<br>合订本（Script Merger 合并出来的那几个文件）固定排在最前，
        不在这里参与排序 —— 它排第二就等于把合并结果作废。` : ''}
    </div>
    <div class="ord-list" id="ordList">${rows}</div>
    <div class="ord-foot">
      <button class="btn btn-primary btn-sm" id="ordSave">保存顺序</button>
      <button class="btn btn-sm" id="ordReset">还原</button>
      <span class="ord-state ${dirty ? 'is-dirty' : ''}">
        ${dirty ? '有改动还没保存 —— 直接关窗会丢' : '改动只在点「保存顺序」之后生效'}
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
    btn.textContent = '保存中…';
    try {
      await api('/api/reorder', { keys: ORD });
      ORD_BASE = ORD.join('|');          // 保存成功，不再算「未保存」
      closeModal(true);
      toast('加载顺序已保存', 3400, { label: '撤销', fn: undoNow });
      await load();
    } catch (err) {
      toast('保存失败：' + err.message, 4000);
      btn.disabled = false;
      btn.textContent = '保存顺序';
    }
  };
  $('#ordReset').onclick = () => {
    if (ordDirty() && !confirm('把顺序还原成打开时的样子？没保存的改动会丢。')) return;
    openOrderDialog();
  };
}

document.addEventListener('DOMContentLoaded', () => {
  const n = $('#navOrder');
  if (n) n.onclick = () => openOrderDialog();
  if (new URLSearchParams(location.search).get('dialog') === 'order') openOrderDialog();
});
