"""Resolve the linked policy's insurer to the researched claim-document catalog.

Only insurer selection belongs here. Incident classification and response formatting
are separate steps; no document requirements are inferred from insurer names.
"""
import json
import logging
import re
import unicodedata
from copy import deepcopy
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Literal

from app.repositories.base import VectorRepository

logger = logging.getLogger(__name__)
CATALOG_PATH = Path(__file__).resolve().parents[2] / 'config' / 'claim_documents' / 'catalog.json'


def normalize_insurer_name(name: str) -> str:
    name = unicodedata.normalize('NFKC', name).casefold()
    name = re.sub(r'주식회사|\(\s*주\s*\)', '', name)
    return re.sub(r'\s+', '', name)


@lru_cache(maxsize=1)
def _load_catalog() -> dict:
    return json.loads(CATALOG_PATH.read_text(encoding='utf-8'))


@dataclass(frozen=True)
class ClaimInsurerSelection:
    status: Literal['MATCHED', 'MISSING_TERMS', 'INSURER_NOT_FOUND', 'UNSUPPORTED_INSURER', 'LOOKUP_FAILED', 'AMBIGUOUS_INSURER']
    insurer_name: str | None = None
    insurer: dict | None = None


def resolve_claim_insurer(terms_id: str | None, repository: VectorRepository) -> ClaimInsurerSelection:
    """Use trusted linked terms only, never question text or underwriter aliases.

    Failures do not break the existing RAG answer. A future caller can distinguish
    missing linkage from an unsupported insurer without guessing another list.
    """
    if not terms_id:
        return ClaimInsurerSelection('MISSING_TERMS')
    try:
        lookup = getattr(repository, 'get_terms_insurer', None)
        if lookup is None:
            return ClaimInsurerSelection('LOOKUP_FAILED')
        name = lookup(terms_id)
        if not name or not name.strip():
            return ClaimInsurerSelection('INSURER_NOT_FOUND')
        key = normalize_insurer_name(name)
        matches = []
        for insurer in _load_catalog()['insurers']:
            names = [insurer['name'], *insurer.get('aliases', [])]
            if insurer.get('dbInsurerName'):
                names.append(insurer['dbInsurerName'])
            if key in {normalize_insurer_name(n) for n in names}:
                matches.append(insurer)
        if len(matches) > 1:
            return ClaimInsurerSelection('AMBIGUOUS_INSURER', name)
        if not matches:
            return ClaimInsurerSelection('UNSUPPORTED_INSURER', name)
        # Callers must not mutate the cached catalog for another user's request.
        return ClaimInsurerSelection('MATCHED', name, deepcopy(matches[0]))
    except Exception as exc:
        # Exception text may contain connection details; log only its type.
        logger.warning('청구서류 보험사 조회 실패: %s', type(exc).__name__)
        return ClaimInsurerSelection('LOOKUP_FAILED')
