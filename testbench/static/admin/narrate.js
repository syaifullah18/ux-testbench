/*
  narrate.js: every sentence the UI builds from data.

  Rules for anything in this file:
  1. Pure functions. Numbers in, string out. No DOM, no network, no randomness, no clock.
  2. Every sentence is a fixed template with slots. If a slot has no value, the sentence is not built.
  3. A sentence states only what the numbers show. No adjectives, no causes, no advice.
  4. A verdict is never stronger than its weakest input.
*/
(function (root) {
  var N = {};
  var MINUS = '−';

  /* ---------- Small helpers ---------- */
  N.list = function (items) {
    if (items.length <= 1) return items.join('');
    return items.slice(0, -1).join(', ') + ' and ' + items[items.length - 1];
  };
  N.cap = function (s) { return s.charAt(0).toUpperCase() + s.slice(1); };
  N.plural = function (n, one, many) { return n + ' ' + (n === 1 ? one : many); };
  N.pct = function (part, whole) { return whole ? Math.round(part / whole * 100) : 0; };
  N.num = function (x, dec) { return Number(x).toFixed(dec || 0).replace('-', MINUS); };
  N.signed = function (x, dec) { var s = Number(x).toFixed(dec || 0); return (x > 0 ? '+' : '') + s.replace('-', MINUS); };
  // Shows a sign on both ends when the interval crosses or sits below zero.
  N.interval = function (low, high, dec) {
    return low < 0 ? N.signed(low, dec) + ' to ' + N.signed(high, dec) : N.num(low, dec) + ' to ' + N.num(high, dec);
  };

  // Two-sided exact sign test: k of n moved the same way, chance is 50/50.
  N.sign = function (k, n) {
    var m = Math.max(k, n - k), c = 1, tail = 0, i;
    for (i = 0; i <= n; i++) { if (i >= m) tail += c; c = c * (n - i) / (i + 1); }
    return Math.min(1, 2 * tail / Math.pow(2, n));
  };

  N.tooFew = function (n) { return n < 5; };

  /* ---------- Verdict from an interval ----------
     low and high are the 95% interval of the benefit of the challenger (positive means better).
     need is the line the challenger has to clear (0 for "no worse", or the gain you set).
     Below the minimum number of finished people the answer is always "unsure". */
  N.verdict = function (o) {
    if (o.n < o.min) return 'unsure';
    if (o.low > o.need) return 'for';
    if (o.high < o.need) return 'against';
    return 'unsure';
  };

  // Why the interval gave that verdict, in one sentence.
  N.intervalNote = function (verdict, need, n, min) {
    if (n < min) return 'Too few people finished to judge.';
    var line = need === 0 ? 'zero' : N.signed(need, need % 1 ? 1 : 0);
    if (verdict === 'for') return 'The whole interval is above ' + line + '.';
    if (verdict === 'against') return 'The whole interval is below ' + line + '.';
    return 'The interval includes ' + line + '.';
  };

  /* ---------- A/B report ---------- */
  var ORDER = ['speed', 'success', 'ease'];

  // Decision table, first match wins:
  //  indicative  n below the minimum
  //  for         all three checks for
  //  open        all three checks unsure
  //  mixed       at least one for and at least one against
  //  against     at least one against, none for
  //  partial     none against, at least one for, the rest unsure
  N.abKind = function (o) {
    var f = 0, a = 0, u = 0;
    if (o.n < o.min) return 'indicative';
    ORDER.forEach(function (k) { var v = o.verdicts[k]; if (v === 'for') f++; else if (v === 'against') a++; else u++; });
    if (f === 3) return 'for';
    if (u === 3) return 'open';
    if (f && a) return 'mixed';
    if (a) return 'against';
    return 'partial';
  };

  N.abOverall = function (o) {
    var kind = N.abKind(o), ch = o.challenger;
    var by = { for: [], against: [], unsure: [] };
    ORDER.forEach(function (k) { by[o.verdicts[k]].push(k); });
    var need = by.unsure.length ? ' ' + N.cap(N.list(by.unsure)) + (by.unsure.length === 1 ? ' needs' : ' need') + ' more data.' : '';
    var supports = function (l) { return N.cap(N.list(l)) + (l.length === 1 ? ' supports ' : ' support '); };
    if (kind === 'indicative') return 'Not enough evidence yet: ' + o.n + ' of ' + o.min + ' people have finished. Read these numbers as a hint, not a result.';
    if (kind === 'for') return 'All three checks support ' + ch + '.';
    if (kind === 'open') return 'Not enough evidence yet on any check.';
    if (kind === 'mixed') return 'Mixed result. ' + supports(by.for) + ch + ', but ' + N.list(by.against) + (by.against.length === 1 ? ' argues' : ' argue') + ' against it.' + need;
    if (kind === 'against') return 'The checks argue against ' + ch + ': ' + N.list(by.against) + '.' + need;
    return 'No clear winner yet. ' + supports(by.for) + ch + '.' + need;
  };

  // The three checks with their numbers, sentences and verdicts. R holds numbers only.
  N.abChecks = function (R, n) {
    var min = R.min, A = R.baseline, B = R.challenger;
    var t = R.time, s = R.success, e = R.ease;
    var tLow = -t.ci[1], tHigh = -t.ci[0];                 // time saved, so bigger is better
    var pA = s.A[0] / s.A[1] * 100, pB = s.B[0] / s.B[1] * 100;
    var sDiff = pB - pA;
    var eDiff = e.B - e.A;
    var p = N.sign(t.fasterB, t.n);
    var vSpeed = N.verdict({ low: tLow, high: tHigh, need: 0, n: n, min: min });
    var vSucc = N.verdict({ low: s.ci[0], high: s.ci[1], need: 0, n: n, min: min });
    var vEase = N.verdict({ low: e.ci[0], high: e.ci[1], need: e.need, n: n, min: min });
    return {
      verdicts: { speed: vSpeed, success: vSucc, ease: vEase },
      rows: [
        { key: 'speed', title: 'Faster', what: 'Total time over all tasks, for each person who tried both.',
          measured: 'Median A <b>' + t.medianA + ' s</b>, B <b>' + t.medianB + ' s</b>. ' + t.fasterB + ' of ' + t.n + ' were faster on B.',
          diff: '<b>' + Math.abs(t.medianA - t.medianB) + ' s ' + (t.medianA > t.medianB ? 'faster' : 'slower') + '</b>',
          detail: '95% interval ' + N.interval(tLow, tHigh, 0) + ' s. Sign test, p = ' + p.toFixed(3) + '. ' + N.intervalNote(vSpeed, 0, n, min), verdict: vSpeed },
        { key: 'success', title: 'At least as successful', what: 'Tasks answered correctly, across all tasks.',
          measured: 'A <b>' + s.A[0] + ' of ' + s.A[1] + '</b> (' + N.pct(s.A[0], s.A[1]) + '%), B <b>' + s.B[0] + ' of ' + s.B[1] + '</b> (' + N.pct(s.B[0], s.B[1]) + '%).',
          diff: '<b>' + N.signed(Math.round(sDiff), 0) + ' points</b>',
          detail: '95% interval ' + N.interval(s.ci[0], s.ci[1], 0) + '. ' + N.intervalNote(vSucc, 0, n, min), verdict: vSucc },
        { key: 'ease', title: 'Rated easier', what: 'Average of “' + e.question + '”, 1 to ' + e.points + '.',
          measured: 'A <b>' + N.num(e.A, 1) + '</b>, B <b>' + N.num(e.B, 1) + '</b>.',
          diff: '<b>' + N.signed(eDiff, 1) + '</b>',
          detail: '95% interval ' + N.interval(e.ci[0], e.ci[1], 1) + '. You asked for at least ' + N.signed(e.need, 1) + '. ' + N.intervalNote(vEase, e.need, n, min), verdict: vEase }
      ]
    };
  };

  // Order effect: only what the medians show.
  N.orderEffect = function (o) {
    var w = o.groups.map(function (g) { return g.A === g.B ? null : (g.A < g.B ? 'A' : 'B'); });
    var same = w.every(function (x) { return x && x === w[0]; });
    if (same) return o.labels[w[0]] + ' is faster ' + (o.groups.length === 2 ? 'in both orders.' : 'in every order.');
    return 'The faster variant differs by order. Read each row.';
  };

  /* ---------- Survey and tree test headlines ---------- */
  function top(counts) {
    var e = Object.keys(counts).map(function (k) { return [k, counts[k]]; }).sort(function (a, b) { return b[1] - a[1] || (a[0] < b[0] ? -1 : 1); });
    if (!e.length || !e[0][1]) return null;
    return { best: e.filter(function (x) { return x[1] === e[0][1]; }).map(function (x) { return x[0]; }), count: e[0][1] };
  }
  function asked(o) { return o.question ? '“' + o.question + '” ' : ''; }
  N.mostCommon = function (o) {
    var t = top(o.counts); if (!t) return null;
    var q = function (x) { return '“' + x + '”'; };
    return asked(o) + (t.best.length === 1
      ? 'Most common answer: ' + q(t.best[0]) + ', ' + t.count + ' of ' + o.of + ' people.'
      : 'Most common answers, tied: ' + N.list(t.best.map(q)) + ', ' + t.count + ' of ' + o.of + ' people each.');
  };
  N.topChoice = function (o) {
    var t = top(o.counts); if (!t) return null;
    return asked(o) + (t.best.length === 1
      ? 'Top priority: ' + t.best[0].toLowerCase() + ', picked by ' + t.count + ' of ' + o.of + ' people.'
      : 'Top priorities, tied: ' + N.list(t.best.map(function (x) { return x.toLowerCase(); })) + ', picked by ' + t.count + ' of ' + o.of + ' people each.');
  };
  N.treeOverall = function (o) {
    var ok = 0, direct = 0, n = 0, weakest = null;
    o.tasks.forEach(function (t) {
      ok += t.ok; direct += t.direct; n += t.n;
      var r = t.ok / t.n;
      if (!weakest || r < weakest.r) weakest = { r: r, title: t.title };
    });
    if (!n) return null;
    var people = Math.max.apply(null, o.tasks.map(function (t) { return t.n; }));
    return 'Across ' + N.plural(o.tasks.length, 'task', 'tasks') + ' and ' + N.plural(people, 'person', 'people') + ': ' + N.pct(ok, n) + '% found the right place, ' + N.pct(direct, n) + '% without going back. Weakest task: ' + weakest.title + ', ' + Math.round(weakest.r * 100) + '%.';
  };

  /* ---------- Drop-off ---------- */
  N.DROPOFF_MIN_PEOPLE = 2;
  N.DROPOFF_MIN_SHARE = 0.2;
  N.dropoff = function (o) {
    var gone = o.started - o.finished;
    if (gone <= 0) return null;
    var head = gone + ' of ' + o.started + ' people who started did not finish';
    var steps = Object.keys(o.stoppedAt || {}).map(function (k) { return [k, o.stoppedAt[k]]; }).sort(function (a, b) { return b[1] - a[1]; });
    var known = steps.reduce(function (s, x) { return s + x[1]; }, 0);
    var detail = null;
    if (steps.length && known === gone) {
      detail = steps.length === 1
        ? (gone === 1 ? 'The person stopped on ' + steps[0][0] + '.' : 'All ' + gone + ' stopped on ' + steps[0][0] + '.')
        : N.cap(N.list(steps.map(function (x) { return x[1] + ' stopped on ' + x[0]; }))) + '.';
    }
    return { gone: gone, headline: head, detail: detail, flag: gone >= N.DROPOFF_MIN_PEOPLE && gone / o.started >= N.DROPOFF_MIN_SHARE };
  };

  N.stoppedLine = function (o) {
    var out = [];
    o.mods.forEach(function (s, i) { if (s.indexOf('stop:') === 0) out.push(o.titles[i] + ' at ' + s.slice(5)); });
    return out.length ? 'Stopped in ' + N.list(out) + '.' : null;
  };

  /* ---------- Card sort, first click and medians ---------- */
  N.median = function (list) {
    if (!list.length) return null;
    var s = list.slice().sort(function (a, b) { return a - b; }), m = s.length >> 1;
    return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2;
  };
  N.AGREE_HIGH = 0.7;
  N.AGREE_LOW = 0.5;
  N.sortAgreement = function (o) {
    var high = 0, low = 0;
    o.cards.forEach(function (c) { var a = Math.max.apply(null, c.counts) / o.n; if (a >= N.AGREE_HIGH) high++; if (a < N.AGREE_LOW) low++; });
    var s = high + ' of ' + o.cards.length + ' cards were put in the same category by at least ' + Math.round(N.AGREE_HIGH * 100) + '% of ' + N.plural(o.n, 'person', 'people') + '.';
    if (low) s += ' ' + N.plural(low, 'card has', 'cards have') + ' no clear home.';
    return s;
  };
  N.firstClickLine = function (o) {
    var med = N.median(o.times);
    return o.inside + ' of ' + N.plural(o.n, 'person', 'people') + ' clicked inside the target area (' + o.target + '). Median time to first click: ' + med.toFixed(1) + ' s.';
  };

  /* ---------- Study lines ---------- */
  N.participantsLine = function (o) {
    if (!o.entered) return 'No participants yet.';
    return N.plural(o.entered, 'participant', 'participants') + ' entered, ' + o.finishedAll + ' finished every module, ' + o.from + ' to ' + o.to + '.';
  };
  N.funnelLine = function (entered) { return 'Out of the ' + N.plural(entered, 'person', 'people') + ' who signed in.'; };
  N.busiest = function (o) {
    var total = 0, max = 0, at = -1;
    o.signups.forEach(function (v, i) { total += v; if (v > max) { max = v; at = i; } });
    if (!total) return 'No signups in the last ' + o.signups.length + ' days.';
    return 'Last ' + o.signups.length + ' days. ' + total + ' in total, busiest day ' + max + ' on ' + (o.day1 + at) + ' ' + o.month + '.';
  };

  /* ---------- Consent wording ---------- */
  N.consentText = function (o) {
    var rec = { ab_test: 'my time, clicks and scrolling on the test pages', first_click: 'where I click and how long I take', tree_test: 'the choices I make and how long I take', card_sort: 'how I group the cards' };
    var seen = {}, items = [];
    (o.types || []).forEach(function (t) { if (rec[t] && !seen[t]) { seen[t] = 1; items.push(rec[t]); } });
    var head = 'I agree that my answers are recorded for this research.' + (items.length ? ' I also agree that these are recorded: ' + items.join('; ') + '.' : '');
    var who = { code: 'My participant code is stored with my answers.', email: 'My email address is stored with my answers.', anonymous: 'No name or contact details are collected unless I offer them.' }[o.identity];
    return who ? head + ' ' + who : head;
  };

  root.Narrate = N;
  if (typeof module !== 'undefined') module.exports = N;
})(typeof window !== 'undefined' ? window : this);
