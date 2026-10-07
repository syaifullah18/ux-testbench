# Backlog progress (ux-testbench-improvements.md)

Order follows the revised plan: 1, 2, 3 + 9-lite, 4, 5, 8, 6, 7, 10.
Status: `done` (built and tested), `open` (not started), `bug` (found, not fixed yet), `fixed`.

## 1. Goal detection — done

- `task_events` table (one JSON list per session/position/task), schema version 3, added to `migrate.py` order and `storage.reset`.
- Config: `goals: {success: [...], failure: [...]}`, `end_on_goal` (default true). Validated: mapping shape, ids `[A-Za-z0-9_.:-]{1,80}`, success required, no id in both lists. A goal task with no `fields` asks for no typed answer.
- Runner: `data-testbench-goal` click and `postMessage({testbench:"goal", id})` accepted only when `e.source === frame.contentWindow`. With `end_on_goal`, the timer stops at the goal and the dialog opens without Back/Give up.
- Server: events cleaned (known kinds, ≤100, ≤80 chars), longer list wins. Grading: goals only, accept only, or both (both must pass). With `end_on_goal`, stored `time_ms` = time at the first listed goal.
- CSV: `goal_outcome`, `goal_events` (`id@seconds`). Studio keeps `goals`, `end_on_goal`, `first_click` (passthrough) and writes `fields: []` for goal-only tasks.
- Docs: `docs/configuration.md`.
- Tests: `tests/test_goals.py` (10 tests). Full suite: 275 passed, 3 skipped.

## Bugs found

- fixed: the test suite used the developer's real S3/R2 bucket. `testbench` loads `.env` on import, so `STORAGE_BACKEND=s3` reached every test; exports were uploaded to the real bucket and the suite took more than 10 minutes. `tests/conftest.py` now clears `STORAGE_BACKEND`/`S3_*` per test. Suite now takes ~21 s, and `test_all_submits` (failing before) passes.
- fixed: the runner had never run in a browser. `tests/test_browser_runner.py` (Playwright Chromium, skipped if not installed) now drives the real app. It confirmed goals, the timer stopping at the goal, pages, misclicks, aria-invalid errors, labels, postMessage goals and journey steps, and found two bugs, both fixed: (1) a JS error `null.events` (blanking the frame between tasks fired `onload` → `logPage()` after the metrics were cleared); (2) a metrics flush at the goal raced the submit and got a 409.
- fixed: `python -m testbench check` hung 30 s then crashed when `DATABASE_URL` pointed to an unreachable PostgreSQL (introduced by the lock check). It now warns and continues.
- note: a draft study only admits the logged-in researcher, so in practice "pilot" = the researcher's own runs before going live. Existing live studies stay unlocked until someone presses "Lock rules now" or sets the status to live again.

## 2. First-click targets per variant — done

- Config `first_click: {variant: [labels]}`, validated against variant keys (non-empty lists).
- Matching: the recorded `<tag>: <label>` loses its tag prefix, then the same `norm()` as answers is applied. The runner now prefers `data-testbench-label` over `aria-label` and text.
- Report: "N% on target" plus the top wrong first clicks per task/variant; paired check "Right first click more often" (exact sign test). It is shown with the rule but excluded from `all_ok` (`counts: False`).
- CSV: `first_click_label`, `first_click_correct`. Docs updated.
- Tests: `tests/test_first_click_targets.py` (4). Full suite: 279 passed, 3 skipped.
- Deferred to item 4: `check` warning when a target label is not in the variant HTML.
## 3. Lock pre-registered rules — done

- `testbench/locks.py`: snapshot of variants (relative files), baseline, order, task ids/accept/contains/goals/end_on_goal/first_click, and decision_rule. Stored in the study DB `meta` table (`lock:<module>`), with `lock_rules`/`unlock_rules` audit entries.
- Locks are written when status is set to live (`admin.set_status`), and from "Lock rules now" on the report.
- Studio: `trial()` adds a problem for any change to a locked field, so every Studio write path (editor, module form, settings, delete) refuses it. Wording changes still save.
- `python -m testbench check`: exits 1 on drift. If the DB is unreachable it prints a warning and still checks the YAML.
- Report: "Pre-registered rules" panel (status, unlock with a required reason, lock again, lock history). A hand edit after the lock shows the drift and withholds verdicts.
- Pilots = sessions started before the latest lock. They are hidden from results, the headline and verdicts ("Show them" toggle; verdicts are never final while pilots are shown), and the CSV has a `pilot` column. Re-locking starts a new period.
- Tests: `tests/test_locks.py` (7). Full suite: 286 passed, 3 skipped.

## 9-lite. Interim labelling — done

- `decision_rule.planned_participants` (alias of `min_participants`).
- Below the planned N, per-check verdicts read "Interim: leaning X" instead of "Evidence for/against". The Results headline says "Interim: n of N planned people".
- Sequential/alpha-spending mode dropped (decision in the analysis).
## 4. Prototype preflight — done

- `testbench/preflight.py`: stdlib HTML parser over each variant's entry page and other HTML in its folder (excluding other variants' entry pages).
- Errors: missing relative asset; external host the CSP blocks (any host with `LOCAL_ASSETS=1`); first-click target missing on a script-free page.
- Warnings: CDN use that breaks with LOCAL_ASSETS; links/forms leaving the prototype; unlabeled controls (rule mirrors `describe()`); duplicate labels; first-click target missing on a page with scripts; goal id not found; modules total > 60 min.
- Launch checklist item "Prototypes pass preflight" with expandable findings (file, snippet, fix). Going live is refused server-side (409 / flash) while errors exist; the existing status JS shows the message.
- `check` prints findings and exits 1 on errors.
- Fixed with it: the "A/B tasks have answer keys" check flagged goal-graded tasks as ungraded.
- Tests: `tests/test_preflight.py` (6). Full suite: 292 passed, 3 skipped.
- Limitation (future work): static only. Pages rendered entirely by script get no label/target checks.
## 5. SUS / UMUX-Lite presets — done

- `questions.py`: `preset: sus | umux_lite` expands into a fixed matrix (EN/ID wording, project locale, `locale:` override). Rows/points/labels/type/options are rejected on a preset.
- Scoring: SUS standard ×2.5 with the Sauro-Lewis curved grade; UMUX-Lite 0–100 plus its regression SUS-equivalent. Incomplete answers get no score.
- A/B report: "Standard questionnaires" panel (n, mean with bootstrap CI, median, grade per variant; paired gain vs baseline with CI and verdict; final-survey presets overall). Survey summaries show the score too. CSV: `<id>.score`.
- Studio writes presets back as `preset:` (they would otherwise become plain scale questions).
- Locale is now passed to `Q.normalize` from every module type.
- Tests: `tests/test_presets.py` (7). Full suite: 299 passed, 3 skipped.
- open (needs a human): check the Indonesian SUS wording word for word against Sharfina & Santoso (2016, ICACSIS, IEEE). A web search found no accessible copy of the items. The report labels it "check it against the published version". The Indonesian UMUX-Lite is labelled as not validated. Presets cannot be used in `decision_rule.survey_questions` (scale questions only).
## 8. Wilcoxon signed-rank — done

- `ab_test.wilcoxon()`: zeros dropped, average ranks for ties, exact distribution (doubled-rank counting table) up to 25 pairs, normal approximation with tie and continuity corrections above, matched-pairs rank-biserial r. Pure Python, no SciPy.
- Report "faster" row shows the sign test p, the Wilcoxon p (marked when approximate) and r, and names which test decides.
- `decision_rule.time_test: sign | wilcoxon` (validated, part of the locked `decision_rule`). Studio keeps it, and also keeps `planned_participants` (it used to fall back to 8).
- Docs: `docs/statistics.md`, `docs/configuration.md`.
- Tests: `tests/test_wilcoxon.py` (6). Hand-checked exact values (2/1024, 0.625, ties 0.5, 4/128). Full suite: 305 passed, 3 skipped.
- fixed: lostness and the other rework metrics use the paired Wilcoxon (item 7).
## 6. Segments by role — done

- `segments: {name: regex}` on ab_test (validated with `_safe_regex`). The report "Segments" table shows people, success and median per variant, and median paired difference; unmatched people form "Other". No per-segment verdicts (decision from the analysis).
- Carryover check on the existing "Order effect" panel: a warning when the challenger's median difference flips sign between baseline-first and challenger-first groups.
- Studio keeps module-level `segments` (`keepModule`).
- Tests: `tests/test_segments.py` (5).
- won't do (design): a "Segment by" control with per-segment verdicts. Segments are too small; the descriptive table covers it.
## 7. Error and rework metrics — done

- Runner: `page` events (first load, every in-frame load, `hashchange`, `postMessage({testbench:"page"})`), `miss` (click on no control), `error` (`postMessage({testbench:"error", id})`). Same `task_events` list, cap raised to 200.
- `ab_test.rework()`: pages, back-navigations, misclicks, errors, Smith lostness (needs `optimal_steps`, validated as an int ≥ 1).
- Report: "Confusion and rework" table per task/variant plus paired Wilcoxon (p, r) per challenger. Columns without data are hidden; the panel is hidden when there are no events.
- CSV: `pages_visited`, `back_nav`, `misclicks`, `errors`, `lostness`.
- Fixed while building it (from item 1): a navigation inside the iframe re-ran `onload` and restarted the timer, even after a goal had ended the task. Now only the first load starts it.
- Tests: `tests/test_rework.py` (5).
- fixed: `aria-invalid="true"` on any element is logged as a validation error (MutationObserver), and verified in the browser test. "Clicks not on any success path": won't do (dropped in the analysis).
- Limitation: the runner blocks non-`#` links, so in practice "pages" are hash states or script-reported views.
## 10. Journey module — done (same-participant mode)

- `testbench/modules/journey_test.py`, a subclass of ABTest. Variants have ordered steps `{id, role, file, goal, failure?, prompt?, title?}`; each step is a goal-graded, no-field task with its own frame URL (`view/<n>/<step>/`). ab_test gained the hooks `tasks_for()`, `task_frame_url()` and `serve_view()` for this; the runner uses a per-task `frameUrl`.
- Validation: roles, goal ids, HTML files inside the project, unique step ids, `cycle_time_hours` > 0, `roles_played_by` must be `same_participant` (`per_role` is rejected as not supported yet).
- Report: "Flow structure" (steps, handoffs, roles, cycle time), shown with zero participants (`report_without_data`; the results page now renders such reports). Per challenger: hands-on time (sign + Wilcoxon, median saved, share of cycle time), whole-flow success with a paired CI, and interim labelling. Step-by-step descriptive table.
- CSV: one row per participant × flow × step (role, time, grade, goal outcome, rework counts); the combined export has total_s and success per flow.
- Preflight checks every step page and its goal. Launch includes journey modules. Studio sends journey modules to the YAML editor; a scaffold template exists (`scaffold/templates/journey_test.yaml`).
- Tests: `tests/test_journey.py` (5). Full suite: 320 passed, 3 skipped.
- fixed: journey flows lock at go-live (`locks._journey_snapshot`; Studio rejects changes; shared `_rules_panel.html`); tested.
- fixed: "Journey test" option in the Studio new-module form, with labels; tested by creating a module.
- deferred by design: `per_role` (multi-participant) runs, per the analysis ("start with same_participant").

## Final state

- All 10 backlog items done; fixes from the review pass applied.
- Full suite: 324 passed, 3 skipped (including 2 real-browser tests).
- Remaining open: only the Indonesian SUS wording check (needs the paper). Deferred by design: multi-participant journeys, segment verdicts, browser-mode preflight, sequential testing.
- Nothing committed; all changes are in the working tree on `feat/one-design-system`.
