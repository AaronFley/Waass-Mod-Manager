/* 检查 MOD 更新（内部还叫 track）：本地台账 + 按 ID 抓取资料 + 手动跳转。
   1.2.1 起左侧入口和对话框都改叫「检查 MOD 更新」——
   用户打开它十次有九次是想看「有没有新版本」，不是为了维护台账。
   依赖 app.js 的 api / openModal / esc / toast / load / openNexus / S */

let TRK = null;
let TRK_FILTER = 'all';
let TRK_BUSY = '';
let TRK_GUESS = {};          // 文件夹 → 候选（「可能是这几个」，点一下才对上）
let TRK_GUESS_AT = 0;

async function openTrackDialog(filter = 'all', findKey = '') {
  TRK_FILTER = filter;
  openModal(T('ui.nav_track'), `<div class="upd-loading">${T('track.loading_log')}</div>`, { wide: true });
  try {
    TRK = await api('/api/track');
  } catch (e) {
    $('#modalBody').innerHTML = `<div class="pkg-hint">${T('track.load_failed', { error: esc(e.message) })}</div>`;
    return;
  }
  // 从详情页点「搜一下」进来的：不是让你再找一遍，直接展开这一条的候选
  if (findKey) TRK_FILTER = 'all';
  renderTrack();
  // 有缺 ID 的就顺手把候选备好。不 await：先把有 ID 的那些显示出来，
  // 候选哪天真搜到了再重画一遍。
  if ((TRK.stats?.missing || 0) > 0) loadGuesses();
  if (findKey) {
    const rowEl = $(`#modalBody [data-row="${CSS.escape(findKey)}"]`);
    if (rowEl) {
      rowEl.scrollIntoView({ block: 'nearest' });
      rowEl.classList.add('is-focus');
      searchAndPick(findKey);
    }
  }
}

/* 把「缺 ID 的模组可能是哪几个」取回来。服务端有三天缓存，同一天反复
   打开对话框不会重复打 Nexus；本地下载目录里的原包也能直接给出候选。 */
async function loadGuesses(force = false) {
  if (!force && TRK_GUESS_AT && Date.now() - TRK_GUESS_AT < 60000) return;
  try {
    const r = await api('/api/nexus/guess', force ? { force: true } : {});
    TRK_GUESS = r.items || {};
    TRK_GUESS_AT = Date.now();
    renderTrack();
  } catch (e) {
    /* 联网失败不影响手动「搜一下」，静默即可 */
  }
}

/* 点了候选里的某一个：记下 ID、取资料、下封面 */
async function pickCandidate(key, modId, btn) {
  const old = btn.textContent;
  btn.disabled = true;
  btn.textContent = T('track.binding');
  try {
    await api('/api/nexus/link', { key, mod_id: modId });
    delete TRK_GUESS[key];
    TRK = await api('/api/track');
    renderTrack();
    toast(T('track.linked_fetching'), 2500);
    setTimeout(async () => {
      try {
        TRK = await api('/api/track');
        renderTrack();
        load();
      } catch (e) { /* 下次刷新也会补上 */ }
    }, 2500);
  } catch (e) {
    toast(T('track.bind_failed', { error: e.message }), 4000);
    btn.disabled = false;
    btn.textContent = old;
  }
}

/* 文件夹名 → 能拿去搜的关键词。"modHoodsSwitchableCloak" → "Hoods Switchable Cloak" */
function guessQuery(s) {
  let t = String(s || '').replace(/^mod/i, '');
  t = t.replace(/([a-z0-9])([A-Z])/g, '$1 $2');
  t = t.replace(/([A-Z]+)([A-Z][a-z])/g, '$1 $2');
  t = t.replace(/[_\-.]+/g, ' ').replace(/\s+/g, ' ').trim();
  return t;
}

function renderTrack() {
  const st = TRK?.stats || {};
  const all = TRK?.rows || [];
  const rows = TRK_FILTER === 'new' ? all.filter(r => r.state === 'new')
    : TRK_FILTER === 'ahead' ? all.filter(r => r.state === 'ahead')
    : TRK_FILTER === 'missing' ? all.filter(r => !r.nexus_id)
    : TRK_FILTER === 'unknown' ? all.filter(r => !r.version)
    : all;

  const chip = (id, label, n) =>
    `<button class="trk-chip ${TRK_FILTER === id ? 'is-on' : ''}" data-f="${id}">${label}<b>${n}</b></button>`;

  const list = rows.length ? rows.map(r => {
    const title = r.name_cn || r.name;
    const subBits = [];
    if (r.name_cn) subBits.push(r.name);
    if (r.author) subBits.push(r.author);
    if (r.nexus_id) subBits.push('ID ' + r.nexus_id);
    const sub = subBits.join(' · ');

    const ver = r.version
      ? `<span class="trk-ver">${esc(r.version)}<em>${esc(r.version_cn || '')}</em></span>`
      : `<span class="trk-ver is-none">${T('common.unknown')}</span>`;

    let latest;
    if (r.latest) {
      // Nexus 页面上作者手填的 version 常落后于他传的主文件，两个数不一样时
      // 把一个灰提示挂上，免得你去网页一看数字对不上，怀疑程序算错。
      const pageHint = (r.latest_page && r.latest_page !== r.latest)
        ? ` title="${T('track.page_version_hint', { v: r.latest_page })}"`
        : '';
      const tag = r.latest_cn
        ? `<em class="${r.latest_code === 'fresh' ? 'is-live' : ''}">${esc(r.latest_cn)}</em>`
        : '';
      latest = `<span class="trk-latest ${r.state === 'new' ? 'is-new' : ''}"${pageHint}>${esc(r.latest)}${tag}`
        + (pageHint ? `<b class="trk-srcv">${T('track.src_main_file')}</b>` : '')
        + `</span>`;
    } else {
      latest = '<span class="trk-latest is-none">—</span>';
    }

    const ahead = r.state === 'ahead'
      ? `<div class="trk-ahead">${T('track.ahead_note')}</div>`
      : '';

    // 缺 ID 的模组：有候选就直接摆出来让人点，别逼着他再去点一次「搜一下」。
    // 已标记「不在 Nexus 上」的就不摆候选了 —— 那是噪音。
    const guess = (!r.nexus_id && !r.ignored) ? (TRK_GUESS[r.key] || null) : null;
    const guessHtml = (guess && (guess.items || []).length)
      ? `<div class="trk-guess"><span class="trk-cap">${T('track.guess_cap')}</span>
          ${guess.items.slice(0, 4).map(it => {
            const hint = it.src === 'local' ? T('track.guess_hint_local')
              : it.src === 'both' ? T('track.guess_hint_both') : '';
            return `<button class="trk-gbtn" data-pick="${esc(r.key)}" data-id="${esc(String(it.mod_id))}"
              title="${esc(it.name)}${esc(hint)}">${esc(it.name)}${it.author ? `<em>${esc(it.author)}</em>` : ''}</button>`;
          }).join('')}
        </div>`
      : '';

    const acts = r.nexus_id
      ? `<button class="btn btn-sm" data-open="${esc(r.url)}">${T('track.btn_mod_page')}</button>
         <button class="btn btn-sm btn-ghost" data-open="${esc(r.url)}?tab=files">${T('track.btn_files')}</button>
         <button class="link-btn trk-edit" data-edit="${esc(r.key)}">${T('track.btn_edit_id')}</button>`
      : `<button class="btn btn-sm" data-find="${esc(r.key)}">${T('track.btn_find')}</button>
         <button class="link-btn trk-edit" data-edit="${esc(r.key)}">${T('track.btn_set_id')}</button>
         <button class="link-btn trk-edit" data-mute="${esc(r.key)}" data-on="${r.ignored ? '0' : '1'}"
           title="${r.ignored ? T('track.ignore_off_title') : T('track.ignore_on_title')}">
           ${r.ignored ? T('track.btn_unmute') : T('track.btn_mute')}</button>`;

    const cover = r.cover
      ? `<img class="trk-cover" src="/${esc(r.cover)}" alt="" loading="lazy">`
      : '<div class="trk-cover trk-cover-none"></div>';

    return `<div class="trk-row" data-row="${esc(r.key)}">
      ${cover}
      <div class="trk-main">
        <div class="trk-name">${esc(title)}</div>
        <div class="trk-sub">${esc(sub)}</div>
        ${ahead}
        ${r.ignored ? `<div class="trk-ahead">${T('track.ignored_note')}</div>` : ''}
      </div>
      <div class="trk-vers">
        <span class="trk-cap">${T('track.col_local')}</span>${ver}
        <span class="trk-arrow">→</span>
        <span class="trk-cap">Nexus</span>${latest}
      </div>
      <div class="trk-acts">${acts}</div>
      ${guessHtml}
    </div>`;
  }).join('') : `<div class="pkg-hint" style="margin-top:6px">${T('track.empty_filter')}</div>`;

  const busy = TRK_BUSY
    ? `<span class="trk-busy">${esc(TRK_BUSY)}</span>` : '';

  $('#modalBody').innerHTML = `
    <div class="trk-stats">
      <div class="trk-stat"><b>${st.total || 0}</b><span>${T('track.stat_mods')}</span></div>
      <div class="trk-stat"><b>${st.linked || 0}</b><span>${T('track.stat_linked')}</span></div>
      <div class="trk-stat"><b>${st.with_meta || 0}</b><span>${T('track.stat_meta')}</span></div>
      <div class="trk-stat ${st.new ? 'is-danger' : ''}"><b>${st.new || 0}</b><span>${T('track.state_update')}</span></div>
    </div>

    <div class="trk-toolbar">
      <div class="pkg-hint" style="margin:0">
        ${T('track.hint_column')}
      </div>
      <button class="btn btn-sm" id="trkRefresh" ${TRK_BUSY ? 'disabled' : ''}>${T('track.btn_refresh_meta')}</button>
    </div>

    <div class="trk-chips">
      ${chip('all', T('track.chip_all'), all.length)}
      ${chip('new', T('track.state_update'), all.filter(r => r.state === 'new').length)}
      ${st.ahead ? chip('ahead', T('track.state_ahead'), st.ahead) : ''}
      ${chip('unknown', T('track.state_version_unknown'), all.filter(r => !r.version).length)}
      ${chip('missing', T('track.chip_missing'), all.filter(r => !r.nexus_id).length)}
      ${busy}
    </div>

    <div class="trk-list">${list}</div>

    <div class="pv-foot" style="margin-top:10px;line-height:1.7">
      ${T('track.missing_footer')}
    </div>`;

  bindTrack();
}

function bindTrack() {
  $$('#modalBody .trk-chip').forEach(b => {
    b.onclick = () => { TRK_FILTER = b.dataset.f; renderTrack(); };
  });
  $$('#modalBody [data-open]').forEach(b => { b.onclick = () => openNexus(b.dataset.open); });
  $$('#modalBody [data-edit]').forEach(b => { b.onclick = () => editTrackId(b.dataset.edit); });
  $$('#modalBody [data-pick]').forEach(b => {
    b.onclick = () => pickCandidate(b.dataset.pick, b.dataset.id, b);
  });
  $$('#modalBody [data-mute]').forEach(b => {
    b.onclick = () => toggleIgnore(b.dataset.mute, b.dataset.on === '1');
  });
  $$('#modalBody [data-find]').forEach(b => { b.onclick = () => searchAndPick(b.dataset.find); });
  const r = $('#trkRefresh');
  if (r) r.onclick = () => refreshAll(r);
}

/* 刷新全部：一次批量请求，把过期和缺失的都补上 */
async function refreshAll(btn) {
  btn.disabled = true;
  btn.textContent = T('track.checking');
  TRK_BUSY = T('track.busy_fetch');
  const keep = $('#modalBody .trk-chips');
  if (keep) keep.insertAdjacentHTML('beforeend', `<span class="trk-busy">${TRK_BUSY}</span>`);
  try {
    const res = await api('/api/nexus/refresh', { force: true, cover: true });
    TRK = await api('/api/track');
    TRK_BUSY = '';
    if (res.error && !res.got) {
      toast(T('track.query_failed', { error: res.error }), 5000);
    } else if (!res.got && !res.requested) {
      // 一个 ID 都没有时，说「资料都是新的」是骗人的 —— 实情是没东西可查。
      // 2026-10-30 的反馈就是这个：用户点了刷新，看到「都是新的」，
      // 以为同步坏了查不出来。
      const miss = TRK?.stats?.missing || 0;
      toast(miss
        ? TN('track.nothing_missing_one', 'track.nothing_missing_many', miss)
        : T('track.all_fresh'), 6000);
    } else {
      toast(res.got
        ? (res.covers
            ? TN('track.refreshed_covers_one', 'track.refreshed_covers_many', res.got, { m: res.covers })
            : TN('track.refreshed_one', 'track.refreshed_many', res.got))
        : T('track.all_fresh'), 4000);
    }
    renderTrack();
    if (TRK?.stats?.missing) loadGuesses(true);   // 顺手把候选也刷一遍
    load();
  } catch (e) {
    TRK_BUSY = '';
    btn.disabled = false;
    btn.textContent = T('track.btn_refresh_meta');
    toast(T('track.refresh_failed', { error: e.message }), 5000);
  }
}

/* 标/取消「这个不在 Nexus 上」。自制、移植、合订本本来就搜不到，
   不标的话「还有 N 个没对上 Nexus」会永远挂着它们，提示条再也消不掉。 */
async function toggleIgnore(key, on) {
  try {
    await api('/api/track', { action: 'ignore', key, on });
    TRK = await api('/api/track');
    renderTrack();
    load();
    toast(on ? T('track.muted_done') : T('track.unmuted_done'), 2500);
  } catch (e) {
    toast(T('track.mute_failed', { error: e.message }), 4000);
  }
}

/* 缺 ID 的模组：拿名字去搜，列出候选让人挑 */
async function searchAndPick(key) {
  const row = TRK.rows.find(r => r.key === key) || {};
  const rowEl = $(`#modalBody [data-row="${CSS.escape(key)}"]`);
  if (!rowEl) return;
  const q = guessQuery(row.name_nexus || row.name || key);
  rowEl.querySelector('.trk-acts').innerHTML = `<span class="trk-busy">${T('track.searching')}</span>`;

  let res;
  try {
    res = await api('/api/nexus/search?q=' + encodeURIComponent(q));
  } catch (e) {
    res = { ok: false, error: e.message, items: [] };
  }
  if (!res.ok || !res.items?.length) {
    rowEl.querySelector('.trk-acts').innerHTML = `
      <span class="trk-none">${res.error ? esc(res.error) : T('track.not_found')}</span>
      <button class="btn btn-sm" data-open="${esc(row.search || '')}">${T('track.btn_search_self')}</button>
      <button class="link-btn" data-edit="${esc(key)}">${T('track.btn_set_id')}</button>`;
    bindTrack();
    return;
  }

  const cands = res.items.map(it => `
    <button class="cand" data-pick="${it.mod_id}">
      <img class="cand-img" src="${esc(it.thumb || it.picture || '')}" alt="">
      <span class="cand-body">
        <span class="cand-name">${esc(it.name)}</span>
        <span class="cand-sub">${esc(it.author || '')}${it.version ? ' · v' + esc(it.version) : ''}${
          it.downloads ? ` · ${T('track.downloads_n', { n: Number(it.downloads).toLocaleString() })}` : ''}</span>
      </span>
    </button>`).join('');

  const box = document.createElement('div');
  box.className = 'cand-box';
  box.innerHTML = `
    <div class="cand-head">
      ${T('track.pick_header', { q: esc(q), n: res.items.length })}
      <button class="link-btn" id="candCancel">${T('common.cancel')}</button>
    </div>
    <div class="cand-list">${cands}</div>
  `;
  rowEl.after(box);
  rowEl.querySelector('.trk-acts').innerHTML = `<span class="trk-busy">${T('track.await_pick')}</span>`;

  $('#candCancel').onclick = () => renderTrack();
  $$('.cand', box).forEach(b => {
    b.onclick = async () => {
      b.classList.add('is-busy');
      try {
        const r = await api('/api/nexus/link', { key, mod_id: b.dataset.pick, cover: true });
        toast(T('track.linked_ok', { name: r.meta?.name || b.dataset.pick }), 3500);
        TRK = await api('/api/track');
        renderTrack();
        load();
      } catch (e) {
        toast(T('track.attach_failed', { error: e.message }), 4500);
        b.classList.remove('is-busy');
      }
    };
  });
}

/* 就地填 / 改 Nexus ID。接受纯数字，也接受整条网址。 */
function editTrackId(key) {
  const row = TRK.rows.find(r => r.key === key) || {};
  const rowEl = $(`#modalBody [data-row="${CSS.escape(key)}"]`);
  if (!rowEl) return;
  const cur = row.nexus_id || '';
  rowEl.querySelector('.trk-acts').innerHTML = `
    <input class="trk-in" id="trkIn" value="${esc(cur)}" placeholder="${T('track.id_placeholder')}" spellcheck="false">
    <button class="btn btn-sm btn-primary" id="trkOk">${T('common.save')}</button>
    <button class="link-btn" id="trkCancel">${T('common.cancel')}</button>`;

  const input = $('#trkIn');
  input.focus();
  input.select();

  const save = async () => {
    const v = input.value.trim();
    if (!v) return toast(T('track.need_id'));
    try {
      await api('/api/track', { action: 'set', key, ref: v });
      toast(T('track.id_saved', { name: row.name_cn || row.name }), 2500);
      TRK = await api('/api/track');
      renderTrack();
      load();
      api('/api/nexus/refresh', { ids: [row.nexus_id || v], cover: true })
        .then(() => api('/api/track'))
        .then(t => { TRK = t; renderTrack(); load(); })
        .catch(() => {});
    } catch (e) {
      toast(T('track.save_failed', { error: e.message }), 4000);
    }
  };

  $('#trkOk').onclick = save;
  $('#trkCancel').onclick = () => renderTrack();
  input.onkeydown = e => {
    if (e.key === 'Enter') save();
    if (e.key === 'Escape') renderTrack();
  };
}

document.addEventListener('DOMContentLoaded', () => {
  const n = $('#navTrack');
  if (n) n.onclick = () => openTrackDialog();
  const chip = $('#btnUpdates');
  if (chip) chip.onclick = () => openTrackDialog('new');
  const qs = new URLSearchParams(location.search);
  const q = qs.get('dialog');
  if (q === 'updates' || q === 'track') openTrackDialog('all', qs.get('find') || '');
});
