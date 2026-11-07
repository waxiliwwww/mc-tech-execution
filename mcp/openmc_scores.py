"""Tally score whitelist shared by MCP-side validation and static semantic checks.

The engine turns every <scores> entry into a ReactionFilter bin
(openmc/filter.py, `bins` setter): an unknown string aborts transport with
'Unknown reaction name "…"'.  That validation used to happen only inside the
engine, so a typo like 'fluxx' passed every MCP-side check and killed the run.

The accepted set is generated from openmc.data.REACTION_MT plus the special
tally scores and stored in config/score-names.json, so the MCP server can check
scores without importing OpenMC (the server keeps the engine in a subprocess).
"""
from __future__ import annotations

import difflib
import json
from pathlib import Path

CATALOG_FILENAME = 'score-names.json'
_CACHE: dict | None = None
_UNAVAILABLE: str | None = None


def _catalog_path() -> Path | None:
    here = Path(__file__).resolve()
    for candidate in (here.parent.parent / 'config' / CATALOG_FILENAME,
                      here.parent / 'config' / CATALOG_FILENAME):
        if candidate.is_file():
            return candidate
    return None


def catalog() -> dict | None:
    """Return the loaded whitelist, or None when the catalog is not deployed."""
    global _CACHE, _UNAVAILABLE
    if _CACHE is not None:
        return _CACHE
    if _UNAVAILABLE is not None:
        return None
    path = _catalog_path()
    if path is None:
        _UNAVAILABLE = ('score名单未随部署提供（config/' + CATALOG_FILENAME
                        + '缺失）；未做score校验')
        return None
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc:
        _UNAVAILABLE = 'score名单无法解析：' + str(exc)[:120]
        return None
    base = [str(name) for name in data.get('base_scores') or []]
    reactions = [str(name) for name in data.get('reaction_names') or []]
    if not base:
        _UNAVAILABLE = 'score名单缺少base_scores，无法判定合法性'
        return None
    _CACHE = {'names': set(base) | set(reactions), 'base': set(base),
              'reactions': set(reactions), 'source': str(data.get('source') or ''),
              'openmc_version': data.get('openmc_version'), 'path': str(path)}
    return _CACHE


def known() -> set[str]:
    data = catalog()
    return set() if data is None else set(data['names'])


def unavailable_reason() -> str | None:
    catalog()
    return _UNAVAILABLE


def suggest(name: str, limit: int = 3) -> list[str]:
    names = known()
    if not names:
        return []
    return difflib.get_close_matches(str(name), sorted(names), n=limit, cutoff=0.6)


def check(scores) -> tuple[list[str], dict[str, list[str]]]:
    """Return (unknown_scores, {score: close_matches}); empty list when all valid."""
    names = known()
    unknown, hints = [], {}
    for score in scores or []:
        text = str(score).strip()
        if not text or text in names:
            continue
        unknown.append(text)
        hints[text] = suggest(text)
    return unknown, hints


def describe() -> dict:
    """Compact provenance block for validation output."""
    data = catalog()
    if data is None:
        return {'checked': False, 'skipped_reason': unavailable_reason()}
    return {'checked': True, 'source': 'config/' + CATALOG_FILENAME,
            'openmc_version': data['openmc_version'], 'accepted_scores': len(data['names'])}