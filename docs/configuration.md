# Configuration reference

A project is a folder under `DATA_DIR/projects/` (editable in the Studio) or under one of the read-only `PROJECTS_DIRS`. The Studio edits exactly these files, so everything below also applies there. The folder name is the URL slug: lowercase letters, digits and hyphens, and not `admin`, `static`, `api` or `health`. Run `python -m testbench check` after every change. It lists every problem across all projects in one pass.

## project.yaml

```yaml
name: My Study
description: Shown on the sign-in page and on the index at /.
locale: en                 # UI language, a file in testbench/locales/ (en, id)
listed: true               # list on the index page at /; unlisted projects are still reachable by URL

identity:
  mode: code               # code | email | anonymous
  pattern: "^P\\d{2}$"      # code: full-match regex; codes are upper-cased first
  hint: Printed under the code field.
  # domains: [example.com] # email: allowed domains (optional)

access: passcode           # passcode | open
# Passcodes are set in the Studio. An environment variable, when set, overrides it:
# passcode_env: MY_STUDY_PASSCODE              # defaults shown
# admin_passcode_env: MY_STUDY_ADMIN_PASSCODE

consent: Checkbox text participants must tick before starting. Omit to skip.

brand:
  primary: "#2563EB"       # buttons, links, focus rings
  nav: "#0F172A"           # top bar

modules: [profile, journey, checkout-ab]       # home-screen order; files under modules/
```

The login modes suit different studies:

| Mode | Suits | Notes |
| --- | --- | --- |
| `code` | Moderated sessions, recruited panels | You control who takes part, and codes carry meaning (for example, `D` codes for a survey and `P` codes for a lab session). |
| `email` | Internal staff | Stores the email as the identity. Use `domains` to restrict it. |
| `anonymous` | Open surveys | The app issues a code like `K7Q2-9FXA` and shows it on the home screen so participants can resume. |

## Common module keys

Every file in `modules/` has these keys:

```yaml
type: survey               # or ab_test
title: Shown on the home screen and in the progress bar
description: One sentence under the title.
minutes: 5                 # estimate shown on the card
requires: [profile]        # finish these first; the card shows as locked until then
audience:                  # hide the module from everyone else (direct URLs return 404)
  identity_pattern: "^P"   # regex search on the participant identity
  when: { ref: profile.role, in: [vendor] }   # condition on another module's answer
```

An `audience.when` condition is false until the referenced module has been answered. Pair it with `requires`, so the module appears once the answer exists.

## journey_test

Compares a current business flow with a proposed one. Each flow is an ordered list of steps; a step is one prototype page with a goal, played by a role. One participant plays every role in turn, and a step ends when its goal fires (see **Goals** under `ab_test`).

```yaml
type: journey_test
title: Vendor profile update to approval
variants:
  current:
    label: Current flow
    cycle_time_hours: 48          # optional: elapsed time in production (for example from process mining)
    steps:
      - { id: submit,  role: vendor,      file: current/vendor-update.html, goal: submitted }
      - { id: assign,  role: assignor,    file: current/assign.html,        goal: assigned }
      - { id: verify,  role: verificator, file: current/verify.html,        goal: verified, failure: [rejected] }
      - { id: approve, role: approver,    file: current/approve.html,       goal: approved }
  proposed:
    label: Proposed flow (auto-assign, no approval for low-risk)
    steps:
      - { id: submit, role: vendor,      file: proposed/vendor-update.html, goal: submitted, prompt: Submit the change. }
      - { id: verify, role: verificator, file: proposed/verify.html,        goal: verified }
baseline: current
order: rotate                     # as in ab_test
roles_played_by: same_participant # the only mode for now
decision_rule:
  planned_participants: 8
  time_test: sign                 # or wilcoxon
```

The report shows the structure of each flow before anyone takes part (steps, handoffs between roles, roles, cycle time). Once people finish, it compares each flow with the baseline on total hands-on time (sign test and Wilcoxon, with the median time saved and, with `cycle_time_hours`, that saving as a share of the cycle time) and on whole-flow success (every step reached its goal), and lists time, success, back-navigations and errors per step. Flows may have different numbers of steps, so only totals are compared statistically. Step-level numbers are descriptive. Studio opens journey modules in the YAML editor. Like `ab_test`, a journey module's flows (steps, order, roles, files, goals) and decision rule lock when the study goes live.

## Questions

Questions are used by `survey` modules and by the `post_survey` and `final_survey` of `ab_test`.

```yaml
- id: role                 # unique within the module; used in CSV columns and refs
  type: single             # single | multi | scale | matrix | text
  label: The question
  hint: Optional helper text
  required: true           # default true
  show_if: { ref: other_question, equals: "yes" }
```

| Type | Extra keys | Stored value |
| --- | --- | --- |
| `single` | `options`, `columns` (1-3), `options_from` | option value |
| `multi` | `options`, `max`, `columns`, `options_from` | list of values |
| `scale` | `points` (2-11, default 5), `labels` (2 ends or one per point) | integer |
| `matrix` | `rows`, `points`, `labels`, `na_label` | `{row: integer or "na"}` |
| `text` | `long` (default true), `max_length`, `placeholder` | string |

### Standard questionnaires

`preset` adds a standard questionnaire with fixed wording, scale and scoring, in the project's locale (English or Indonesian; `locale:` on the question overrides it):

```yaml
final_survey:
  - { id: sus, preset: sus }          # System Usability Scale: 10 items, 1 to 5, scored 0 to 100
  - { id: umux, preset: umux_lite }   # UMUX-Lite: 2 items, 1 to 7, scored 0 to 100
```

A preset is a `matrix` question whose rows, points and labels cannot be changed; use a normal matrix for custom wording. SUS is scored the standard way (odd items score minus 1, even items 5 minus score, sum times 2.5) and graded on the Sauro-Lewis curved scale. UMUX-Lite is `(item1 - 1 + item2 - 1) / 12 * 100`; the report also shows its regression-adjusted SUS equivalent (`0.65 * score + 22.9`). In an A/B `post_survey` the report compares each variant with the baseline (paired, bootstrapped 95% interval); in a `final_survey` or survey module it shows the overall score. CSV exports add `<id>.score` next to the item columns. The report names the wording used: the Indonesian SUS follows Sharfina & Santoso (2016), and the Indonesian UMUX-Lite is not a validated translation.

`options` and `rows` accept any of these forms:

```yaml
options: [Weekly, Monthly]                      # value = label
options: { weekly: Every week, monthly: Every month }
options: [{ value: w, label: Every week }]
```

`options_from: <matrix id>` reuses a matrix's rows as options. The matrix report then gains a "picked as priority" column, sorted by it, which gives a quick ranking of pain points.

### Conditions

`show_if` takes `ref` plus one of `equals`, `in` or `not_in`:

- `ref: question` refers to a question in the same module. On the same page it toggles live in the browser. It must refer to a question earlier on the page.
- `ref: module.question` refers to another module's answer, resolved when the page loads.

Hidden questions are neither required nor stored.

## ab_test

```yaml
type: ab_test
variants:                   # two or more
  A: { label: Current, file: prototypes/current/index.html }
  B: { label: Proposal, file: prototypes/proposal/index.html }
baseline: A                 # compared against every other variant
order: rotate               # rotate | code_parity | random | fixed
intro: Optional text for the first screen.

tasks:
  - id: find-price
    title: Find a price
    prompt: What does the premium plan cost per month?
    fields:                                   # default: one text field
      - { id: price, label: Price, placeholder: "e.g. 10" }
      - { id: sure, label: How sure are you?, kind: choice, options: [Sure, Unsure] }
    accept: { price: ["12", "12.00", "$12"] } # answer key: every field must match one alternative
    contains: []                              # fields that pass when an alternative appears anywhere in the answer
    probe: Shown to admins only, next to results. Note what the task is meant to reveal.
  - id: decide
    title: Finish the verification
    prompt: Complete the verification with the decision you think is right.
    goals:                                    # graded by what the prototype reports, see below
      success: [return-to-vendor]
      failure: [approve]
    end_on_goal: true                         # default: the timer stops at the goal and the task ends
    optimal_steps: 3                          # optional: fewest pages/states the task needs (for lostness)
    first_click:                              # optional: the right first click, per variant
      A: ["Tab Data Pajak"]
      B: ["Bagian Data Pajak", "Bandingkan Dokumen"]

segments:                   # optional: groups by identity pattern, shown as a descriptive table
  Verificator: "^VER-"
  Approver: "^APR-"
ease_question: true         # ask a 1-7 ease rating after each task (SEQ)
post_survey: [ ...questions ]   # after each variant
final_survey: [ ...questions ]  # once, at the end
preference: true            # adds "which view would you use?" when there are 2+ variants

decision_rule:
  planned_participants: 8   # pre-registered sample size; below it every verdict is interim ("leaning")
                            # (min_participants is the older name for the same number)
  survey_questions: [easy]  # scale questions in post_survey whose mean must rise
  min_survey_gain: 0.5
  time_test: sign           # sign (default) | wilcoxon: which test decides the "faster" check
```

These rules apply to prototypes and tasks:

- **Prototype files and folders.** A prototype is a static HTML file inside the project folder. It is served at `.../x/view/<n>/`, so relative links to sibling css, js and images keep working. Links that would leave the page are blocked and logged. `#anchor` links and in-page scripts work normally.
- **Answer matching.** Answers are compared after normalisation: upper-cased, with spaces and punctuation removed. `accept: { price: ["12"] }` therefore also accepts `12`, `12,-` and `1 2`.
- **Goals.** A task that ends in an action (save, submit, approve) can be graded by the prototype instead of a typed answer. Clicking an element with `data-testbench-goal="<id>"` reports goal `<id>`; prototype script can do the same with `parent.postMessage({ testbench: "goal", id: "<id>" }, "*")`. Only messages from the prototype frame count. The first listed goal reached decides the outcome; goals the task does not list are recorded but change nothing. With `end_on_goal: true` the timer stops at that goal, task time is the time at the goal, and the participant moves straight on (to the ease rating, or to any `fields` still to answer). A task with `goals` and no `fields` asks for no typed answer. When a task has both `accept` and `goals`, success needs both. The CSV export has `goal_outcome` and `goal_events` (`id@seconds`) per task.
- **First-click targets.** The runner records each click as `<tag>: <label>`, where the label is the element's `data-testbench-label`, else its `aria-label`, else its visible text. `first_click` targets are matched against the label without the tag, using the same normalisation as answers. Add `data-testbench-label` to elements you target so copy edits do not break the match. The report shows the share on target and the most common wrong first clicks per variant, plus a paired comparison (exact sign test on people who hit more targets on one variant). That comparison is shown next to the decision rule but never changes it. The CSV export has `first_click_label` and `first_click_correct`.
- **Locked rules.** When the study goes live, each `ab_test` module's analysis rules are locked: variants and their files, `baseline`, `order`, task ids and order, `accept`, `contains`, `goals`, `end_on_goal`, `first_click` and `decision_rule`. Studio then refuses a change to any of them, and `python -m testbench check` exits with an error if a YAML file was edited by hand; the report withholds verdicts until the change is undone. Titles, prompts and surveys can still be reworded. Sessions started before the lock are pilots: they are left out of results (Results can show them with "Show them") and marked `pilot = 1` in the CSV. To change a rule, unlock it from the module's results page with a reason, edit, then lock again; the lock history and reasons stay in the audit log, and results count only sessions started after the latest lock.
- **Prototype preflight.** Launch and `python -m testbench check` run static checks on every variant's HTML and the other HTML pages in its folder. Errors block going live: a relative stylesheet, script or image missing from the upload; a request to a host the app's security policy blocks (any host for images, anything but the Tailwind and font CDNs otherwise, and every host with `LOCAL_ASSETS=1`); and, on a page without scripts, a `first_click` target that matches no control. Warnings do not block: CDN requests that `LOCAL_ASSETS=1` would break, links or forms that leave the prototype, controls without a label (same rule as the click path), one label on several controls, first-click targets missing on pages with scripts, goal ids found neither on a `data-testbench-goal` nor in the prototype's script, and modules adding up to more than 60 minutes. Each finding names the file, the element and a fix. Elements a script adds at runtime are invisible to these checks.
- **Segments and order.** With `segments`, the report adds a table per group (people whose identity matches the pattern; the rest are "Other"): success and median total time per variant and the median paired difference. It is descriptive only, with no verdicts, because groups are small. The order table warns "Check for carryover" when a challenger's median difference has opposite signs for people who saw the baseline first and people who saw the challenger first.
- **Confusion and rework.** The runner records the pages and in-page states (`#hash`) a participant passes through, clicks that land on no control (misclicks), and validation errors the prototype reports with `parent.postMessage({ testbench: "error", id: "<what>" }, "*")`. A prototype that changes views with script, not the hash, can report them with `parent.postMessage({ testbench: "page", id: "<view>" }, "*")`. The report then shows, per task and variant, back-navigations (returns to a page already seen), misclicks, errors and, for tasks with `optimal_steps`, Smith's lostness (0 on the shortest path, above about 0.4 looks lost), plus a paired Wilcoxon comparison per challenger. Columns without data (no error events, no `optimal_steps`) are hidden. The CSV export has `pages_visited`, `back_nav`, `misclicks`, `errors` and `lostness`.
- **Tasks without a key.** Tasks with no answer key (observational tasks) stay ungraded until someone sets a grade on the participant's admin page. A coordinator's grade always overrides the automatic check.
- **The decision rule.** For each challenger, the rule has three checks:
  - Total time is shorter for more than half of the participants who tried both.
  - Success rate is not lower.
  - The mean of `survey_questions` rises by at least `min_survey_gain`.

  Each check is shown as met or not met.
