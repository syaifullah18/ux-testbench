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
  UTB.toast = function (msg) {
    var t = document.getElementById('toast');
    if (!t) return;
    t.textContent = msg;
    t.classList.remove('is-hidden');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { t.classList.add('is-hidden'); }, 3200);
  };

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
