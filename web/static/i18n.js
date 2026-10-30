/* 界面文案：中英双语。
   键表由服务端渲染首页时内联进 window.__WAASS_I18N__，读的是 web/i18n/*.json
   —— 和后端 core/i18n.py 同一份文件，所以不会出现「界面英文、提示弹中文」。

   用法：
     T('ui.import')                       取一句
     T('ui.upd_pill', { n: 3 })           带占位符，{n} 会被换掉
     TN('mods.one', 'mods.many', n)       单复数（中文两份一样，英文按 n 选）
   静态文本在 HTML 里挂 data-i18n="键"，本文件加载时统一填一遍。 */

const I18N = window.__WAASS_I18N__ || { lang: 'zh', strings: {} };
const I18N_STR = I18N.strings || {};
const I18N_LANG = I18N.lang || 'zh';

function T(key, params) {
  let s = I18N_STR[key];
  if (s === undefined) return key;      // 漏翻时直接露键名，好定位；测试会抓
  if (params) {
    for (const k in params) {
      if (Object.prototype.hasOwnProperty.call(params, k)) {
        s = s.split('{' + k + '}').join(String(params[k]));
      }
    }
  }
  return s;
}

/* 单复数只分「1」和「其它」两档 —— 本程序的可数量词（模组 / 文件 / 处）
   英文都只有这两档。 */
function TN(oneKey, manyKey, n, params) {
  return T(Number(n) === 1 ? oneKey : manyKey, Object.assign({ n }, params || {}));
}

/* 静态文案落位。四种挂法，按元素类型挑一个用：
   data-i18n        → textContent
   data-i18n-html   → innerHTML（句子里要加 <b> 这类标签时用）
   data-i18n-title  → title
   data-i18n-ph     → placeholder
   data-i18n-aria   → aria-label */
function applyStatic(root) {
  const box = root || document;
  box.querySelectorAll('[data-i18n]').forEach(el => {
    el.textContent = T(el.dataset.i18n);
  });
  box.querySelectorAll('[data-i18n-html]').forEach(el => {
    el.innerHTML = T(el.dataset.i18nHtml);
  });
  box.querySelectorAll('[data-i18n-title]').forEach(el => {
    el.title = T(el.dataset.i18nTitle);
  });
  box.querySelectorAll('[data-i18n-ph]').forEach(el => {
    el.placeholder = T(el.dataset.i18nPh);
  });
  box.querySelectorAll('[data-i18n-aria]').forEach(el => {
    el.setAttribute('aria-label', T(el.dataset.i18nAria));
  });
}

/* 切语言 = 存一次配置 + 整页重载。
   不做无刷新热切换：那样每个面板都得重写「重新渲染」逻辑，漏一个就会中英混排，
   而重载只要 0.3 秒，且保证没有一个角落被漏掉。 */
function switchLang(next) {
  return fetch('/api/config', {
    method: 'POST',
    headers: { 'X-Waass-Token': window.__WAASS_TOKEN__, 'Content-Type': 'application/json' },
    body: JSON.stringify({ lang: next }),
  }).then(() => location.reload());
}

function bindLangButton() {
  const btn = document.getElementById('btnLang');
  if (!btn) return;
  // 按钮上写的是「点了会变成什么」：中文界面显示 EN，英文界面显示「中」。
  btn.textContent = I18N_LANG === 'zh' ? 'EN' : '中';
  btn.title = T('ui.lang_switch');
  btn.addEventListener('click', () => {
    btn.disabled = true;
    switchLang(I18N_LANG === 'zh' ? 'en' : 'zh');
  });
}

applyStatic();
bindLangButton();
