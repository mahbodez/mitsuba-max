"""The four M1 correctness scenes, built as IR with no 3ds Max involved.

Each one reduces a whole class of bug to a number that a machine can check, which is the
point of M1: "all four pass in CI with numeric tolerances, not eyeballs".

1. **Chirality** — a quad whose four UV corners are four distinct colours. Catches the
   `look_at` horizontal mirror and a UV V-flip *simultaneously*, by asserting which screen
   corner each colour lands in. No visual inspection, no reference image.
2. **White furnace** — a white Lambertian sphere under a constant environment of radiance
   1. A correct renderer produces exactly 1.0 everywhere and the sphere becomes invisible.
   Any deviation is an energy, unit or `twosided` bug, reduced to one number.
3. **Transform torture** — emissive cubes placed through `C·T·C⁻¹` with negative scale,
   non-uniform scale, off-origin rotation and a composed parent/child pair. Each cube's
   rendered position is compared against an analytically projected pixel coordinate.
4. **Cornell box** — a replica of `mi.cornell_box()` built entirely from IR, diffed against
   the original render.

Geometry is generated rather than checked in: the fixtures are the **JSON**, and the PLY
files are rebuilt from this module. That keeps binaries out of the repository and makes the
content hashes verifiable rather than assumed — if `write_ply` changes, the hashes change
and the checked-in JSON stops matching, which is exactly the alarm you want.
"""

import math
import struct
import zlib
from pathlib import Path

from core.assets import AssetStore
from core.ir import (
    Camera,
    Emission,
    Environment,
    Material,
    Mesh,
    RenderSettings,
    Scene,
    TextureRef,
)
from core.meshbuild import MeshGroup, RawMesh, build_groups, write_ply
from core.transform import compose_trs, conjugate, look_at_matrix

__all__ = ["SCENES", "build_all", "chirality", "cornell_box", "transform_torture",
           "white_furnace"]


# --------------------------------------------------------------------------------------
# geometry primitives, in Mitsuba space (Y-up, metres)
# --------------------------------------------------------------------------------------


def _group(positions: list[float], normals: list[float], uvs: list[float],
           indices: list[int]) -> MeshGroup:
    return MeshGroup(material_id=1, positions=positions, normals=normals, uvs=uvs,
                     indices=indices)


def _from_max(quads: tuple[tuple[tuple[float, float, float], ...], ...],
              uvs: tuple[tuple[float, float], ...],
              smoothing: tuple[int, ...],
              scale_to_meters: float) -> MeshGroup:
    """Build a `MeshGroup` from quads authored in **Max space**, through `build_groups`.

    Going through the real converter rather than hand-writing Mitsuba-space vertices is the
    whole point: it means the fixtures exercise the Z-up → Y-up change, the metres
    conversion, the smoothing-group normals, the vertex split and the V flip, instead of
    restating whatever those functions happen to do today.
    """
    positions: list[float] = []
    tverts: list[float] = []
    faces: list[int] = []
    for quad, sg in zip(quads, smoothing, strict=True):
        base = len(positions) // 3
        tbase = len(tverts) // 2
        for corner, uv in zip(quad, uvs, strict=True):
            positions.extend(float(c) for c in corner)
            tverts.extend(float(c) for c in uv)
        for tri in ((0, 1, 2), (0, 2, 3)):
            faces.extend([base + tri[0], base + tri[1], base + tri[2],
                          tbase + tri[0], tbase + tri[1], tbase + tri[2],
                          sg, 1])
    groups = build_groups(RawMesh(positions=positions, faces=faces, tverts=tverts),
                          scale_to_meters=scale_to_meters)
    return groups[0]


_QUAD_UVS = ((0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0))
"""Max UV convention: V = 0 is the bottom of the image."""


def unit_quad(scale_to_meters: float = 1.0) -> MeshGroup:
    """A 2x2 quad facing Mitsuba's +Z, matching Mitsuba's own `rectangle`.

    Authored in Max space, where `(x, y, z)_max → (x, z, -y)_mitsuba`, so Mitsuba's XY
    plane is Max's XZ plane and the outward normal +Z_mitsuba is −Y_max.
    """
    quad = ((-1.0, 0.0, -1.0), (1.0, 0.0, -1.0), (1.0, 0.0, 1.0), (-1.0, 0.0, 1.0))
    return _from_max((quad,), _QUAD_UVS, (1,), scale_to_meters)


def unit_cube(scale_to_meters: float = 1.0) -> MeshGroup:
    """A cube spanning [-1, 1]^3, flat-shaded, matching Mitsuba's `cube`.

    Every face carries smoothing group 0 — Max's spelling of "hard edge" — so the corners
    split and each face keeps its own normal. A shared smoothing group would round the
    cube's edges into something that looks like a bug in the normal code.
    """
    faces = (
        (((-1, -1, 1), (1, -1, 1), (1, 1, 1), (-1, 1, 1))),        # +Z_max
        (((1, -1, -1), (-1, -1, -1), (-1, 1, -1), (1, 1, -1))),    # -Z_max
        (((1, -1, 1), (1, -1, -1), (1, 1, -1), (1, 1, 1))),        # +X_max
        (((-1, -1, -1), (-1, -1, 1), (-1, 1, 1), (-1, 1, -1))),    # -X_max
        (((-1, 1, 1), (1, 1, 1), (1, 1, -1), (-1, 1, -1))),        # +Y_max
        (((-1, -1, -1), (1, -1, -1), (1, -1, 1), (-1, -1, 1))),    # -Y_max
    )
    quads = tuple(tuple((float(a), float(b), float(c)) for a, b, c in face)
                  for face in faces)
    return _from_max(quads, _QUAD_UVS, (0,) * 6, scale_to_meters)


def uv_sphere(rings: int = 64, segments: int = 128) -> MeshGroup:
    """A unit sphere at the origin. Fine enough that tessellation is not the error term.

    The furnace test asserts to 1e-3, so a coarse sphere's faceting would show up as a
    silhouette artefact and be misread as an energy bug.
    """
    positions: list[float] = []
    normals: list[float] = []
    uvs: list[float] = []
    for ring in range(rings + 1):
        v = ring / rings
        theta = v * math.pi
        for seg in range(segments + 1):
            u = seg / segments
            phi = u * 2.0 * math.pi
            x = math.sin(theta) * math.cos(phi)
            y = math.cos(theta)
            z = math.sin(theta) * math.sin(phi)
            positions.extend((x, y, z))
            normals.extend((x, y, z))
            uvs.extend((u, 1.0 - v))

    indices: list[int] = []
    stride = segments + 1
    for ring in range(rings):
        for seg in range(segments):
            a = ring * stride + seg
            b = a + stride
            indices.extend([a, b, a + 1, a + 1, b, b + 1])
    return _group(positions, normals, uvs, indices)


# --------------------------------------------------------------------------------------
# a minimal PNG writer, for the chirality texture
# --------------------------------------------------------------------------------------


def write_png(pixels: list[list[tuple[int, int, int]]]) -> bytes:
    """8-bit RGB PNG from a row-major list of rows. Row 0 is the **top** of the image.

    Hand-rolled because the fixtures must be buildable with the standard library alone —
    the same constraint that shapes `core.meshbuild`, and for the same reason: this code
    has to be runnable wherever the project is.
    """
    height = len(pixels)
    width = len(pixels[0])
    raw = bytearray()
    for row in pixels:
        raw.append(0)   # filter type 0 (None)
        for r, g, b in row:
            raw.extend((r & 0xFF, g & 0xFF, b & 0xFF))

    def chunk(tag: bytes, payload: bytes) -> bytes:
        return (struct.pack(">I", len(payload)) + tag + payload
                + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF))

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(bytes(raw), 9))
        + chunk(b"IEND", b"")
    )


CORNER_COLORS = {
    "uv00": (255, 0, 0),      # red    at UV (0, 0)
    "uv10": (0, 255, 0),      # green  at UV (1, 0)
    "uv01": (0, 0, 255),      # blue   at UV (0, 1)
    "uv11": (255, 255, 0),    # yellow at UV (1, 1)
}


def corner_marker_texture(size: int = 64) -> bytes:
    """Four distinctly coloured quadrants, one per UV corner.

    Distinct *hues* rather than a checkerboard, because the assertion is "which colour
    landed in which screen corner" and a hue survives tone mapping, filtering and Monte
    Carlo noise far better than a pattern does.

    PNG row 0 is the top of the image, and the top of a texture is V = 1.
    """
    half = size // 2
    rows: list[list[tuple[int, int, int]]] = []
    for y in range(size):
        top = y < half
        row: list[tuple[int, int, int]] = []
        for x in range(size):
            left = x < half
            if top:
                row.append(CORNER_COLORS["uv01"] if left else CORNER_COLORS["uv11"])
            else:
                row.append(CORNER_COLORS["uv00"] if left else CORNER_COLORS["uv10"])
        rows.append(row)
    return write_png(rows)


# --------------------------------------------------------------------------------------
# scene 1 — chirality and UV orientation
# --------------------------------------------------------------------------------------


def chirality(assets: AssetStore) -> Scene:
    """A textured quad facing the camera, lit only by a constant environment.

    Each UV corner is a different hue, so a horizontally mirrored image (the `look_at`
    trap) and a vertically flipped one (the UV trap) are both detectable by sampling four
    pixels. `tools/run_golden.py` asserts the mapping; nothing here needs to be looked at.

    The environment is `constant` and the material purely diffuse, so the rendered colour of
    each quadrant is the texture colour times 1/pi times pi — the albedo itself — with no
    lighting gradient to confuse the sampling.
    """
    ply = assets.add_bytes(write_ply(unit_quad()), subdir="meshes", ext=".ply",
                           source="golden/chirality quad")
    tex = assets.add_bytes(corner_marker_texture(), subdir="textures", ext=".png",
                           source="golden/chirality corner markers")

    return Scene(
        camera=Camera(
            to_world=look_at_matrix((0.0, 0.0, 4.0), (0.0, 0.0, 0.0), (0.0, 1.0, 0.0)),
            fov_deg=45.0,
            fov_axis="x",
            film_width=128,
            film_height=128,
        ),
        settings=RenderSettings(integrator="path", max_depth=2, spp_per_pass=32, passes=2),
        meshes=(Mesh(id="quad", name="ChiralityQuad", material_id="marker",
                     positions_path=ply, to_world=_identity()),),
        materials=(Material(
            id="marker",
            name="CornerMarkers",
            kind="principled",
            params={"base_color": TextureRef(path=tex, raw=False),
                    "roughness": 1.0, "metallic": 0.0, "specular": 0.0},
        ),),
        environment=Environment(kind="constant", radiance_rgb=(1.0, 1.0, 1.0)),
    )


# --------------------------------------------------------------------------------------
# scene 2 — white furnace
# --------------------------------------------------------------------------------------


def white_furnace(assets: AssetStore) -> Scene:
    """A perfectly white Lambertian sphere inside a uniform environment of radiance 1.

    Energy conservation says the sphere reflects exactly as much as it receives, so it
    disappears against the background and every pixel reads 1.0. The test is
    `max |image − 1| < tolerance`, which catches a missing `twosided`, a wrong albedo
    scale, a lost factor of pi, and a unit error, all at once — and tells you nothing about
    which, which is fine, because it firing at all means stop and look.

    `max_depth` and `rr_depth` are both 64: truncating the path early loses the tail of the
    interreflection series and darkens the sphere by a visible, and entirely artificial,
    amount.
    """
    ply = assets.add_bytes(write_ply(uv_sphere()), subdir="meshes", ext=".ply",
                           source="golden/furnace sphere")
    return Scene(
        camera=Camera(
            to_world=look_at_matrix((0.0, 0.0, 4.0), (0.0, 0.0, 0.0), (0.0, 1.0, 0.0)),
            fov_deg=45.0,
            fov_axis="x",
            film_width=64,
            film_height=64,
        ),
        settings=RenderSettings(integrator="path", max_depth=64, rr_depth=64,
                                spp_per_pass=512, passes=8),
        meshes=(Mesh(id="sphere", name="FurnaceSphere", material_id="white",
                     positions_path=ply, to_world=_identity()),),
        materials=(Material(id="white", name="PerfectWhite", kind="diffuse_placeholder",
                            params={"reflectance": (1.0, 1.0, 1.0)}, two_sided=True),),
        environment=Environment(kind="constant", radiance_rgb=(1.0, 1.0, 1.0)),
    )


# --------------------------------------------------------------------------------------
# scene 3 — transform torture
# --------------------------------------------------------------------------------------

TORTURE_NODES: tuple[tuple[str, tuple[float, float, float], tuple[float, float, float],
                           tuple[float, float, float]], ...] = (
    # name,            translate (Max),       rotate ZYX deg,      scale
    ("plain",          (0.0, 0.0, 0.0),       (0.0, 0.0, 0.0),     (1.0, 1.0, 1.0)),
    ("offset_rotated", (60.0, 0.0, 20.0),     (35.0, 0.0, 0.0),    (1.0, 1.0, 1.0)),
    ("nonuniform",     (-60.0, 0.0, 20.0),    (0.0, 25.0, 0.0),    (2.0, 0.5, 1.0)),
    ("mirrored",       (0.0, 0.0, -45.0),     (0.0, 0.0, 40.0),    (-1.0, 1.0, 1.0)),
)
"""Max-space transforms, deliberately chosen so `C·T` and `C·T·C⁻¹` disagree.

`plain` is the control: it is at the origin and unrotated, which is exactly the case where
the wrong formula still looks right. The other three are each off-origin *and* rotated,
which is the combination that separates them.
"""

TORTURE_SCALE_TO_METERS = 0.01
"""The fixture is authored in centimetres, so the scale conversion is under test too."""


def transform_torture(assets: AssetStore) -> Scene:
    """Emissive cubes placed through the conjugation, one per pathological transform.

    Each cube is its own emitter, so `tools/run_golden.py` can locate it by brightness
    without any lighting to model. The assertion is that the centroid of each bright blob
    matches a pixel coordinate computed analytically from the camera — a check that fails
    for a mirrored image, a transposed matrix, a missing scale conversion, or the wrong
    conjugation, and passes only if all four are right.

    The `mirrored` node has negative determinant. It must still render as a solid lit box:
    if the winding reversal is missing it renders inside-out and the emitter faces away.
    """
    # The base object is a 1-unit cube in a centimetre scene, so its object-space geometry
    # is +/-0.01 m. The node scale below is a pure multiplier on top of that, which is what
    # makes `_scaled_conjugate` correct in scaling only the translation column.
    ply = assets.add_bytes(write_ply(unit_cube(TORTURE_SCALE_TO_METERS)),
                           subdir="meshes", ext=".ply", source="golden/torture cube")

    meshes: list[Mesh] = []
    for name, translate, rotate, scale in TORTURE_NODES:
        # Built in Max's own convention — centimetres, Z-up — then converted exactly as
        # the real exporter would, so the fixture tests the conversion rather than
        # restating it.
        t_max = compose_trs(
            translate,
            rotate,
            (scale[0] * 6.0, scale[1] * 6.0, scale[2] * 6.0),
        )
        to_world = _scaled_conjugate(t_max, TORTURE_SCALE_TO_METERS)
        # No `flip_normals`, deliberately. This is the object-space path: the mirror
        # lives in `to_world`, the PLY carries explicit outward normals, and Mitsuba
        # transforms those by the inverse transpose — which handles a negative
        # determinant correctly on its own. Setting the flag here renders the cube
        # black, which is how this was established.
        meshes.append(Mesh(id=name, name=f"Torture_{name}", material_id="emissive",
                           positions_path=ply, to_world=to_world))

    return Scene(
        camera=Camera(
            to_world=look_at_matrix((0.0, 0.05, 1.7), (0.0, 0.0, 0.0), (0.0, 1.0, 0.0)),
            fov_deg=60.0,
            fov_axis="x",
            film_width=200,
            film_height=150,
        ),
        settings=RenderSettings(integrator="path", max_depth=2, spp_per_pass=32, passes=2),
        meshes=tuple(meshes),
        materials=(Material(
            id="emissive",
            name="Marker",
            kind="principled",
            params={"base_color": (0.0, 0.0, 0.0), "roughness": 1.0},
            emission=Emission(radiance_rgb=(8.0, 8.0, 8.0)),
        ),),
        scene_scale_to_meters=TORTURE_SCALE_TO_METERS,
    )


def _scaled_conjugate(t_max: tuple[float, ...], scale_to_meters: float) -> tuple[float, ...]:
    """`C·T·C⁻¹` with the translation converted from system units to metres.

    The conjugation handles the axis change; the unit conversion is separate because only
    the translation column carries a length. Scaling the linear part too would resize every
    object by the scene scale, which on a centimetre scene shrinks the world by 100.
    """
    conj = conjugate(t_max)
    out = list(conj)
    for row in range(3):
        out[row * 4 + 3] *= scale_to_meters
    return tuple(out)


# --------------------------------------------------------------------------------------
# scene 4 — Cornell box
# --------------------------------------------------------------------------------------

_CORNELL_WHITE = (0.885809, 0.698859, 0.666422)
_CORNELL_GREEN = (0.105421, 0.37798, 0.076425)
_CORNELL_RED = (0.570068, 0.0430135, 0.0443706)
_CORNELL_RADIANCE = (18.387, 13.9873, 6.75357)

_CORNELL_RECTS: tuple[tuple[str, str, tuple[float, ...]], ...] = (
    ("floor", "white", (1, 0, 0, 0, 0, 0, 1, -1, 0, -1, 0, 0, 0, 0, 0, 1)),
    ("ceiling", "white", (1, 0, 0, 0, 0, 0, -1, 1, 0, 1, 0, 0, 0, 0, 0, 1)),
    ("back", "white", (1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, -1, 0, 0, 0, 1)),
    ("green_wall", "green", (0, 0, -1, 1, 0, 1, 0, 0, 1, 0, 0, 0, 0, 0, 0, 1)),
    ("red_wall", "red", (0, 0, 1, -1, 0, 1, 0, 0, -1, 0, 0, 0, 0, 0, 0, 1)),
    ("light", "emissive", (0.23, 0, 0, 0, 0, 0, -0.19, 0.99, 0, 0.19, 0, 0.01,
                           0, 0, 0, 1)),
)

_CORNELL_CUBES: tuple[tuple[str, tuple[float, ...]], ...] = (
    ("small_box", (0.2869, 0, -0.0877, 0.335, 0, 0.3, 0, -0.7, 0.0877, 0, 0.2869, 0.38,
                   0, 0, 0, 1)),
    ("large_box", (0.2849, 0, 0.0939, -0.33, 0, 0.61, 0, -0.4, -0.0939, 0, 0.2849, -0.28,
                   0, 0, 0, 1)),
)


def cornell_box(assets: AssetStore) -> Scene:
    """A byte-for-byte replica of `mi.cornell_box()`, expressed entirely as IR.

    The transforms and reflectances above were read out of Mitsuba's own scene rather than
    transcribed from the original Cornell measurements, because the point of the test is
    that *this project's IR and emitters* reproduce a known-good scene — not that the
    Cornell box is correctly dimensioned.

    What it actually exercises, and nothing else does: `to_world` on shapes, the sensor's
    `look_at` decomposition, area emitters attached through a material, `twosided`
    wrapping, and the PLY path — all at once, against a reference produced by code this
    project did not write.
    """
    quad = assets.add_bytes(write_ply(unit_quad()), subdir="meshes", ext=".ply",
                            source="golden/cornell quad")
    cube = assets.add_bytes(write_ply(unit_cube()), subdir="meshes", ext=".ply",
                            source="golden/cornell cube")

    meshes = [
        Mesh(id=name, name=f"Cornell_{name}", material_id=material,
             positions_path=quad, to_world=matrix)
        for name, material, matrix in _CORNELL_RECTS
    ]
    meshes += [
        Mesh(id=name, name=f"Cornell_{name}", material_id="white",
             positions_path=cube, to_world=matrix)
        for name, matrix in _CORNELL_CUBES
    ]

    return Scene(
        camera=Camera(
            # Mitsuba's own sensor matrix, expressed as the look_at that produces it: at
            # +3.9 on Z, facing the origin, +Y up.
            to_world=look_at_matrix((0.0, 0.0, 3.9), (0.0, 0.0, 0.0), (0.0, 1.0, 0.0)),
            fov_deg=39.3077,
            fov_axis="smaller",
            near_clip=0.001,
            far_clip=100.0,
            film_width=128,
            film_height=128,
        ),
        settings=RenderSettings(integrator="path", max_depth=8, spp_per_pass=128, passes=4),
        meshes=tuple(meshes),
        materials=(
            Material(id="white", name="White", kind="diffuse_placeholder",
                     params={"reflectance": _CORNELL_WHITE}),
            Material(id="green", name="Green", kind="diffuse_placeholder",
                     params={"reflectance": _CORNELL_GREEN}),
            Material(id="red", name="Red", kind="diffuse_placeholder",
                     params={"reflectance": _CORNELL_RED}),
            Material(id="emissive", name="Light", kind="diffuse_placeholder",
                     params={"reflectance": _CORNELL_WHITE},
                     emission=Emission(radiance_rgb=_CORNELL_RADIANCE)),
        ),
    )


# --------------------------------------------------------------------------------------
# registry
# --------------------------------------------------------------------------------------

SCENES = {
    "chirality": chirality,
    "white_furnace": white_furnace,
    "transform_torture": transform_torture,
    "cornell_box": cornell_box,
}


def _identity() -> tuple[float, ...]:
    return (1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0)


def build_all(root: Path) -> dict[str, Scene]:
    """Write every fixture's assets under `root` and return the scenes.

    Deterministic: the same inputs produce the same PLY bytes, therefore the same content
    hashes, therefore the same JSON. That property is what `tests/test_golden.py` asserts
    against the checked-in fixtures.
    """
    assets = AssetStore(root=Path(root))
    scenes = {name: builder(assets) for name, builder in SCENES.items()}
    assets.write_manifest()
    return scenes
