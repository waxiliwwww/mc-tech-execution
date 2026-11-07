"""Pydantic model for the declarative model input; loaded only when Pydantic is present."""
from __future__ import annotations

from typing import Annotated, Literal, Union as TUnion

from pydantic import BaseModel, ConfigDict, Field, ValidationError as PydanticValidationError

from .contract import ValidationError, validate_semantics


class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, allow_inf_nan=False)


Name = Annotated[str, Field(max_length=200)]
NuclideName = Annotated[str, Field(min_length=1, max_length=40)]
PositiveFloat = Annotated[float, Field(gt=0)]
PositiveInt = Annotated[int, Field(ge=1)]
Vec2 = Annotated[list[float], Field(min_length=2, max_length=2)]
Vec3 = Annotated[list[float], Field(min_length=3, max_length=3)]
Vec3Int = Annotated[list[int], Field(min_length=3, max_length=3)]
Boundary = Literal['transmission', 'vacuum', 'reflective', 'white']


class Nuclide(Strict):
    name: NuclideName
    fraction: PositiveFloat


class Material(Strict):
    id: PositiveInt
    name: Name = ''
    density: PositiveFloat
    density_unit: Literal['g/cm3', 'atom/b-cm', 'atom/cm3']
    percent_type: Literal['ao', 'wo']
    nuclides: list[Nuclide] = Field(min_length=1, max_length=500)
    temperature: PositiveFloat = Field(default=None)
    sab: list[Name] = Field(default_factory=list, max_length=20)
    depletable: bool = False
    volume: PositiveFloat = Field(default=None)


class HalfSpace(Strict):
    op: Literal['halfspace']
    surface: PositiveInt
    side: Literal['negative', 'positive']


class RegionIntersection(Strict):
    op: Literal['intersection']
    items: list['RegionNode'] = Field(min_length=1)


class RegionUnion(Strict):
    op: Literal['union']
    items: list['RegionNode'] = Field(min_length=1)


class RegionComplement(Strict):
    op: Literal['complement']
    item: 'RegionNode'


RegionNode = Annotated[
    TUnion[HalfSpace, RegionIntersection, RegionUnion, RegionComplement],
    Field(discriminator='op'),
]
for _model in (RegionIntersection, RegionUnion, RegionComplement):
    _model.model_rebuild()


class XPlaneParameters(Strict):
    x0: float


class YPlaneParameters(Strict):
    y0: float


class ZPlaneParameters(Strict):
    z0: float


class XCylinderParameters(Strict):
    y0: float = 0
    z0: float = 0
    r: PositiveFloat


class YCylinderParameters(Strict):
    x0: float = 0
    z0: float = 0
    r: PositiveFloat


class ZCylinderParameters(Strict):
    x0: float = 0
    y0: float = 0
    r: PositiveFloat


class SphereParameters(Strict):
    x0: float = 0
    y0: float = 0
    z0: float = 0
    r: PositiveFloat


class SurfaceBase(Strict):
    id: PositiveInt
    boundary: Boundary = 'transmission'


class XPlane(SurfaceBase):
    type: Literal['x-plane']
    parameters: XPlaneParameters


class YPlane(SurfaceBase):
    type: Literal['y-plane']
    parameters: YPlaneParameters


class ZPlane(SurfaceBase):
    type: Literal['z-plane']
    parameters: ZPlaneParameters


class XCylinder(SurfaceBase):
    type: Literal['x-cylinder']
    parameters: XCylinderParameters


class YCylinder(SurfaceBase):
    type: Literal['y-cylinder']
    parameters: YCylinderParameters


class ZCylinder(SurfaceBase):
    type: Literal['z-cylinder']
    parameters: ZCylinderParameters


class Sphere(SurfaceBase):
    type: Literal['sphere']
    parameters: SphereParameters


SurfaceNode = Annotated[
    TUnion[XPlane, YPlane, ZPlane, XCylinder, YCylinder, ZCylinder, Sphere],
    Field(discriminator='type'),
]


class VoidFill(Strict):
    type: Literal['void']


class RefFill(Strict):
    type: Literal['material', 'universe', 'lattice']
    id: PositiveInt


Fill = Annotated[TUnion[VoidFill, RefFill], Field(discriminator='type')]


class Cell(Strict):
    id: PositiveInt
    name: Name = ''
    universe: Annotated[int, Field(ge=0)]
    region: RegionNode
    fill: Fill


class Lattice(Strict):
    id: PositiveInt
    name: Name = ''
    lower_left: Vec2
    pitch: Annotated[list[PositiveFloat], Field(min_length=2, max_length=2)]
    universes: Annotated[
        list[Annotated[list[Annotated[int, Field(ge=0)]], Field(min_length=1, max_length=100)]],
        Field(min_length=1, max_length=100),
    ]
    outer: Annotated[int, Field(ge=0)] = Field(default=None)


class PointSource(Strict):
    type: Literal['point']
    point: Vec3
    strength: PositiveFloat = Field(default=None)
    energy_ev: PositiveFloat = Field(default=None)


class BoxSource(Strict):
    type: Literal['box']
    lower_left: Vec3
    upper_right: Vec3
    strength: PositiveFloat = Field(default=None)
    energy_ev: PositiveFloat = Field(default=None)


Source = Annotated[TUnion[PointSource, BoxSource], Field(discriminator='type')]


class Settings(Strict):
    run_mode: Literal['eigenvalue', 'fixed source']
    particles: Annotated[int, Field(ge=1, le=100000000)]
    batches: Annotated[int, Field(ge=2, le=1000000)]
    inactive: Annotated[int, Field(ge=0)]
    generations_per_batch: Annotated[int, Field(ge=1)] = 1
    seed: Annotated[int, Field(ge=1)] = 1
    source: Source


class EnergyFilter(Strict):
    type: Literal['energy']
    bins: Annotated[list[float], Field(min_length=1, max_length=10000)]


class MaterialFilter(Strict):
    type: Literal['material']
    bins: Annotated[list[PositiveInt], Field(min_length=1, max_length=10000)]


class MeshFilter(Strict):
    type: Literal['mesh']
    dimension: Vec3Int
    lower_left: Vec3
    upper_right: Vec3


Filter = Annotated[TUnion[EnergyFilter, MaterialFilter, MeshFilter], Field(discriminator='type')]


class Tally(Strict):
    id: PositiveInt
    name: Name = ''
    scores: Annotated[list[Name], Field(min_length=1, max_length=20)]
    nuclides: Annotated[list[Name], Field(min_length=1, max_length=500)] = Field(default=None)
    filters: Annotated[list[Filter], Field(max_length=8)] = Field(default_factory=list)


class Override(Strict):
    code: Literal['outer_boundary_transmission']
    reason: Annotated[str, Field(min_length=1, max_length=500)]
    scope: Literal['run', 'build', 'scan'] = 'run'


class Validation(Strict):
    overrides: list[Override] = Field(default_factory=list)


class ModelSpec(Strict):
    model_config = ConfigDict(extra='forbid', strict=True, allow_inf_nan=False,
                            json_schema_extra={'description': 'OpenMC 声明式模型输入；组合模型，不执行调用方Python；专业适用性需另行确认。', '$id': 'https://mc-agent-tools.local/model-spec.json'})
    name: Name = ''
    materials: list[Material] = Field(min_length=1, max_length=1000)
    surfaces: list[SurfaceNode] = Field(min_length=1, max_length=10000)
    cells: list[Cell] = Field(min_length=1, max_length=10000)
    lattices: list[Lattice] = Field(default_factory=list, max_length=100)
    root_universe: Annotated[int, Field(ge=0)]
    settings: Settings
    tallies: list[Tally] = Field(default_factory=list, max_length=100)
    validation: Validation = Field(default=None)


for _model in (RegionIntersection, RegionUnion, RegionComplement):
    _model.model_rebuild()


def _location(location):
    dots = []
    for item in location:
        if isinstance(item, int):
            if dots:
                dots[-1] += f'[{item}]'
            else:
                dots.append(f'[{item}]')
        else:
            dots.append(str(item))
    return '.'.join(dots)


_MESSAGES = {
    'extra_forbidden': '不支持的字段',
    'missing': '缺少必需字段',
    'literal_error': '不支持的值',
    'enum': '不支持的值',
    'greater_than': '数值超范围',
    'greater_than_equal': '数值超范围',
    'less_than': '数值超范围',
    'less_than_equal': '数值超范围',
    'string_too_long': '长度超限',
    'string_too_short': '长度超限',
    'too_long': '长度超限',
    'too_short': '长度超限',
    'list_type': '类型错误',
    'dict_type': '类型错误',
    'string_type': '类型错误',
    'int_type': '类型错误',
    'float_type': '类型错误',
    'bool_type': '类型错误',
    'int_from_float': '类型错误',
    'finite_number': '类型错误或非有限值',
}


def validate_shape(spec):
    try:
        ModelSpec.model_validate(spec)
    except PydanticValidationError as exc:
        first = exc.errors()[0]
        path = _location(first.get('loc', ())) or '$'
        message = _MESSAGES.get(first.get('type'), first['msg'])
        raise ValidationError(path + '：' + message) from exc
    return spec


def validate(spec):
    validate_shape(spec)
    return validate_semantics(spec, 'pydantic')
