# Configuration reference

A project is a folder under one of the `PROJECTS_DIRS`. The folder name is the URL slug: lowercase letters, digits and hyphens, and not `admin`, `static`, `api` or `health`. Run `python -m testbench check` after every change. It lists every problem across all projects in one pass.

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

ease_question: true         # ask a 1-7 ease rating after each task (SEQ)
post_survey: [ ...questions ]   # after each variant
final_survey: [ ...questions ]  # once, at the end
preference: true            # adds "which view would you use?" when there are 2+ variants

decision_rule:
  min_participants: 8       # below this, the report marks the result as indicative
  survey_questions: [easy]  # scale questions in post_survey whose mean must rise
  min_survey_gain: 0.5
```

These rules apply to prototypes and tasks:

- **Prototype files and folders.** A prototype is a static HTML file inside the project folder. It is served at `.../x/view/<n>/`, so relative links to sibling css, js and images keep working. Links that would leave the page are blocked and logged. `#anchor` links and in-page scripts work normally.
- **Answer matching.** Answers are compared after normalisation: upper-cased, with spaces and punctuation removed. `accept: { price: ["12"] }` therefore also accepts `12`, `12,-` and `1 2`.
- **Tasks without a key.** Tasks with no answer key (observational tasks) stay ungraded until someone sets a grade on the participant's admin page. A coordinator's grade always overrides the automatic check.
- **The decision rule.** For each challenger, the rule has three checks:
  - Total time is shorter for more than half of the participants who tried both.
  - Success rate is not lower.
  - The mean of `survey_questions` rises by at least `min_survey_gain`.

  Each check is shown as met or not met.
