"""Backend selection for model-spec/v2 validation.

Pydantic is preferred when importable. Only an ImportError falls back; a real
validation failure is never hidden by trying a second backend.
"""
from __future__ import annotations

from .contract import ValidationError, check_region_depth, validate_semantics


def _load_pydantic():
    try:
        from . import pydantic_backend
    except ImportError as exc:
        return None, exc
    return pydantic_backend, None


def _load_fallback():
    from . import fallback_backend
    return fallback_backend


def available_backend():
    backend, _ = _load_pydantic()
    return 'pydantic' if backend is not None else 'fallback'


def _selected(backend):
    if backend == 'auto':
        module, _ = _load_pydantic()
        if module is not None:
            return 'pydantic', module
        return 'fallback', _load_fallback()
    if backend == 'pydantic':
        module, error = _load_pydantic()
        if module is None:
            raise ValidationError('Pydantic backend 不可用：' + str(error))
        return 'pydantic', module
    if backend == 'fallback':
        return 'fallback', _load_fallback()
    raise ValidationError('未知 backend：' + str(backend))


def validate_spec_result(spec, backend='auto'):
    check_region_depth(spec)
    name, module = _selected(backend)
    module.validate_shape(spec)
    return validate_semantics(spec, name)


def validate_spec(spec, backend='auto'):
    validate_spec_result(spec, backend)
    return spec


def validation_report(spec, backend='auto'):
    return validate_spec_result(spec, backend)
