"""Small stdlib JSON-Schema validator for the declarative model input.

It implements the subset emitted by the Pydantic model (types, refs, oneOf,
anyOf, allOf, const, enum, bounds, arrays and objects). It intentionally does
not depend on Pydantic, jsonschema or OpenMC so a copied case can validate on a
machine that only has Python and OpenMC.
"""
from __future__ import annotations

import math
import re

from .contract import MODEL_SCHEMA, ValidationError, validate_semantics


def _resolve(root, ref):
    if not isinstance(ref, str) or not ref.startswith('#/'):
        raise ValidationError('schema引用格式错误：' + str(ref))
    value = root
    for raw in ref[2:].split('/'):
        token = raw.replace('~1', '/').replace('~0', '~')
        if isinstance(value, list):
            value = value[int(token)]
        else:
            value = value[token]
    return value


def _type_ok(value, kind):
    if kind == 'object':
        return isinstance(value, dict)
    if kind == 'array':
        return isinstance(value, list)
    if kind == 'string':
        return isinstance(value, str)
    if kind == 'integer':
        return type(value) is int
    if kind == 'number':
        return type(value) in (int, float) and math.isfinite(value)
    if kind == 'boolean':
        return type(value) is bool
    if kind == 'null':
        return value is None
    return False


def _validate(value, schema, path, root):
    if '$ref' in schema:
        _validate(value, _resolve(root, schema['$ref']), path, root)
        return
    if 'allOf' in schema:
        for item in schema['allOf']:
            _validate(value, item, path, root)
    if 'anyOf' in schema:
        for item in schema['anyOf']:
            try:
                _validate(value, item, path, root)
                break
            except ValidationError:
                continue
        else:
            raise ValidationError(path + ' 类型或结构不符合')
    if 'oneOf' in schema:
        discriminator = schema.get('discriminator')
        if isinstance(discriminator, dict) and isinstance(value, dict):
            prop = discriminator.get('propertyName')
            selected = None
            for item in schema['oneOf']:
                branch = _resolve(root, item['$ref']) if '$ref' in item else item
                candidate = branch.get('properties', {}).get(prop, {})
                if candidate.get('const') == value.get(prop):
                    selected = branch
                    break
            if selected is not None:
                _validate(value, selected, path, root)
                return
        matches = 0
        errors = []
        for item in schema['oneOf']:
            try:
                _validate(value, item, path, root)
                matches += 1
            except ValidationError as exc:
                errors.append(str(exc))
        if matches != 1:
            detail = errors[0] if errors else '无可匹配分支'
            raise ValidationError(path + ' 必须精确匹配一个结构：' + detail)
    if 'const' in schema and value != schema['const']:
        raise ValidationError(path + ' 必须为 ' + repr(schema['const']))
    if 'enum' in schema and value not in schema['enum']:
        raise ValidationError(path + ' 不支持的值')
    if 'type' in schema:
        types = schema['type'] if isinstance(schema['type'], list) else [schema['type']]
        if not any(_type_ok(value, kind) for kind in types):
            raise ValidationError(path + ' 类型错误或非有限值')
    if isinstance(value, dict):
        for key in schema.get('required', []):
            if key not in value:
                raise ValidationError(path + '.' + key + ' 缺少必需字段')
        properties = schema.get('properties', {})
        additional = schema.get('additionalProperties', True)
        for key, item in value.items():
            if key in properties:
                _validate(item, properties[key], path + '.' + key, root)
            elif additional is False:
                raise ValidationError(path + '.' + key + ' 不支持的字段')
            elif isinstance(additional, dict):
                _validate(item, additional, path + '.' + key, root)
    if isinstance(value, list):
        if len(value) < schema.get('minItems', 0) or len(value) > schema.get('maxItems', 10**9):
            raise ValidationError(path + ' 长度超限')
        if 'items' in schema:
            for index, item in enumerate(value):
                _validate(item, schema['items'], path + f'[{index}]', root)
    if isinstance(value, str):
        if len(value) < schema.get('minLength', 0) or len(value) > schema.get('maxLength', 10**9):
            raise ValidationError(path + ' 长度超限')
        if 'pattern' in schema and re.search(schema['pattern'], value) is None:
            raise ValidationError(path + ' 格式不符合')
    if type(value) in (int, float) and not isinstance(value, bool):
        minimum = schema.get('minimum')
        maximum = schema.get('maximum')
        exclusive = schema.get('exclusiveMinimum')
        if minimum is not None and value < minimum:
            raise ValidationError(path + ' 数值超范围')
        if maximum is not None and value > maximum:
            raise ValidationError(path + ' 数值超范围')
        if isinstance(exclusive, bool):
            if exclusive and minimum is not None and value <= minimum:
                raise ValidationError(path + ' 数值超范围')
        elif exclusive is not None and value <= exclusive:
            raise ValidationError(path + ' 数值超范围')


def validate_shape(spec):
    _validate(spec, MODEL_SCHEMA, '$', MODEL_SCHEMA)
    return spec


def validate(spec):
    validate_shape(spec)
    return validate_semantics(spec, 'fallback')
