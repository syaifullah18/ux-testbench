// Runs timed tasks on top of a prototype loaded in a same-origin iframe.
// The prototype markup is never modified: listeners are attached to the frame's document
// after it loads, so any static HTML prototype can be dropped into a project as is.
(function () {
    const cfg = window.TASKS, S = cfg.strings;
    const $ = (id) => document.getElementById(id);
    const fmt = (s, vars) => s.replace(/\{(\w+)\}/g, (_, k) => (k in vars ? vars[k] : ''));
    const frame = $('proto'), gate = $('gate'), dlg = $('dlg');
    const REVERSAL_MIN_PX = 80;     // ignore jitter: a reversal needs this much travel first
    const FLUSH_EVERY_MS = 15000;
    const PATH_MAX = 40;
    const EVENTS_MAX = 200;
    const CONTROL = '[data-testbench-label],a,button,select,input,textarea,summary,label,[role=button],[data-dropdown-toggle],[data-collapse-toggle]';

    let idx = cfg.tasks.findIndex((t) => !t.done);
    if (idx < 0) { location.reload(); return; }

    let m = null;           // metrics of the active task
    let active = false;     // timer running and interactions counted
    let since = null;
    let gaveUp = false, answer = {};

    const task = () => cfg.tasks[idx];
    const url = (tpl) => tpl.replace('__T__', encodeURIComponent(task().id));

    // Static labels
    $('btn-start').querySelector('span').textContent = S.start;
    $('btn-answer').querySelector('span').textContent = S.answer;
    $('btn-toggle').textContent = S.hide;
    $('d-title').textContent = S.your_answer;
    $('btn-submit').textContent = S.submit;
    $('btn-giveup').textContent = S.gave_up;
    $('btn-back').textContent = S.back;
    $('e-title').textContent = S.ease_q;
    $('e-scale').textContent = `1 = ${S.ease_low}, 7 = ${S.ease_high}`;

    function settle() {
        if (since !== null) {
            m.time_ms += Math.round(performance.now() - since);
            since = null;
        }
        if (active && !document.hidden) since = performance.now();
    }

    function snapshot() {
        settle();
        return { time_ms: m.time_ms, clicks: m.clicks, scroll_reversals: m.scroll_reversals,
                 click_path: m.click_path, events: m.events, viewport_w: window.innerWidth };
    }

    function flush(beacon) {
        if (!m) return;
        const body = JSON.stringify(snapshot());
        fetch(url(cfg.metricsUrl), { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-CSRFToken': cfg.csrfToken }, body, keepalive: true }).catch(() => {});
    }

    function describe(el) {
        const target = el.closest(CONTROL) || el;
        const tag = target.tagName.toLowerCase();
        // Never label a text field by what the participant typed into it.
        const typed = (tag === 'input' && !['button', 'submit', 'reset', 'checkbox', 'radio'].includes(target.type)) || tag === 'textarea';
        // A stable label set by the prototype author wins, so first-click targets survive copy edits.
        const text = (target.getAttribute('data-testbench-label') || target.getAttribute('aria-label') || (typed ? target.placeholder || target.name : target.innerText || target.value) || '')
            .replace(/\s+/g, ' ').trim();
        return (text ? `${tag}: ${text}` : `${tag}${target.id ? '#' + target.id : ''}`).slice(0, 80);
    }

    function logStep(label) {
        if (m.click_path.length < PATH_MAX) m.click_path.push(label);
    }

    // A prototype reports a goal (saved, submitted, approved...) either declaratively, by a click
    // on an element with data-testbench-goal="<id>", or from its own script with
    // parent.postMessage({ testbench: 'goal', id: '<id>' }, '*'). Messages count only when they
    // come from the prototype frame itself, whatever its origin.
    function logEvent(kind, value) {
        value = String(value || '').slice(0, 80);
        if (!active || !value) return false;
        settle();
        if (m.events.length < EVENTS_MAX) m.events.push({ kind, value, t_ms: m.time_ms });
        return true;
    }

    // Pages and in-page states (hash) the participant passes through, for back-navigation and
    // lostness. Repeats of the same page in a row are one visit.
    function logPage() {
        if (!m || !active) return;   // the frame is blanked between tasks, which fires load too
        let key;
        try {
            const loc = frame.contentWindow.location;
            key = (loc.pathname.match(/\/view\/\d+\/(.*)$/) || [, ''])[1] || 'index';
            key += loc.hash;
        } catch (err) { return; }   // a cross-origin frame cannot be read; its script reports instead
        const pages = m.events.filter((e) => e.kind === 'page');
        if (!pages.length || pages[pages.length - 1].value !== key.slice(0, 80)) logEvent('page', key);
    }

    function reachGoal(id) {
        id = String(id || '').slice(0, 80);
        if (!logEvent('goal', id)) return;
        const t = task();
        if (t.end_on_goal && t.goals.includes(id)) {
            active = false;       // the task ends at the goal, not when the participant moves on
            since = null;
            // With fields still to answer, save the stopped time now; otherwise the submit that
            // follows carries it (a separate flush would race it and hit a closed view).
            if (t.fields.length) flush(false);
            openAnswer(true);
        }
    }
    window.addEventListener('message', (e) => {
        const d = e.data;
        if (e.source !== frame.contentWindow || !d) return;
        if (d.testbench === 'goal' && typeof d.id === 'string') reachGoal(d.id);
        // A validation error the prototype shows: parent.postMessage({ testbench: 'error', id: 'npwp-format' }, '*')
        if (d.testbench === 'error') logEvent('error', typeof d.id === 'string' && d.id ? d.id : 'error');
        if (d.testbench === 'page' && typeof d.id === 'string') logEvent('page', d.id);
    });

    function wireFrame() {
        const doc = frame.contentDocument, win = frame.contentWindow;
        if (!doc) return;
        doc.addEventListener('click', (e) => {
            // Keep the participant inside the prototype: links that would leave it do nothing.
            const a = e.target.closest && e.target.closest('a[href]');
            if (a && !a.getAttribute('href').startsWith('#')) e.preventDefault();
            if (!active) return;
            m.clicks += 1;
            logStep(describe(e.target));
            if (!(e.target.closest && e.target.closest(CONTROL))) logEvent('miss', e.target.tagName.toLowerCase());
            const g = e.target.closest && e.target.closest('[data-testbench-goal]');
            if (g) reachGoal(g.getAttribute('data-testbench-goal'));
        }, true);
        doc.addEventListener('submit', (e) => e.preventDefault(), true);
        doc.addEventListener('change', (e) => {
            if (active && e.target.tagName === 'SELECT') logStep(`select: ${e.target.value}`.slice(0, 80));
        }, true);
        let lastY = win.scrollY, dir = 0, travel = 0;
        win.addEventListener('scroll', () => {
            const y = win.scrollY, d = Math.sign(y - lastY);
            if (d && d !== dir) {
                if (dir && travel >= REVERSAL_MIN_PX && active) m.scroll_reversals += 1;
                dir = d; travel = 0;
            }
            travel += Math.abs(y - lastY);
            lastY = y;
        }, { passive: true });
        win.addEventListener('hashchange', logPage);
        // A field the prototype marks aria-invalid="true" counts as a validation error, so
        // prototypes that follow accessible error patterns need no extra script.
        if (win.MutationObserver) {
            new win.MutationObserver((records) => {
                records.forEach((r) => {
                    if (r.target.getAttribute('aria-invalid') === 'true' && r.oldValue !== 'true') {
                        logEvent('error', r.target.getAttribute('data-testbench-label') || r.target.name || r.target.id || 'aria-invalid');
                    }
                });
            }).observe(doc.documentElement, { attributes: true, attributeFilter: ['aria-invalid'], attributeOldValue: true, subtree: true });
        }
    }

    function showGate() {
        const t = task(), count = fmt(S.task_of, { n: idx + 1, total: cfg.tasks.length });
        active = false; since = null;
        $('t-count').textContent = $('g-count').textContent = count;
        $('g-title').textContent = t.title;
        $('g-prompt').textContent = $('t-prompt').textContent = $('d-prompt').textContent = t.prompt;
        ['btn-answer', 'btn-toggle', 't-prompt'].forEach((id) => $(id).classList.add('hidden'));
        frame.removeAttribute('src');
        gate.classList.remove('hidden');
        $('btn-start').focus();
    }

    // Every task starts from a fresh load at the top of the page, so earlier tasks never leave
    // filters or scroll position behind. The timer starts once the page has loaded, which keeps
    // CDN load time (heavier for some variants) out of the measurement.
    $('btn-start').addEventListener('click', () => {
        const init = task().initial || {};
        m = { time_ms: init.time_ms || 0, clicks: init.clicks || 0, scroll_reversals: init.scroll_reversals || 0, click_path: [],
              events: (init.events || []).slice() };
        gate.classList.add('hidden');
        let started = false;
        frame.onload = () => {
            wireFrame();          // every page the prototype navigates to needs its listeners
            if (started) { logPage(); return; }   // never restart a timer a goal has stopped
            started = true;
            active = true;
            settle();
            logPage();
            ['btn-answer', 'btn-toggle', 't-prompt'].forEach((id) => $(id).classList.remove('hidden'));
            // A task the prototype ends by a goal has nothing to type: the button only offers a way out.
            $('btn-answer').querySelector('span').textContent = task().fields.length ? S.answer : S.stuck;
            $('btn-answer').querySelector('i').className = task().fields.length ? 'fa-solid fa-pen' : 'fa-solid fa-flag';
            $('btn-answer').classList.toggle('btn-primary', !!task().fields.length);
            $('btn-answer').classList.toggle('btn-secondary', !task().fields.length);
            $('btn-toggle').textContent = S.hide;
            $('btn-toggle').setAttribute('aria-expanded', 'true');
        };
        frame.src = (task().frameUrl || cfg.frameUrl) + '?t=' + (idx + 1) + '&_=' + Date.now();
    });

    $('btn-toggle').addEventListener('click', (e) => {
        const hidden = $('t-prompt').classList.toggle('hidden');
        e.currentTarget.textContent = hidden ? S.show : S.hide;
        e.currentTarget.setAttribute('aria-expanded', String(!hidden));
    });

    // ------------------------------------------------ answer dialog

    function buildFields() {
        const box = $('d-fields');
        box.innerHTML = '';
        task().fields.forEach((f) => {
            const wrap = document.createElement('div');
            if (f.kind === 'choice') {
                const p = document.createElement('p'), list = document.createElement('div');
                p.className = 'text-sm font-semibold text-ink mb-2';
                p.textContent = f.label;
                list.className = 'space-y-2';
                f.options.forEach((opt) => {
                    const l = document.createElement('label'), r = document.createElement('input');
                    l.className = 'flex items-center gap-2.5 px-3.5 py-2.5 border border-line rounded-lg cursor-pointer text-sm has-[:checked]:border-brand has-[:checked]:bg-brand/10';
                    Object.assign(r, { type: 'radio', name: f.id, value: opt, className: 'w-4 h-4 text-brand border-ink-subtle focus:ring-0' });
                    l.append(r, document.createTextNode(opt));
                    list.appendChild(l);
                });
                wrap.append(p, list);
            } else {
                const l = document.createElement('label'), i = document.createElement('input');
                l.className = 'label';
                l.textContent = f.label;
                l.htmlFor = 'f-' + f.id;
                Object.assign(i, { type: 'text', id: 'f-' + f.id, name: f.id, placeholder: f.placeholder || '', autocomplete: 'off',
                    className: 'input' });
                wrap.append(l, i);
            }
            box.appendChild(wrap);
        });
    }

    function readFields() {
        const form = $('f-answer'), out = {};
        task().fields.forEach((f) => {
            out[f.id] = f.kind === 'choice'
                ? ((form.querySelector(`input[name="${f.id}"]:checked`) || {}).value || '')
                : (form.elements[f.id].value || '').trim();
        });
        return out;
    }

    function showError(id, msg) {
        $(id).textContent = msg || '';
        $(id).classList.toggle('hidden', !msg);
    }

    // atGoal: the prototype ended the task, so there is no going back to it or giving up.
    function openAnswer(atGoal) {
        buildFields();
        showError('d-error');
        const noFields = !task().fields.length;
        $('d-title').textContent = noFields && !atGoal ? S.stuck_title : S.your_answer;
        $('btn-submit').classList.toggle('hidden', noFields);
        $('btn-giveup').textContent = noFields ? S.stuck_confirm : S.gave_up;
        $('f-answer').classList.remove('hidden');
        $('ease').classList.add('hidden');
        ['btn-back', 'btn-giveup'].forEach((id) => $(id).classList.toggle('hidden', atGoal));
        if (!dlg.open) dlg.showModal();
        if (atGoal && !task().fields.length) { finishSearching(false); return; }
        const first = $('d-fields').querySelector('input');
        if (first) first.focus();
    }
    $('btn-answer').addEventListener('click', () => openAnswer(false));
    $('btn-back').addEventListener('click', () => dlg.close());
    dlg.addEventListener('cancel', (e) => { if (!active) e.preventDefault(); });

    function finishSearching(isGiveUp) {
        answer = readFields();
        gaveUp = isGiveUp;
        if (!gaveUp && Object.values(answer).some((v) => !v)) {
            showError('d-error', S.incomplete);
            return;
        }
        settle();
        active = false;   // time on task ends here; rating the task is not part of it
        since = null;
        if (!cfg.ease) { submit(0); return; }
        $('f-answer').classList.add('hidden');
        buildEase();
        $('ease').classList.remove('hidden');
    }
    $('f-answer').addEventListener('submit', (e) => { e.preventDefault(); finishSearching(false); });
    $('btn-giveup').addEventListener('click', () => finishSearching(true));

    function buildEase() {
        const box = $('ease-buttons');
        box.innerHTML = '';
        showError('ease-error');
        for (let i = 1; i <= 7; i++) {
            const b = document.createElement('button');
            b.type = 'button';
            b.textContent = i;
            b.className = 'h-11 rounded-lg border border-line font-bold text-ink hover:border-brand hover:bg-brand/10 focus:ring-4 focus:ring-brand/30';
            b.addEventListener('click', () => submit(i));
            box.appendChild(b);
        }
    }

    let sending = false;
    async function submit(ease) {
        if (sending) return;
        sending = true;
        const errBox = cfg.ease ? 'ease-error' : 'd-error';
        try {
            const res = await fetch(url(cfg.submitUrl), {
                method: 'POST', headers: { 'Content-Type': 'application/json', 'X-CSRFToken': cfg.csrfToken },
                body: JSON.stringify({ answer, gave_up: gaveUp, ease, metrics: snapshot() }),
            });
            const data = await res.json().catch(() => ({}));
            if (!res.ok) {
                if (res.status === 409) { location.reload(); return; }
                showError(errBox, data.error || S.save_failed);
                return;
            }
            m = null;
            if (data.next) { location.href = data.next; return; }
            dlg.close();
            task().done = true;
            idx += 1;
            showGate();
        } catch (err) {
            showError(errBox, S.offline);
        } finally {
            sending = false;
        }
    }

    // ------------------------------------------------ lifecycle

    document.addEventListener('visibilitychange', () => {
        if (!m) return;
        settle();
        if (document.hidden) flush(true);
    });
    window.addEventListener('pagehide', () => flush(true));
    setInterval(() => { if (active) flush(false); }, FLUSH_EVERY_MS);

    showGate();
})();
