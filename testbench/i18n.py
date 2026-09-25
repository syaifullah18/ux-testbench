"""UI strings per locale. Study content (questions, tasks) comes from project YAML in whatever
language the project uses; these files only cover the app's own wording."""
from functools import lru_cache
from pathlib import Path

import yaml

LOCALE_DIR = Path(__file__).parent / "locales"
FALLBACK = "en"


@lru_cache(maxsize=None)
def _table(locale):
    path = LOCALE_DIR / f"{locale}.yaml"
    if not path.is_file():
        return {}
    with open(path, encoding="utf-8") as fh:
        return _flatten(yaml.safe_load(fh) or {})


def _flatten(tree, prefix=""):
    out = {}
    for key, val in tree.items():
        full = f"{prefix}{key}"
        if isinstance(val, dict):
            out.update(_flatten(val, full + "."))
        else:
            out[full] = str(val)
    return out


def available():
    return sorted(p.stem for p in LOCALE_DIR.glob("*.yaml"))


def translator(locale):
    table, fallback = _table(locale), _table(FALLBACK)

    def t(key, **kw):
        text = table.get(key) or fallback.get(key) or key
        return text.format(**kw) if kw else text
    return t
