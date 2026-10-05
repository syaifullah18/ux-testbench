// UX Testbench Admin interactive behavior
(function () {
  var UTB = window.UTB || {};
  window.UTB = UTB;

  // Preloader removal
  function hidePreloader() {
    var p = document.getElementById('preloader');
    if (!p || p.dataset.hiding) return;
    p.dataset.hiding = '1';
    setTimeout(function () {
      p.classList.add('pl-out');
      setTimeout(function () { if (p.parentNode) p.parentNode.removeChild(p); }, 300);
    }, 200);
  }
  document.addEventListener('DOMContentLoaded', hidePreloader);
  setTimeout(hidePreloader, 1200);

  // Toast
  var toastTimer;
  UTB.toast = function (msg, category) {
    var t = document.getElementById('toast');
    if (!t) return;
    var isErr = category === 'error';
    t.className = 'pointer-events-auto flex items-center gap-2.5 rounded-lg px-4 py-3 text-sm font-semibold text-white shadow-xl transition-all duration-300 cursor-pointer ' + (isErr ? 'bg-[#B42318]' : 'bg-ink');
    var iconClass = isErr ? 'fa-solid fa-circle-exclamation text-amber-300' : 'fa-solid fa-circle-check text-emerald-400';
    t.innerHTML = '<i class="' + iconClass + '" aria-hidden="true"></i><span>' + msg + '</span>';
    t.style.display = 'flex';
    t.style.opacity = '1';
    t.style.transform = 'translateY(0)';
    t.classList.remove('is-hidden', 'hidden');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () {
      t.style.opacity = '0';
      t.style.transform = 'translateY(8px)';
      setTimeout(function () {
        t.style.display = 'none';
        t.classList.add('is-hidden', 'hidden');
        t.style.opacity = '';
        t.style.transform = '';
      }, 350);
    }, 3500);
  };

  // Auto-dismiss server flash toasts & click-to-dismiss
  function initToastDismiss() {
    var items = document.querySelectorAll('#toast-wrap .toast-item');
    if (items.length) {
      setTimeout(function () {
        items.forEach(function (el) {
          el.style.opacity = '0';
          el.style.transform = 'translateY(8px)';
          setTimeout(function () {
            if (el.parentNode) el.parentNode.removeChild(el);
          }, 350);
        });
      }, 4000);
    }
  }
  document.addEventListener('DOMContentLoaded', initToastDismiss);

  document.addEventListener('click', function (e) {
    var item = e.target.closest('#toast-wrap .toast-item, #toast-wrap #toast');
    if (item && !item.classList.contains('is-hidden') && !item.classList.contains('hidden') && item.style.display !== 'none') {
      item.style.opacity = '0';
      item.style.transform = 'translateY(8px)';
      setTimeout(function () {
        if (item.id === 'toast') {
          item.style.display = 'none';
          item.classList.add('is-hidden', 'hidden');
          item.style.opacity = '';
          item.style.transform = '';
        } else if (item.parentNode) {
          item.parentNode.removeChild(item);
        }
      }, 300);
    }
  });

  // Account menu in the top bar
  document.addEventListener('click', function (e) {
    var btn = document.getElementById('acct-btn');
    var menu = document.getElementById('acct-menu');
    if (!btn || !menu) return;
    var open = btn.contains(e.target) ? menu.classList.contains('hidden') : false;
    if (!open && menu.contains(e.target)) return;
    menu.classList.toggle('hidden', !open);
    btn.setAttribute('aria-expanded', open ? 'true' : 'false');
  });
  document.addEventListener('keydown', function (e) {
    var menu = document.getElementById('acct-menu');
    if (e.key === 'Escape' && menu && !menu.classList.contains('hidden')) {
      menu.classList.add('hidden');
      document.getElementById('acct-btn').setAttribute('aria-expanded', 'false');
    }
  });

  // Clipboard copy
  document.addEventListener('click', function (e) {
    var btn = e.target.closest('[data-copy]');
    if (!btn) return;
    var text = btn.getAttribute('data-copy');
    if (!text) return;
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(function () {
        UTB.toast('Copied to clipboard');
      }).catch(function () {
        prompt('Copy this link:', text);
      });
    } else {
      prompt('Copy this link:', text);
    }
  });

  // Color luminance and contrast helpers
  function lum(hex) {
    var n = parseInt(hex.slice(1), 16);
    var c = [(n >> 16) & 255, (n >> 8) & 255, n & 255].map(function (v) {
      v /= 255;
      return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4);
    });
    return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2];
  }
  UTB.contrast = function (a, b) {
    var x = lum(a), y = lum(b);
    return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05);
  };
  UTB.onBrand = function (hex) {
    return UTB.contrast(hex, '#FFFFFF') >= UTB.contrast(hex, '#151A23') ? '#FFFFFF' : '#151A23';
  };
  UTB.setBrand = function (hex) {
    if (!/^#[0-9a-fA-F]{6}$/.test(hex)) return;
    document.documentElement.style.setProperty('--brand', hex);
    document.documentElement.style.setProperty('--on-brand', UTB.onBrand(hex));
  };
})();
