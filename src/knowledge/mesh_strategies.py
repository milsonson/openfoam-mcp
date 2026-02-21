"""OpenFOAM Knowledge Base - Mesh generation strategies."""

from typing import Dict, Any, Optional, List, Tuple
from dataclasses import dataclass, field
from enum import Enum
import math


class MeshStrategy(str, Enum):
    """Available mesh generation strategies."""
    BLOCK_MESH = "blockMesh"
    SNAPPY_HEX_MESH = "snappyHexMesh"


class GeometryType(str, Enum):
    """Types of geometry that can be meshed."""
    PIPE = "pipe"
    BOX = "box"
    CYLINDER = "cylinder"
    CAVITY = "cavity"
    EXTERNAL_FLOW = "external_flow"
    CUSTOM = "custom"


@dataclass
class MeshQualitySettings:
    """Mesh quality parameters."""
    max_non_orthogonality: float = 65.0
    max_skewness: float = 4.0
    min_volume: float = 1e-13
    min_tet_quality: float = -1e30
    min_face_weight: float = 0.05


@dataclass
class MeshResolution:
    """Mesh resolution settings."""
    cells_per_diameter: int = 20
    boundary_layer_cells: int = 5
    expansion_ratio: float = 1.2
    first_layer_thickness: Optional[float] = None


@dataclass
class BlockMeshParams:
    """Parameters for blockMesh generation."""
    # Domain bounds
    x_min: float = 0.0
    x_max: float = 1.0
    y_min: float = 0.0
    y_max: float = 1.0
    z_min: float = 0.0
    z_max: float = 1.0

    # Cell counts
    n_cells_x: int = 20
    n_cells_y: int = 20
    n_cells_z: int = 1

    # Grading (expansion ratios)
    grading_x: float = 1.0
    grading_y: float = 1.0
    grading_z: float = 1.0

    # Boundary patches
    patches: Dict[str, Dict[str, Any]] = field(default_factory=dict)

    def __post_init__(self):
        if not self.patches:
            self.patches = {
                "inlet": {"type": "patch", "faces": []},
                "outlet": {"type": "patch", "faces": []},
                "walls": {"type": "wall", "faces": []},
                "frontAndBack": {"type": "empty", "faces": []},
            }


@dataclass
class RefinementRegion:
    """Definition of a refinement region for snappyHexMesh."""
    name: str
    type: str  # "box", "sphere", "cylinder", "surface"
    level: int  # refinement level
    # Box parameters
    min_point: Optional[Tuple[float, float, float]] = None
    max_point: Optional[Tuple[float, float, float]] = None
    # Sphere parameters
    centre: Optional[Tuple[float, float, float]] = None
    radius: Optional[float] = None
    # Cylinder parameters
    point1: Optional[Tuple[float, float, float]] = None
    point2: Optional[Tuple[float, float, float]] = None
    # Surface parameters
    surface_name: Optional[str] = None


@dataclass
class AddLayersControls:
    """Boundary layer mesh parameters for snappyHexMesh."""
    # Layer settings
    relative_sizes: bool = True
    expansion_ratio: float = 1.2
    final_layer_thickness: float = 0.3
    min_thickness: float = 0.1
    n_grow: int = 0
    feature_angle: float = 60.0
    slip_feature_angle: float = 30.0
    n_relax_iter: int = 5
    n_smooth_surface_normals: int = 1
    n_smooth_normals: int = 3
    n_smooth_thickness: int = 10
    max_face_thickness_ratio: float = 0.5
    max_thickness_to_medial_ratio: float = 0.3
    min_median_axis_angle: float = 90.0
    n_buffer_cells_no_extrude: int = 0
    n_layer_iter: int = 50
    # Layer specification for each patch
    layers: Dict[str, Dict[str, Any]] = field(default_factory=dict)

    def __post_init__(self):
        if not self.layers:
            self.layers = {}


@dataclass
class SnappyHexMeshParams:
    """Parameters for snappyHexMesh generation."""
    # STL geometry files
    stl_files: List[Dict[str, Any]] = field(default_factory=list)
    # Background mesh bounds (for blockMesh)
    x_min: float = -1.0
    x_max: float = 1.0
    y_min: float = -1.0
    y_max: float = 1.0
    z_min: float = -1.0
    z_max: float = 1.0
    # Background mesh cell counts
    n_cells_x: int = 20
    n_cells_y: int = 20
    n_cells_z: int = 20
    # Point inside mesh (locationInMesh)
    location_in_mesh: Tuple[float, float, float] = (0.0, 0.0, 0.0)
    # Castellated mesh controls
    max_local_cells: int = 100000
    max_global_cells: int = 2000000
    min_refinement_cells: int = 10
    max_load_unbalance: float = 0.1
    n_cells_between_levels: int = 3
    resolve_feature_angle: float = 30.0
    allow_free_standing_zone_faces: bool = True
    # Surface refinement level (min, max)
    surface_refinement_level: Tuple[int, int] = (2, 3)
    # Refinement regions
    refinement_regions: List[RefinementRegion] = field(default_factory=list)
    # Snap controls
    n_smooth_patch: int = 3
    tolerance: float = 2.0
    n_solve_iter: int = 100
    n_relax_iter: int = 5
    n_feature_snap_iter: int = 10
    implicit_feature_snap: bool = False
    explicit_feature_snap: bool = True
    multi_region_feature_snap: bool = False
    # Add layers controls
    add_layers: bool = True
    add_layers_controls: AddLayersControls = field(default_factory=AddLayersControls)
    # Mesh quality settings
    mesh_quality: MeshQualitySettings = field(default_factory=MeshQualitySettings)
    # Phases to run
    castellated_mesh: bool = True
    snap: bool = True
    add_layers_phase: bool = True

    def __post_init__(self):
        if not self.stl_files:
            self.stl_files = []
        if not self.refinement_regions:
            self.refinement_regions = []


def calculate_mesh_resolution(
    characteristic_length: float,
    reynolds_number: float,
    geometry_type: GeometryType
) -> MeshResolution:
    """
    Calculate recommended mesh resolution based on flow parameters.

    Args:
        characteristic_length: Characteristic length scale (m)
        reynolds_number: Flow Reynolds number
        geometry_type: Type of geometry

    Returns:
        Recommended mesh resolution settings
    """
    # Base resolution on Reynolds number
    if reynolds_number < 2000:
        # Laminar flow
        cells_per_diameter = 15
        bl_cells = 3
    elif reynolds_number < 10000:
        # Transitional
        cells_per_diameter = 25
        bl_cells = 5
    else:
        # Turbulent
        cells_per_diameter = 40
        bl_cells = 10

    # Adjust for geometry type
    if geometry_type == GeometryType.EXTERNAL_FLOW:
        cells_per_diameter = int(cells_per_diameter * 1.5)
        bl_cells = int(bl_cells * 1.5)

    # Calculate first layer thickness for turbulent flows
    first_layer = None
    if reynolds_number > 2000:
        # Estimate y+ = 1 first layer thickness
        # y = y+ * nu / u_tau, u_tau ~ 0.05 * U
        # Simplified estimate
        first_layer = characteristic_length / (reynolds_number ** 0.9) * 5

    return MeshResolution(
        cells_per_diameter=cells_per_diameter,
        boundary_layer_cells=bl_cells,
        expansion_ratio=1.2,
        first_layer_thickness=first_layer
    )


def select_mesh_strategy(
    geometry_type: GeometryType,
    has_stl: bool = False
) -> MeshStrategy:
    """
    Select appropriate mesh generation strategy.

    Args:
        geometry_type: Type of geometry
        has_stl: Whether STL geometry file is available

    Returns:
        Recommended mesh strategy
    """
    if has_stl:
        return MeshStrategy.SNAPPY_HEX_MESH

    simple_geometries = {
        GeometryType.PIPE,
        GeometryType.BOX,
        GeometryType.CAVITY,
    }

    if geometry_type in simple_geometries:
        return MeshStrategy.BLOCK_MESH

    return MeshStrategy.SNAPPY_HEX_MESH


def create_pipe_mesh_params(
    diameter: float,
    length: float,
    n_radial: int = 20,
    n_axial: int = 50,
    is_2d: bool = False
) -> BlockMeshParams:
    """
    Create blockMesh parameters for a pipe geometry.

    Args:
        diameter: Pipe diameter (m)
        length: Pipe length (m)
        n_radial: Number of cells in radial direction
        n_axial: Number of cells in axial direction
        is_2d: Whether to create a 2D axisymmetric mesh

    Returns:
        BlockMesh parameters
    """
    radius = diameter / 2

    if is_2d:
        # 2D planar case
        params = BlockMeshParams(
            x_min=0.0,
            x_max=length,
            y_min=-radius,
            y_max=radius,
            z_min=0.0,
            z_max=0.1,  # Small thickness for 2D
            n_cells_x=n_axial,
            n_cells_y=n_radial,
            n_cells_z=1,
            patches={
                "inlet": {"type": "patch", "faces": ["x_min"]},
                "outlet": {"type": "patch", "faces": ["x_max"]},
                "walls": {"type": "wall", "faces": ["y_min", "y_max"]},
                "frontAndBack": {"type": "empty", "faces": ["z_min", "z_max"]},
            }
        )
    else:
        # 3D case - use wedge for axisymmetric or full 3D
        n_circumferential = max(int(n_radial * math.pi), 20)
        params = BlockMeshParams(
            x_min=0.0,
            x_max=length,
            y_min=-radius,
            y_max=radius,
            z_min=-radius,
            z_max=radius,
            n_cells_x=n_axial,
            n_cells_y=n_radial,
            n_cells_z=n_circumferential,
            patches={
                "inlet": {"type": "patch", "faces": ["x_min"]},
                "outlet": {"type": "patch", "faces": ["x_max"]},
                "walls": {"type": "wall", "faces": ["y_min", "y_max", "z_min", "z_max"]},
            }
        )

    return params


def create_cavity_mesh_params(
    width: float,
    height: float,
    depth: float = 0.1,
    n_x: int = 50,
    n_y: int = 50,
    is_2d: bool = True
) -> BlockMeshParams:
    """
    Create blockMesh parameters for a cavity geometry.

    Args:
        width: Cavity width (m)
        height: Cavity height (m)
        depth: Cavity depth for 3D (m)
        n_x: Number of cells in x direction
        n_y: Number of cells in y direction
        is_2d: Whether to create a 2D mesh

    Returns:
        BlockMesh parameters
    """
    n_z = 1 if is_2d else max(int(n_x * depth / width), 10)

    return BlockMeshParams(
        x_min=0.0,
        x_max=width,
        y_min=0.0,
        y_max=height,
        z_min=0.0,
        z_max=depth,
        n_cells_x=n_x,
        n_cells_y=n_y,
        n_cells_z=n_z,
        patches={
            "movingWall": {"type": "wall", "faces": ["y_max"]},
            "fixedWalls": {"type": "wall", "faces": ["x_min", "x_max", "y_min"]},
            "frontAndBack": {"type": "empty" if is_2d else "wall", "faces": ["z_min", "z_max"]},
        }
    )


def create_external_flow_mesh_params(
    object_size: float,
    domain_length: float = None,
    domain_width: float = None,
    domain_height: float = None,
    n_cells_base: int = 50
) -> BlockMeshParams:
    """
    Create blockMesh parameters for external flow around an object.

    Args:
        object_size: Characteristic size of the object (m)
        domain_length: Total domain length (default: 20 * object_size)
        domain_width: Total domain width (default: 10 * object_size)
        domain_height: Total domain height (default: 10 * object_size)
        n_cells_base: Base cell count

    Returns:
        BlockMesh parameters
    """
    if domain_length is None:
        domain_length = 20 * object_size
    if domain_width is None:
        domain_width = 10 * object_size
    if domain_height is None:
        domain_height = 10 * object_size

    # Object centered at origin, inlet at negative x
    x_inlet = -5 * object_size
    x_outlet = domain_length - 5 * object_size

    return BlockMeshParams(
        x_min=x_inlet,
        x_max=x_outlet,
        y_min=-domain_width / 2,
        y_max=domain_width / 2,
        z_min=-domain_height / 2,
        z_max=domain_height / 2,
        n_cells_x=int(n_cells_base * domain_length / object_size / 5),
        n_cells_y=n_cells_base,
        n_cells_z=n_cells_base,
        patches={
            "inlet": {"type": "patch", "faces": ["x_min"]},
            "outlet": {"type": "patch", "faces": ["x_max"]},
            "top": {"type": "symmetry", "faces": ["y_max"]},
            "bottom": {"type": "symmetry", "faces": ["y_min"]},
            "frontAndBack": {"type": "empty", "faces": ["z_min", "z_max"]},
        }
    )


def estimate_cell_count(params: BlockMeshParams) -> int:
    """Estimate total cell count from blockMesh parameters."""
    return params.n_cells_x * params.n_cells_y * params.n_cells_z


def create_snappy_hex_mesh_params(
    stl_path: str,
    stl_name: str = "geometry",
    domain_bounds: Dict[str, float] = None,
    location_in_mesh: Tuple[float, float, float] = None,
    surface_refinement: Tuple[int, int] = (2, 3),
    refinement_regions: List[Dict[str, Any]] = None,
    add_boundary_layers: bool = True,
    layer_patches: List[str] = None,
    n_surface_layers: int = 3,
    layer_expansion_ratio: float = 1.2,
    final_layer_thickness: float = 0.3,
    background_cell_size: float = None,
    max_cells: int = 2000000,
) -> SnappyHexMeshParams:
    """
    Create snappyHexMesh parameters from user inputs.

    Args:
        stl_path: Path to the STL geometry file
        stl_name: Name for the geometry in the mesh
        domain_bounds: Dictionary with x_min, x_max, y_min, y_max, z_min, z_max
        location_in_mesh: Point inside the mesh region to keep
        surface_refinement: (min_level, max_level) for surface refinement
        refinement_regions: List of refinement region definitions
        add_boundary_layers: Whether to add boundary layers
        layer_patches: Patch names to add boundary layers on
        n_surface_layers: Number of boundary layer cells
        layer_expansion_ratio: Expansion ratio for boundary layers
        final_layer_thickness: Final layer thickness (relative)
        background_cell_size: Target background cell size
        max_cells: Maximum number of cells

    Returns:
        SnappyHexMeshParams configured for the geometry
    """
    # Default domain bounds
    if domain_bounds is None:
        domain_bounds = {
            "x_min": -1.0, "x_max": 1.0,
            "y_min": -1.0, "y_max": 1.0,
            "z_min": -1.0, "z_max": 1.0
        }

    # Calculate background mesh resolution
    domain_length = max(
        domain_bounds["x_max"] - domain_bounds["x_min"],
        domain_bounds["y_max"] - domain_bounds["y_min"],
        domain_bounds["z_max"] - domain_bounds["z_min"]
    )

    if background_cell_size is None:
        background_cell_size = domain_length / 20

    n_cells_x = max(5, int((domain_bounds["x_max"] - domain_bounds["x_min"]) / background_cell_size))
    n_cells_y = max(5, int((domain_bounds["y_max"] - domain_bounds["y_min"]) / background_cell_size))
    n_cells_z = max(5, int((domain_bounds["z_max"] - domain_bounds["z_min"]) / background_cell_size))

    # Default location in mesh (center of domain)
    if location_in_mesh is None:
        location_in_mesh = (
            (domain_bounds["x_min"] + domain_bounds["x_max"]) / 2,
            (domain_bounds["y_min"] + domain_bounds["y_max"]) / 2,
            (domain_bounds["z_min"] + domain_bounds["z_max"]) / 2
        )

    # Create STL file entry
    stl_files = [{
        "path": stl_path,
        "name": stl_name,
        "type": "triSurfaceMesh",
        "refinement_level": surface_refinement,
        "patches": {}
    }]

    # Parse refinement regions
    parsed_regions = []
    if refinement_regions:
        for region in refinement_regions:
            region_type = region.get("type", "box")
            if region_type == "box":
                parsed_regions.append(RefinementRegion(
                    name=region.get("name", f"region_{len(parsed_regions)}"),
                    type="box",
                    level=region.get("level", 2),
                    min_point=tuple(region.get("min", [-0.5, -0.5, -0.5])),
                    max_point=tuple(region.get("max", [0.5, 0.5, 0.5]))
                ))
            elif region_type == "sphere":
                parsed_regions.append(RefinementRegion(
                    name=region.get("name", f"region_{len(parsed_regions)}"),
                    type="sphere",
                    level=region.get("level", 2),
                    centre=tuple(region.get("centre", [0, 0, 0])),
                    radius=region.get("radius", 0.5)
                ))
            elif region_type == "cylinder":
                parsed_regions.append(RefinementRegion(
                    name=region.get("name", f"region_{len(parsed_regions)}"),
                    type="cylinder",
                    level=region.get("level", 2),
                    point1=tuple(region.get("point1", [0, 0, -0.5])),
                    point2=tuple(region.get("point2", [0, 0, 0.5])),
                    radius=region.get("radius", 0.5)
                ))

    # Setup boundary layers
    add_layers_controls = AddLayersControls(
        expansion_ratio=layer_expansion_ratio,
        final_layer_thickness=final_layer_thickness,
        layers={}
    )

    if add_boundary_layers and layer_patches:
        for patch in layer_patches:
            add_layers_controls.layers[patch] = {
                "nSurfaceLayers": n_surface_layers
            }

    return SnappyHexMeshParams(
        stl_files=stl_files,
        x_min=domain_bounds["x_min"],
        x_max=domain_bounds["x_max"],
        y_min=domain_bounds["y_min"],
        y_max=domain_bounds["y_max"],
        z_min=domain_bounds["z_min"],
        z_max=domain_bounds["z_max"],
        n_cells_x=n_cells_x,
        n_cells_y=n_cells_y,
        n_cells_z=n_cells_z,
        location_in_mesh=location_in_mesh,
        max_global_cells=max_cells,
        surface_refinement_level=surface_refinement,
        refinement_regions=parsed_regions,
        add_layers=add_boundary_layers,
        add_layers_controls=add_layers_controls,
    )


def validate_mesh_quality(
    params: BlockMeshParams,
    quality_settings: MeshQualitySettings = None
) -> List[str]:
    """
    Validate mesh parameters against quality criteria.

    Returns:
        List of warning messages (empty if all OK)
    """
    if quality_settings is None:
        quality_settings = MeshQualitySettings()

    warnings = []

    # Check aspect ratios
    dx = (params.x_max - params.x_min) / params.n_cells_x
    dy = (params.y_max - params.y_min) / params.n_cells_y
    dz = (params.z_max - params.z_min) / params.n_cells_z if params.n_cells_z > 1 else dx

    max_aspect = max(dx, dy, dz) / min(dx, dy, dz)
    if max_aspect > 20:
        warnings.append(f"高纵横比警告: {max_aspect:.1f} > 20，可能影响求解精度")

    # Check total cell count
    total_cells = estimate_cell_count(params)
    if total_cells > 10_000_000:
        warnings.append(f"网格数量较大: {total_cells:,} 单元，可能需要较长计算时间")
    elif total_cells < 1000:
        warnings.append(f"网格数量较少: {total_cells:,} 单元，可能精度不足")

    return warnings


def validate_snappy_params(params: SnappyHexMeshParams) -> List[str]:
    """
    Validate snappyHexMesh parameters.

    Returns:
        List of warning/error messages (empty if all OK)
    """
    warnings = []

    # Check STL files
    if not params.stl_files:
        warnings.append("错误: 未指定STL几何文件")

    # Check location in mesh
    x, y, z = params.location_in_mesh
    if not (params.x_min < x < params.x_max and
            params.y_min < y < params.y_max and
            params.z_min < z < params.z_max):
        warnings.append("警告: locationInMesh 点可能在域边界之外")

    # Check cell count estimates
    base_cells = params.n_cells_x * params.n_cells_y * params.n_cells_z
    if base_cells > params.max_global_cells:
        warnings.append(f"警告: 背景网格单元数 ({base_cells:,}) 超过最大单元数限制 ({params.max_global_cells:,})")

    # Check refinement levels
    min_level, max_level = params.surface_refinement_level
    if max_level > 8:
        warnings.append(f"警告: 表面加密级别 ({max_level}) 较高，可能导致单元数量爆炸")

    # Check boundary layers
    if params.add_layers and not params.add_layers_controls.layers:
        warnings.append("警告: 启用了边界层但未指定任何边界层面片")

    return warnings
