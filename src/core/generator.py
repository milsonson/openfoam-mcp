"""OpenFOAM configuration file generator."""

from typing import Dict, Any, List, Optional
from pathlib import Path
from dataclasses import dataclass
import os
import re

from ..io_utils import atomic_write_text
from ..knowledge import (
    FlowType, TimeType, TurbulenceType,
    select_solver, estimate_reynolds_number, recommend_turbulence_model, get_solver_info,
    get_fluid_properties,
    BoundaryType, BoundaryDefinition, get_boundary_conditions,
    BlockMeshParams, create_pipe_mesh_params, create_cavity_mesh_params,
    create_external_flow_mesh_params, estimate_cell_count,
    SnappyHexMeshParams, create_snappy_hex_mesh_params,
    RefinementRegion, AddLayersControls,
)
from ..templates import CaseTemplate, get_template
from .parallel import _factorize_processors


def _coerce_float_scalar(value: Any, parameter_name: str) -> float:
    """Coerce numeric scalar while rejecting bool/string injections."""
    if isinstance(value, bool):
        raise ValueError(f"{parameter_name} 必须是数字，不能是布尔值")
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{parameter_name} 必须是数字，当前值: {value}") from exc


_SAFE_SCRIPT_SOLVER_RE = re.compile(r"^[A-Za-z][A-Za-z0-9]*$")


def _sanitize_solver_token_for_script(solver: str) -> str:
    """Validate solver token before embedding into generated shell scripts."""
    token = str(solver).strip()
    if not _SAFE_SCRIPT_SOLVER_RE.fullmatch(token):
        raise ValueError(f"非法求解器名称，无法写入 Allrun: {solver}")
    return token


@dataclass
class CaseConfig:
    """Complete configuration for an OpenFOAM case."""
    case_path: Path
    solver: str
    flow_type: FlowType
    time_type: TimeType
    turbulence_type: TurbulenceType
    is_2d: bool
    fluid_properties: Dict[str, float]
    geometry_params: Dict[str, float]
    boundary_definitions: List[BoundaryDefinition]
    mesh_params: BlockMeshParams
    control_params: Dict[str, Any]
    reynolds_number: float
    # Optional snappyHexMesh support
    use_snappy: bool = False
    snappy_params: Optional[SnappyHexMeshParams] = None
    # Mesh quality info (from checkMesh)
    max_non_orthogonality: float = 0.0
    # Whether this is a closed system (needs pressure reference)
    is_closed_system: bool = False


class OpenFOAMGenerator:
    """Generates OpenFOAM case files from configuration."""

    OPENFOAM_HEADER = '''/*--------------------------------*- C++ -*----------------------------------*\\
| =========                 |                                                 |
| \\\\      /  F ield         | OpenFOAM: The Open Source CFD Toolbox           |
|  \\\\    /   O peration     | Version:  v2312                                 |
|   \\\\  /    A nd           | Website:  www.openfoam.com                      |
|    \\\\/     M anipulation  |                                                 |
\\*---------------------------------------------------------------------------*/
FoamFile
{{
    version     2.0;
    format      ascii;
    class       {class_type};
    object      {object_name};
}}
// * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * //
'''
    def __init__(self, config: CaseConfig):
        self.config = config

    def _calculate_turbulence_params(self) -> Dict[str, float]:
        """Calculate turbulence initial values based on flow conditions.

        Returns turbulence intensity and length scale appropriate for the flow type,
        then computes k, epsilon, and omega initial values.
        """
        inlet_vel = self.config.geometry_params.get("inlet_velocity", 1.0)
        # Use diameter, or width, or height as characteristic length
        char_length = self.config.geometry_params.get(
            "diameter",
            self.config.geometry_params.get(
                "width",
                self.config.geometry_params.get("height", 0.1)
            )
        )
        try:
            char_length = float(char_length)
        except (TypeError, ValueError):
            char_length = 0.1
        char_length = max(char_length, 1e-6)

        Re = self.config.reynolds_number

        # Turbulent intensity based on flow type and Re
        # Reference: OpenFOAM User Guide, CFD best practices
        if Re < 2000:  # Laminar
            turbulent_intensity = 0.01
        elif Re < 4000:  # Transitional
            turbulent_intensity = 0.03
        elif Re < 10000:  # Low turbulence
            turbulent_intensity = 0.04
        elif Re < 100000:  # Moderate turbulence
            turbulent_intensity = 0.05
        else:  # High Re - higher turbulence
            turbulent_intensity = 0.06

        # Turbulent length scale based on flow type
        # Internal flow: L_t ~ 0.07 * D (hydraulic diameter)
        # External flow: L_t ~ 0.4 * delta (boundary layer thickness)
        # General: L_t ~ 0.038 * D for fully developed pipe flow
        if "pipe" in str(self.config.geometry_params.get("template_id", "")):
            turbulent_length_scale = 0.07 * char_length  # Internal flow
        elif "cylinder" in str(self.config.geometry_params.get("template_id", "")):
            turbulent_length_scale = 0.1 * char_length  # External flow
        else:
            turbulent_length_scale = 0.07 * char_length  # Default

        # Calculate turbulent kinetic energy: k = 1.5 * (U * I)^2
        k = 1.5 * (inlet_vel * turbulent_intensity) ** 2

        # Ensure k is not too small (numerical stability)
        k = max(k, 1e-6)

        # Calculate epsilon: epsilon = C_mu^0.75 * k^1.5 / L_t
        # where C_mu = 0.09
        C_mu = 0.09
        epsilon = (C_mu ** 0.75) * (k ** 1.5) / turbulent_length_scale
        epsilon = max(epsilon, 1e-6)

        # Calculate omega: omega = k^0.5 / (C_mu^0.25 * L_t)
        # or omega = epsilon / (C_mu * k)
        omega = epsilon / (C_mu * k) if k > 0 else 1.0
        omega = max(omega, 1e-6)

        return {
            "turbulent_intensity": turbulent_intensity,
            "turbulent_length_scale": turbulent_length_scale,
            "k": k,
            "epsilon": epsilon,
            "omega": omega,
        }

    def generate_all(self, include_parallel: bool = False, n_processors: int = 4,
                       decomposition_method: str = "scotch") -> Dict[str, str]:
        """Generate all case files and return as dictionary.

        Args:
            include_parallel: Whether to include decomposeParDict for parallel runs
            n_processors: Number of processors for parallel decomposition
            decomposition_method: Decomposition method (simple, hierarchical, scotch)

        Returns:
            Dictionary mapping relative file paths to file contents
        """
        files = {}

        # Generate directory structure content
        files["0/U"] = self._generate_U()

        # Multiphase flow (interFoam) needs different field setup
        if self.config.flow_type == FlowType.MULTIPHASE:
            files["0/alpha.water"] = self._generate_alpha_water()
            files["0/alpha.water.orig"] = self._generate_alpha_water()
            files["0/p_rgh"] = self._generate_p_rgh_multiphase()
            files["constant/g"] = self._generate_g()
            files["constant/transportProperties"] = self._generate_transport_properties_multiphase()
            files["system/setFieldsDict"] = self._generate_set_fields_dict()
        elif self.config.flow_type == FlowType.COMPRESSIBLE:
            files["0/p"] = self._generate_p_compressible()
            files["0/T"] = self._generate_T_compressible()
            files["constant/thermophysicalProperties"] = self._generate_thermophysical_properties_compressible()
            # Add setFieldsDict for shock tube initialization
            template_id = self.config.geometry_params.get("template_id")
            if template_id == "shock_tube":
                files["system/setFieldsDict"] = self._generate_set_fields_dict()
        elif self.config.flow_type == FlowType.HEAT_TRANSFER:
            files["0/p"] = self._generate_p()
            files["0/T"] = self._generate_T()
            files["0/p_rgh"] = self._generate_p_rgh()
            files["constant/thermophysicalProperties"] = self._generate_thermophysical_properties()
            files["constant/g"] = self._generate_g()
            files["constant/transportProperties"] = self._generate_transport_properties()
            files["constant/physicalProperties"] = self._generate_physical_properties()
        else:
            files["0/p"] = self._generate_p()
            files["constant/transportProperties"] = self._generate_transport_properties()
            files["constant/physicalProperties"] = self._generate_physical_properties()

        if self.config.turbulence_type != TurbulenceType.LAMINAR:
            files["0/k"] = self._generate_k()
            files["0/epsilon"] = self._generate_epsilon()
            files["0/omega"] = self._generate_omega()
            files["0/nut"] = self._generate_nut()

        files["constant/turbulenceProperties"] = self._generate_turbulence_properties()

        files["system/controlDict"] = self._generate_control_dict()
        files["system/fvSchemes"] = self._generate_fv_schemes()
        files["system/fvSolution"] = self._generate_fv_solution()

        # Generate mesh dictionary based on configuration
        if self.config.use_snappy and self.config.snappy_params:
            files["system/blockMeshDict"] = self._generate_background_mesh_dict()
            files["system/snappyHexMeshDict"] = self._generate_snappy_hex_mesh_dict()
        else:
            files["system/blockMeshDict"] = self._generate_block_mesh_dict()

        # Generate parallel decomposition dictionary if requested
        if include_parallel:
            files["system/decomposeParDict"] = self._generate_decompose_par_dict(
                n_processors, decomposition_method
            )

        files["Allrun"] = self._generate_allrun()
        files["Allclean"] = self._generate_allclean()

        return files

    def write_case(self) -> List[str]:
        """Write all case files to disk. Returns list of created files."""
        files = self.generate_all()
        created_files = []
        case_root = self.config.case_path.resolve()

        for rel_path, content in files.items():
            rel = Path(rel_path)
            if rel.is_absolute() or ".." in rel.parts:
                raise ValueError(f"生成文件路径非法（越界）: {rel_path}")

            full_path = (case_root / rel).resolve()
            try:
                full_path.relative_to(case_root)
            except ValueError as exc:
                raise ValueError(f"生成文件路径越界: {rel_path}") from exc

            full_path.parent.mkdir(parents=True, exist_ok=True)

            atomic_write_text(full_path, content)

            # Make scripts executable
            if rel_path.startswith("All"):
                os.chmod(full_path, 0o755)

            created_files.append(str(full_path))

        return created_files

    def _header(self, class_type: str, object_name: str) -> str:
        """Generate OpenFOAM file header."""
        return self.OPENFOAM_HEADER.format(
            class_type=class_type,
            object_name=object_name
        )

    def _generate_U(self) -> str:
        """Generate velocity field file."""
        content = self._header("volVectorField", "U")

        # Get inlet velocity
        inlet_vel = _coerce_float_scalar(
            self.config.geometry_params.get("inlet_velocity", 1.0),
            "inlet_velocity",
        )
        lid_vel = _coerce_float_scalar(
            self.config.geometry_params.get("lid_velocity", 1.0),
            "lid_velocity",
        )

        content += f'''
dimensions      [0 1 -1 0 0 0 0];

internalField   uniform (0 0 0);

boundaryField
{{
'''
        # Add boundary conditions based on geometry
        for boundary in self.config.boundary_definitions:
            content += f"    {boundary.name}\n    {{\n"

            if boundary.physical_type == BoundaryType.INLET:
                content += f"        type            fixedValue;\n"
                content += f"        value           uniform ({inlet_vel} 0 0);\n"
            elif boundary.physical_type == BoundaryType.OUTLET:
                # For multiphase flow (interFoam), use pressureInletOutletVelocity
                if self.config.flow_type == FlowType.MULTIPHASE:
                    content += f"        type            pressureInletOutletVelocity;\n"
                    content += f"        value           uniform (0 0 0);\n"
                else:
                    content += f"        type            zeroGradient;\n"
            elif boundary.physical_type == BoundaryType.WALL:
                if boundary.subtype == "moving":
                    content += f"        type            fixedValue;\n"
                    content += f"        value           uniform ({lid_vel} 0 0);\n"
                else:
                    content += f"        type            noSlip;\n"
            elif boundary.physical_type == BoundaryType.SYMMETRY:
                content += f"        type            symmetry;\n"
            elif boundary.physical_type == BoundaryType.EMPTY:
                content += f"        type            empty;\n"

            content += "    }\n"

        content += '''}

// ************************************************************************* //
'''
        return content

    def _generate_p(self) -> str:
        """Generate pressure field file."""
        content = self._header("volScalarField", "p")

        # Use kinematic pressure for incompressible
        if self.config.flow_type == FlowType.INCOMPRESSIBLE:
            dimensions = "[0 2 -2 0 0 0 0]"
        else:
            dimensions = "[1 -1 -2 0 0 0 0]"

        content += f'''
dimensions      {dimensions};

internalField   uniform 0;

boundaryField
{{
'''
        for boundary in self.config.boundary_definitions:
            content += f"    {boundary.name}\n    {{\n"

            if boundary.physical_type == BoundaryType.INLET:
                content += f"        type            zeroGradient;\n"
            elif boundary.physical_type == BoundaryType.OUTLET:
                content += f"        type            fixedValue;\n"
                content += f"        value           uniform 0;\n"
            elif boundary.physical_type == BoundaryType.WALL:
                content += f"        type            zeroGradient;\n"
            elif boundary.physical_type == BoundaryType.SYMMETRY:
                content += f"        type            symmetry;\n"
            elif boundary.physical_type == BoundaryType.EMPTY:
                content += f"        type            empty;\n"

            content += "    }\n"

        content += '''}

// ************************************************************************* //
'''
        return content

    def _generate_k(self) -> str:
        """Generate turbulent kinetic energy field."""
        content = self._header("volScalarField", "k")

        # Use improved turbulence parameter calculation
        turb_params = self._calculate_turbulence_params()
        k_init = turb_params["k"]

        content += f'''
dimensions      [0 2 -2 0 0 0 0];

internalField   uniform {k_init:.6f};

boundaryField
{{
'''
        for boundary in self.config.boundary_definitions:
            content += f"    {boundary.name}\n    {{\n"

            if boundary.physical_type == BoundaryType.INLET:
                content += f"        type            fixedValue;\n"
                content += f"        value           uniform {k_init:.6f};\n"
            elif boundary.physical_type == BoundaryType.OUTLET:
                content += f"        type            zeroGradient;\n"
            elif boundary.physical_type == BoundaryType.WALL:
                content += f"        type            kqRWallFunction;\n"
                content += f"        value           uniform {k_init:.6f};\n"
            elif boundary.physical_type == BoundaryType.SYMMETRY:
                content += f"        type            symmetry;\n"
            elif boundary.physical_type == BoundaryType.EMPTY:
                content += f"        type            empty;\n"

            content += "    }\n"

        content += '''}

// ************************************************************************* //
'''
        return content

    def _generate_epsilon(self) -> str:
        """Generate turbulent dissipation rate field."""
        content = self._header("volScalarField", "epsilon")

        # Use improved turbulence parameter calculation
        turb_params = self._calculate_turbulence_params()
        epsilon_init = turb_params["epsilon"]

        content += f'''
dimensions      [0 2 -3 0 0 0 0];

internalField   uniform {epsilon_init:.6f};

boundaryField
{{
'''
        for boundary in self.config.boundary_definitions:
            content += f"    {boundary.name}\n    {{\n"

            if boundary.physical_type == BoundaryType.INLET:
                content += f"        type            fixedValue;\n"
                content += f"        value           uniform {epsilon_init:.6f};\n"
            elif boundary.physical_type == BoundaryType.OUTLET:
                content += f"        type            zeroGradient;\n"
            elif boundary.physical_type == BoundaryType.WALL:
                content += f"        type            epsilonWallFunction;\n"
                content += f"        value           uniform {epsilon_init:.6f};\n"
            elif boundary.physical_type == BoundaryType.SYMMETRY:
                content += f"        type            symmetry;\n"
            elif boundary.physical_type == BoundaryType.EMPTY:
                content += f"        type            empty;\n"

            content += "    }\n"

        content += '''}

// ************************************************************************* //
'''
        return content

    def _generate_omega(self) -> str:
        """Generate specific dissipation rate field for k-omega SST model."""
        content = self._header("volScalarField", "omega")

        # Use improved turbulence parameter calculation
        turb_params = self._calculate_turbulence_params()
        omega_init = turb_params["omega"]

        content += f'''
dimensions      [0 0 -1 0 0 0 0];

internalField   uniform {omega_init:.6f};

boundaryField
{{
'''
        for boundary in self.config.boundary_definitions:
            content += f"    {boundary.name}\n    {{\n"

            if boundary.physical_type == BoundaryType.INLET:
                content += f"        type            fixedValue;\n"
                content += f"        value           uniform {omega_init:.6f};\n"
            elif boundary.physical_type == BoundaryType.OUTLET:
                content += f"        type            zeroGradient;\n"
            elif boundary.physical_type == BoundaryType.WALL:
                content += f"        type            omegaWallFunction;\n"
                content += f"        value           uniform {omega_init:.6f};\n"
            elif boundary.physical_type == BoundaryType.SYMMETRY:
                content += f"        type            symmetry;\n"
            elif boundary.physical_type == BoundaryType.EMPTY:
                content += f"        type            empty;\n"

            content += "    }\n"

        content += '''}

// ************************************************************************* //
'''
        return content

    def _generate_nut(self) -> str:
        """Generate turbulent viscosity field."""
        content = self._header("volScalarField", "nut")

        content += f'''
dimensions      [0 2 -1 0 0 0 0];

internalField   uniform 0;

boundaryField
{{
'''
        for boundary in self.config.boundary_definitions:
            content += f"    {boundary.name}\n    {{\n"

            if boundary.physical_type == BoundaryType.WALL:
                content += f"        type            nutkWallFunction;\n"
                content += f"        value           uniform 0;\n"
            elif boundary.physical_type == BoundaryType.EMPTY:
                content += f"        type            empty;\n"
            elif boundary.physical_type == BoundaryType.SYMMETRY:
                content += f"        type            symmetry;\n"
            else:
                content += f"        type            calculated;\n"
                content += f"        value           uniform 0;\n"

            content += "    }\n"

        content += '''}

// ************************************************************************* //
'''
        return content

    def _generate_T(self) -> str:
        """Generate temperature field for heat transfer cases."""
        content = self._header("volScalarField", "T")

        hot_temp = _coerce_float_scalar(
            self.config.geometry_params.get("hot_wall_temp", 310),
            "hot_wall_temp",
        )
        cold_temp = _coerce_float_scalar(
            self.config.geometry_params.get("cold_wall_temp", 290),
            "cold_wall_temp",
        )
        avg_temp = (hot_temp + cold_temp) / 2

        content += f'''
dimensions      [0 0 0 1 0 0 0];

internalField   uniform {avg_temp};

boundaryField
{{
'''
        for boundary in self.config.boundary_definitions:
            content += f"    {boundary.name}\n    {{\n"

            if "hot" in boundary.name.lower():
                content += f"        type            fixedValue;\n"
                content += f"        value           uniform {hot_temp};\n"
            elif "cold" in boundary.name.lower():
                content += f"        type            fixedValue;\n"
                content += f"        value           uniform {cold_temp};\n"
            elif boundary.physical_type == BoundaryType.WALL:
                content += f"        type            zeroGradient;\n"
            elif boundary.physical_type == BoundaryType.EMPTY:
                content += f"        type            empty;\n"
            else:
                content += f"        type            zeroGradient;\n"

            content += "    }\n"

        content += '''}

// ************************************************************************* //
'''
        return content

    def _generate_p_compressible(self) -> str:
        """Generate pressure field for compressible flows."""
        content = self._header("volScalarField", "p")

        # Get initial pressure from template parameters
        p_init = _coerce_float_scalar(
            self.config.geometry_params.get("initial_pressure", 101325.0),
            "initial_pressure",
        )

        content += f'''
dimensions      [1 -1 -2 0 0 0 0];

internalField   uniform {p_init};

boundaryField
{{
'''
        for boundary in self.config.boundary_definitions:
            content += f"    {boundary.name}\n    {{\n"

            if boundary.physical_type == BoundaryType.INLET:
                # Total pressure at inlet
                p_inlet = _coerce_float_scalar(
                    self.config.geometry_params.get("inlet_pressure", p_init),
                    "inlet_pressure",
                )
                content += f"        type            totalPressure;\n"
                content += f"        p0              uniform {p_inlet};\n"
                content += f"        value           uniform {p_inlet};\n"
            elif boundary.physical_type == BoundaryType.OUTLET:
                p_outlet = _coerce_float_scalar(
                    self.config.geometry_params.get("outlet_pressure", p_init),
                    "outlet_pressure",
                )
                content += f"        type            fixedValue;\n"
                content += f"        value           uniform {p_outlet};\n"
            elif boundary.physical_type == BoundaryType.WALL:
                content += f"        type            zeroGradient;\n"
            elif boundary.physical_type == BoundaryType.SYMMETRY:
                content += f"        type            symmetry;\n"
            elif boundary.physical_type == BoundaryType.EMPTY:
                content += f"        type            empty;\n"
            else:
                content += f"        type            zeroGradient;\n"

            content += "    }\n"

        content += '''}

// ************************************************************************* //
'''
        return content

    def _generate_T_compressible(self) -> str:
        """Generate temperature field for compressible flows."""
        content = self._header("volScalarField", "T")

        # Get initial temperature from template parameters
        T_init = _coerce_float_scalar(
            self.config.geometry_params.get("initial_temperature", 300.0),
            "initial_temperature",
        )

        content += f'''
dimensions      [0 0 0 1 0 0 0];

internalField   uniform {T_init};

boundaryField
{{
'''
        for boundary in self.config.boundary_definitions:
            content += f"    {boundary.name}\n    {{\n"

            if boundary.physical_type == BoundaryType.INLET:
                # Total temperature at inlet
                T_inlet = _coerce_float_scalar(
                    self.config.geometry_params.get("inlet_temperature", T_init),
                    "inlet_temperature",
                )
                content += f"        type            totalTemperature;\n"
                content += f"        T0              uniform {T_inlet};\n"
                content += f"        value           uniform {T_inlet};\n"
            elif boundary.physical_type == BoundaryType.OUTLET:
                content += f"        type            zeroGradient;\n"
            elif boundary.physical_type == BoundaryType.WALL:
                # Adiabatic wall by default
                wall_temp = self.config.geometry_params.get("wall_temperature", None)
                if wall_temp is not None:
                    wall_temp_value = _coerce_float_scalar(wall_temp, "wall_temperature")
                    content += f"        type            fixedValue;\n"
                    content += f"        value           uniform {wall_temp_value};\n"
                else:
                    content += f"        type            zeroGradient;\n"
            elif boundary.physical_type == BoundaryType.SYMMETRY:
                content += f"        type            symmetry;\n"
            elif boundary.physical_type == BoundaryType.EMPTY:
                content += f"        type            empty;\n"
            else:
                content += f"        type            zeroGradient;\n"

            content += "    }\n"

        content += '''}

// ************************************************************************* //
'''
        return content

    def _generate_p_rgh(self) -> str:
        """Generate pressure minus hydrostatic pressure field for buoyancy-driven flows."""
        content = self._header("volScalarField", "p_rgh")

        content += f'''
dimensions      [0 2 -2 0 0 0 0];

internalField   uniform 0;

boundaryField
{{
'''
        for boundary in self.config.boundary_definitions:
            content += f"    {boundary.name}\n    {{\n"

            if boundary.physical_type == BoundaryType.INLET:
                content += f"        type            fixedFluxPressure;\n"
                content += f"        value           uniform 0;\n"
            elif boundary.physical_type == BoundaryType.OUTLET:
                content += f"        type            fixedValue;\n"
                content += f"        value           uniform 0;\n"
            elif boundary.physical_type == BoundaryType.WALL:
                content += f"        type            fixedFluxPressure;\n"
                content += f"        value           uniform 0;\n"
            elif boundary.physical_type == BoundaryType.SYMMETRY:
                content += f"        type            symmetry;\n"
            elif boundary.physical_type == BoundaryType.EMPTY:
                content += f"        type            empty;\n"

            content += "    }\n"

        content += '''}

// ************************************************************************* //
'''
        return content

    def _generate_alpha_water(self) -> str:
        """Generate alpha.water field for VOF multiphase simulations."""
        content = self._header("volScalarField", "alpha.water")

        content += '''
dimensions      [0 0 0 0 0 0 0];

internalField   uniform 0;

boundaryField
{
'''
        for boundary in self.config.boundary_definitions:
            content += f"    {boundary.name}\n    {{\n"

            if boundary.physical_type == BoundaryType.INLET:
                content += f"        type            fixedValue;\n"
                content += f"        value           uniform 1;\n"
            elif boundary.physical_type == BoundaryType.OUTLET:
                # Atmosphere boundary for VOF
                content += f"        type            inletOutlet;\n"
                content += f"        inletValue      uniform 0;\n"
                content += f"        value           uniform 0;\n"
            elif boundary.physical_type == BoundaryType.WALL:
                content += f"        type            zeroGradient;\n"
            elif boundary.physical_type == BoundaryType.SYMMETRY:
                content += f"        type            symmetry;\n"
            elif boundary.physical_type == BoundaryType.EMPTY:
                content += f"        type            empty;\n"
            else:
                content += f"        type            zeroGradient;\n"

            content += "    }\n"

        content += '''}

// ************************************************************************* //
'''
        return content

    def _generate_p_rgh_multiphase(self) -> str:
        """Generate p_rgh field for VOF multiphase simulations."""
        content = self._header("volScalarField", "p_rgh")

        content += '''
dimensions      [1 -1 -2 0 0 0 0];

internalField   uniform 0;

boundaryField
{
'''
        for boundary in self.config.boundary_definitions:
            content += f"    {boundary.name}\n    {{\n"

            if boundary.physical_type == BoundaryType.INLET:
                content += f"        type            fixedFluxPressure;\n"
                content += f"        value           uniform 0;\n"
            elif boundary.physical_type == BoundaryType.OUTLET:
                # Atmosphere boundary - totalPressure for open boundary
                content += f"        type            totalPressure;\n"
                content += f"        p0              uniform 0;\n"
            elif boundary.physical_type == BoundaryType.WALL:
                content += f"        type            fixedFluxPressure;\n"
                content += f"        value           uniform 0;\n"
            elif boundary.physical_type == BoundaryType.SYMMETRY:
                content += f"        type            symmetry;\n"
            elif boundary.physical_type == BoundaryType.EMPTY:
                content += f"        type            empty;\n"
            else:
                content += f"        type            fixedFluxPressure;\n"
                content += f"        value           uniform 0;\n"

            content += "    }\n"

        content += '''}

// ************************************************************************* //
'''
        return content

    def _generate_transport_properties_multiphase(self) -> str:
        """Generate transportProperties for VOF multiphase simulations."""
        content = self._header("dictionary", "transportProperties")

        # Get surface tension (default water-air at 20°C)
        sigma = _coerce_float_scalar(
            self.config.geometry_params.get("surface_tension", 0.07),
            "surface_tension",
        )

        content += f'''
phases (water air);

water
{{
    transportModel  Newtonian;
    nu              [0 2 -1 0 0 0 0] 1e-06;
    rho             [1 -3 0 0 0 0 0] 1000;
}}

air
{{
    transportModel  Newtonian;
    nu              [0 2 -1 0 0 0 0] 1.48e-05;
    rho             [1 -3 0 0 0 0 0] 1;
}}

sigma           [1 0 -2 0 0 0 0] {sigma};

// ************************************************************************* //
'''
        return content

    def _generate_set_fields_dict(self) -> str:
        """Generate setFieldsDict for initializing VOF fields."""
        content = self._header("dictionary", "setFieldsDict")

        template_id = self.config.geometry_params.get("template_id", "dam_break")

        if template_id == "dam_break":
            # Water column dimensions
            water_width = _coerce_float_scalar(
                self.config.geometry_params.get("water_column_width", 0.146),
                "water_column_width",
            )
            water_height = _coerce_float_scalar(
                self.config.geometry_params.get("water_column_height", 0.292),
                "water_column_height",
            )

            content += f'''
defaultFieldValues
(
    volScalarFieldValue alpha.water 0
);

regions
(
    boxToCell
    {{
        box (0 0 -1) ({water_width} {water_height} 1);
        fieldValues
        (
            volScalarFieldValue alpha.water 1
        );
    }}
);

// ************************************************************************* //
'''
        elif template_id == "bubble_rising":
            # Bubble dimensions
            bubble_d = _coerce_float_scalar(
                self.config.geometry_params.get("bubble_diameter", 0.01),
                "bubble_diameter",
            )
            column_width = _coerce_float_scalar(
                self.config.geometry_params.get("column_width", 0.06),
                "column_width",
            )
            # Bubble center at bottom center of domain
            cx = column_width / 2.0
            cy = bubble_d * 1.5  # 1.5 diameters from bottom
            r = bubble_d / 2.0

            content += f'''
defaultFieldValues
(
    volScalarFieldValue alpha.water 1
);

regions
(
    sphereToCell
    {{
        centre ({cx} {cy} 0);
        radius {r};
        fieldValues
        (
            volScalarFieldValue alpha.water 0
        );
    }}
);

// ************************************************************************* //
'''
        elif template_id == "shock_tube":
            # Shock tube - initialize left and right states
            diaphragm_pos = _coerce_float_scalar(
                self.config.geometry_params.get("diaphragm_position", 5.0),
                "diaphragm_position",
            )
            left_p = _coerce_float_scalar(
                self.config.geometry_params.get("left_pressure", 100000.0),
                "left_pressure",
            )
            right_p = _coerce_float_scalar(
                self.config.geometry_params.get("right_pressure", 10000.0),
                "right_pressure",
            )
            left_T = _coerce_float_scalar(
                self.config.geometry_params.get("left_temperature", 348.432),
                "left_temperature",
            )
            right_T = _coerce_float_scalar(
                self.config.geometry_params.get("right_temperature", 278.746),
                "right_temperature",
            )

            content += f'''
defaultFieldValues
(
    volScalarFieldValue p {right_p}
    volScalarFieldValue T {right_T}
);

regions
(
    boxToCell
    {{
        box (-1 -1 -1) ({diaphragm_pos} 1 1);
        fieldValues
        (
            volScalarFieldValue p {left_p}
            volScalarFieldValue T {left_T}
        );
    }}
);

// ************************************************************************* //
'''
        else:
            content += '''
defaultFieldValues
(
    volScalarFieldValue alpha.water 0
);

regions
();

// ************************************************************************* //
'''
        return content

    def _generate_transport_properties(self) -> str:
        """Generate transportProperties file."""
        content = self._header("dictionary", "transportProperties")

        nu = self.config.fluid_properties.get("nu", 1e-6)

        content += f'''
transportModel  Newtonian;

nu              [0 2 -1 0 0 0 0] {nu};

// ************************************************************************* //
'''
        return content

    def _generate_physical_properties(self) -> str:
        """Generate physicalProperties for newer OpenFOAM distributions."""
        content = self._header("dictionary", "physicalProperties")

        nu = self.config.fluid_properties.get("nu", 1e-6)

        content += f'''
viscosityModel  Newtonian;

nu              [0 2 -1 0 0 0 0] {nu};

// ************************************************************************* //
'''
        return content

    def _generate_thermophysical_properties(self) -> str:
        """Generate thermophysicalProperties file for buoyancy-driven flows."""
        content = self._header("dictionary", "thermophysicalProperties")

        content += '''
thermoType
{
    type            heRhoThermo;
    mixture         pureMixture;
    transport       const;
    thermo          hConst;
    equationOfState perfectGas;
    specie          specie;
    energy          sensibleEnthalpy;
}

mixture
{
    specie
    {
        molWeight       28.9;
    }
    thermodynamics
    {
        Cp              1005;
        Hf              0;
    }
    transport
    {
        mu              1.8e-05;
        Pr              0.7;
    }
}

// ************************************************************************* //
'''
        return content

    def _generate_thermophysical_properties_compressible(self) -> str:
        """Generate thermophysicalProperties file for compressible flows (rhoCentralFoam)."""
        content = self._header("dictionary", "thermophysicalProperties")

        # Get gas properties from template
        gas = self.config.geometry_params.get("gas", "air")

        if gas == "air":
            mol_weight = 28.96
            Cp = 1005
            mu = 1.8e-05
            Pr = 0.7
        else:
            # Default to air
            mol_weight = 28.96
            Cp = 1005
            mu = 1.8e-05
            Pr = 0.7

        content += f'''
thermoType
{{
    type            hePsiThermo;
    mixture         pureMixture;
    transport       const;
    thermo          hConst;
    equationOfState perfectGas;
    specie          specie;
    energy          sensibleInternalEnergy;
}}

mixture
{{
    specie
    {{
        molWeight       {mol_weight};
    }}
    thermodynamics
    {{
        Cp              {Cp};
        Hf              0;
    }}
    transport
    {{
        mu              {mu};
        Pr              {Pr};
    }}
}}

// ************************************************************************* //
'''
        return content

    def _generate_g(self) -> str:
        """Generate gravitational acceleration file for buoyancy-driven flows."""
        content = self._header("uniformDimensionedVectorField", "g")

        content += '''
dimensions      [0 1 -2 0 0 0 0];
value           (0 -9.81 0);

// ************************************************************************* //
'''
        return content

    def _generate_turbulence_properties(self) -> str:
        """Generate turbulenceProperties file."""
        content = self._header("dictionary", "turbulenceProperties")

        if self.config.turbulence_type == TurbulenceType.LAMINAR:
            content += '''
simulationType  laminar;

// ************************************************************************* //
'''
        else:
            content += '''
simulationType  RAS;

RAS
{
    RASModel        kEpsilon;
    turbulence      on;
    printCoeffs     on;
}

// ************************************************************************* //
'''
        return content

    def _generate_control_dict(self) -> str:
        """Generate controlDict file with monitoring functions."""
        content = self._header("dictionary", "controlDict")

        solver = self.config.solver
        end_time = self.config.control_params.get("end_time", 1000)
        write_interval = self.config.control_params.get("write_interval", 100)
        delta_t = self.config.control_params.get("delta_t", 1)

        if self.config.time_type == TimeType.TRANSIENT:
            delta_t = self.config.control_params.get("delta_t", 0.001)
            write_interval = self.config.control_params.get("write_interval", 0.1)

        content += f'''
application     {solver};

startFrom       startTime;

startTime       0;

stopAt          endTime;

endTime         {end_time};

deltaT          {delta_t};

writeControl    {"adjustableRunTime" if self.config.time_type == TimeType.TRANSIENT else "timeStep"};

writeInterval   {write_interval};

purgeWrite      0;

writeFormat     ascii;

writePrecision  6;

writeCompression off;

timeFormat      general;

timePrecision   6;

runTimeModifiable true;
'''
        # Add adaptive time stepping for multiphase simulations
        if self.config.flow_type == FlowType.MULTIPHASE:
            content += '''
adjustTimeStep  yes;

maxCo           1;

maxAlphaCo      1;

maxDeltaT       1;
'''

        content += '''
functions
{
'''
        # Add residuals monitoring - different fields for multiphase
        if self.config.flow_type == FlowType.MULTIPHASE:
            content += '''    residuals
    {
        type            residuals;
        libs            ("libutilityFunctionObjects.so");
        writeControl    timeStep;
        writeInterval   1;
        fields          (p_rgh U);
    }
'''
        else:
            fields = "(p U k epsilon omega)"
            if self.config.turbulence_type == TurbulenceType.LAMINAR:
                fields = "(p U)"
            content += '''    residuals
    {
        type            residuals;
        libs            ("libutilityFunctionObjects.so");
        writeControl    timeStep;
        writeInterval   1;
'''
            content += f"        fields          {fields};\n"
            content += '''    }
'''

        # Get boundary names for monitoring
        wall_patches = []
        inlet_patches = []
        outlet_patches = []

        for bd in self.config.boundary_definitions:
            if bd.physical_type == BoundaryType.WALL:
                wall_patches.append(bd.name)
            elif bd.physical_type == BoundaryType.INLET:
                inlet_patches.append(bd.name)
            elif bd.physical_type == BoundaryType.OUTLET:
                outlet_patches.append(bd.name)

        # Add forces monitoring if there are wall patches
        if wall_patches:
            wall_patches_str = " ".join(wall_patches)
            rho = self.config.fluid_properties.get("rho", 1000)
            content += f'''
    forces
    {{
        type            forces;
        libs            ("libforces.so");
        writeControl    timeStep;
        writeInterval   10;
        patches         ({wall_patches_str});
        rho             rhoInf;
        rhoInf          {rho};
        CofR            (0 0 0);
        log             true;
    }}
'''

        # Add flow rate monitoring if there are inlet/outlet patches
        if inlet_patches:
            inlet_str = inlet_patches[0]  # Use first inlet
            content += f'''
    inletFlowRate
    {{
        type            surfaceFieldValue;
        libs            ("libfieldFunctionObjects.so");
        writeControl    timeStep;
        writeInterval   10;
        log             true;
        writeFields     false;
        regionType      patch;
        name            {inlet_str};
        operation       sum;
        fields          (phi);
    }}
'''

        if outlet_patches:
            outlet_str = outlet_patches[0]  # Use first outlet
            content += f'''
    outletFlowRate
    {{
        type            surfaceFieldValue;
        libs            ("libfieldFunctionObjects.so");
        writeControl    timeStep;
        writeInterval   10;
        log             true;
        writeFields     false;
        regionType      patch;
        name            {outlet_str};
        operation       sum;
        fields          (phi);
    }}
'''

        # Add y+ monitoring for wall-bounded flows
        if wall_patches and self.config.turbulence_type != TurbulenceType.LAMINAR:
            wall_patches_str = " ".join(wall_patches)
            content += f'''
    yPlus
    {{
        type            yPlus;
        libs            ("libfieldFunctionObjects.so");
        writeControl    writeTime;
        patches         ({wall_patches_str});
        log             true;
    }}
'''

        # Add field averaging for transient simulations
        if self.config.time_type == TimeType.TRANSIENT:
            content += '''
    fieldAverage1
    {
        type            fieldAverage;
        libs            ("libfieldFunctionObjects.so");
        writeControl    writeTime;

        fields
        (
            U
            {
                mean        on;
                prime2Mean  on;
                base        time;
            }
            p
            {
                mean        on;
                prime2Mean  on;
                base        time;
            }
        );
    }
'''

        content += '''}

// ************************************************************************* //
'''
        return content

    def _generate_fv_schemes(self) -> str:
        """Generate fvSchemes file with schemes based on Reynolds number."""
        content = self._header("dictionary", "fvSchemes")

        # VOF multiphase needs completely different schemes
        if self.config.flow_type == FlowType.MULTIPHASE:
            return self._generate_fv_schemes_multiphase(content)

        # Compressible flow (rhoCentralFoam) needs different schemes
        if self.config.flow_type == FlowType.COMPRESSIBLE:
            return self._generate_fv_schemes_compressible(content)

        Re = self.config.reynolds_number
        max_non_orth = self.config.max_non_orthogonality

        # Time scheme
        if self.config.time_type == TimeType.TRANSIENT:
            ddt_scheme = "backward"  # Second-order implicit
        else:
            ddt_scheme = "steadyState"

        # Determine convection schemes based on Re and mesh quality
        # For high Re or poor mesh quality, use more robust (but less accurate) schemes
        if Re < 2000:
            # Laminar - can use higher order schemes
            div_U_scheme = "bounded Gauss linearUpwind grad(U)"
            div_turb_scheme = "bounded Gauss linearUpwind grad(k)"
        elif Re < 10000 or max_non_orth > 70:
            # Transitional or poor mesh - use blended scheme
            div_U_scheme = "bounded Gauss linearUpwind grad(U)"
            div_turb_scheme = "bounded Gauss upwind"
        else:
            # Fully turbulent with good mesh - standard schemes
            div_U_scheme = "bounded Gauss linearUpwind grad(U)"
            div_turb_scheme = "bounded Gauss upwind"

        # Laplacian scheme based on mesh quality
        if max_non_orth < 70:
            laplacian_scheme = "Gauss linear corrected"
            sn_grad_scheme = "corrected"
        elif max_non_orth < 80:
            laplacian_scheme = "Gauss linear limited corrected 0.5"
            sn_grad_scheme = "limited corrected 0.5"
        else:
            laplacian_scheme = "Gauss linear limited corrected 0.33"
            sn_grad_scheme = "limited corrected 0.33"

        content += f'''
ddtSchemes
{{
    default         {ddt_scheme};
}}

gradSchemes
{{
    default         Gauss linear;
    grad(U)         cellLimited Gauss linear 1;
    grad(k)         cellLimited Gauss linear 1;
    grad(epsilon)   cellLimited Gauss linear 1;
    grad(omega)     cellLimited Gauss linear 1;
}}

divSchemes
{{
    default         none;
    div(phi,U)      {div_U_scheme};
    div(phi,k)      {div_turb_scheme};
    div(phi,epsilon) {div_turb_scheme};
    div(phi,omega)  {div_turb_scheme};
    div(phi,nuTilda) {div_turb_scheme};
    div((nuEff*dev2(T(grad(U))))) Gauss linear;
}}

laplacianSchemes
{{
    default         {laplacian_scheme};
}}

interpolationSchemes
{{
    default         linear;
}}

snGradSchemes
{{
    default         {sn_grad_scheme};
}}

wallDist
{{
    method          meshWave;
}}

// ************************************************************************* //
'''

        return content

    def _generate_fv_schemes_multiphase(self, content: str) -> str:
        """Generate fvSchemes for VOF multiphase (interFoam)."""
        content += '''
ddtSchemes
{
    default         Euler;
}

gradSchemes
{
    default         Gauss linear;
}

divSchemes
{
    div(rhoPhi,U)   Gauss linearUpwind grad(U);
    div(phi,alpha)  Gauss vanLeer;
    div(phirb,alpha) Gauss linear;
    div(((rho*nuEff)*dev2(T(grad(U))))) Gauss linear;
}

laplacianSchemes
{
    default         Gauss linear corrected;
}

interpolationSchemes
{
    default         linear;
}

snGradSchemes
{
    default         corrected;
}

// ************************************************************************* //
'''
        return content

    def _generate_fv_schemes_compressible(self, content: str) -> str:
        """Generate fvSchemes for compressible flows (rhoCentralFoam)."""
        content += '''
ddtSchemes
{
    default         Euler;
}

gradSchemes
{
    default         Gauss linear;
}

divSchemes
{
    default         none;
    div(tauMC)      Gauss linear;
}

laplacianSchemes
{
    default         Gauss linear corrected;
}

interpolationSchemes
{
    default         linear;
    reconstruct(rho) vanLeer;
    reconstruct(U)  vanLeerV;
    reconstruct(T)  vanLeer;
}

snGradSchemes
{
    default         corrected;
}

fluxRequired
{
    default         no;
    p               ;
}

// ************************************************************************* //
'''
        return content

    def _generate_fv_solution(self) -> str:
        """Generate fvSolution file."""
        content = self._header("dictionary", "fvSolution")

        # Determine nNonOrthogonalCorrectors based on mesh quality
        max_non_orth = self.config.max_non_orthogonality
        if max_non_orth < 70:
            n_non_orth_correctors = 0
        elif max_non_orth < 75:
            n_non_orth_correctors = 1
        elif max_non_orth < 80:
            n_non_orth_correctors = 2
        else:
            n_non_orth_correctors = 3

        # Determine under-relaxation factors based on Reynolds number
        Re = self.config.reynolds_number
        if Re > 100000:  # Very high Re - more conservative
            urf_U = 0.5
            urf_p = 0.2
            urf_turb = 0.5
        elif Re > 10000:  # High Re - standard
            urf_U = 0.7
            urf_p = 0.3
            urf_turb = 0.7
        else:  # Low Re - can be more aggressive
            urf_U = 0.8
            urf_p = 0.4
            urf_turb = 0.8

        # Pressure reference for closed systems
        pref_block = ""
        if self.config.is_closed_system:
            pref_block = """
    pRefCell        0;
    pRefValue       0;"""

        if self.config.time_type == TimeType.STEADY:
            vector_solver_pattern = "(U|k|epsilon|omega)"
            residual_control_turbulence = '        "(k|epsilon|omega)" 1e-4;\n'
            relaxation_equations = (
                f"        U               {urf_U};\n"
                f"        k               {urf_turb};\n"
                f"        epsilon         {urf_turb};\n"
                f"        omega           {urf_turb};\n"
            )
            if self.config.turbulence_type == TurbulenceType.LAMINAR:
                vector_solver_pattern = "U"
                residual_control_turbulence = ""
                relaxation_equations = f"        U               {urf_U};\n"

            content += f'''
solvers
{{
    p
    {{
        solver          GAMG;
        tolerance       1e-06;
        relTol          0.1;
        smoother        GaussSeidel;
    }}

    pFinal
    {{
        $p;
        relTol          0;
    }}

    "{vector_solver_pattern}"
    {{
        solver          smoothSolver;
        smoother        symGaussSeidel;
        tolerance       1e-06;
        relTol          0.1;
    }}
}}

SIMPLE
{{
    nNonOrthogonalCorrectors {n_non_orth_correctors};
    consistent      yes;{pref_block}

    residualControl
    {{
        p               1e-4;
        U               1e-4;
{residual_control_turbulence.rstrip()}
    }}
}}

relaxationFactors
{{
    equations
    {{
{relaxation_equations.rstrip()}
    }}
    fields
    {{
        p               {urf_p};
    }}
}}

// ************************************************************************* //
'''
        elif self.config.flow_type == FlowType.MULTIPHASE:
            # VOF multiphase - interFoam uses PIMPLE with MULES
            content += f'''
solvers
{{
    "alpha.water.*"
    {{
        nAlphaCorr      2;
        nAlphaSubCycles 1;
        cAlpha          1;

        MULESCorr       yes;
        nLimiterIter    5;

        solver          smoothSolver;
        smoother        symGaussSeidel;
        tolerance       1e-8;
        relTol          0;
    }}

    "pcorr.*"
    {{
        solver          PCG;
        preconditioner  DIC;
        tolerance       1e-5;
        relTol          0;
    }}

    p_rgh
    {{
        solver          PCG;
        preconditioner  DIC;
        tolerance       1e-07;
        relTol          0.05;
    }}

    p_rghFinal
    {{
        $p_rgh;
        relTol          0;
    }}

    U
    {{
        solver          smoothSolver;
        smoother        symGaussSeidel;
        tolerance       1e-06;
        relTol          0;
    }}

    UFinal
    {{
        $U;
        relTol          0;
    }}
}}

PIMPLE
{{
    momentumPredictor   no;
    nOuterCorrectors    1;
    nCorrectors         3;
    nNonOrthogonalCorrectors {n_non_orth_correctors};
}}

relaxationFactors
{{
    equations
    {{
        ".*"            1;
    }}
}}

// ************************************************************************* //
'''
        elif self.config.flow_type == FlowType.COMPRESSIBLE:
            # rhoCentralFoam uses minimal fvSolution
            content += f'''
solvers
{{
    "(rho|rhoU|rhoE)"
    {{
        solver          diagonal;
    }}

    U
    {{
        solver          smoothSolver;
        smoother        GaussSeidel;
        nSweeps         2;
        tolerance       1e-09;
        relTol          0.01;
    }}

    e
    {{
        $U;
        tolerance       1e-10;
        relTol          0;
    }}
}}

PIMPLE
{{
    nOuterCorrectors 1;
    nCorrectors 2;
    nNonOrthogonalCorrectors {n_non_orth_correctors};
}}

// ************************************************************************* //
'''
        else:
            vector_solver_pattern = "(U|k|epsilon|omega)"
            vector_solver_final_pattern = "(U|k|epsilon|omega)Final"
            if self.config.turbulence_type == TurbulenceType.LAMINAR:
                vector_solver_pattern = "U"
                vector_solver_final_pattern = "UFinal"

            algorithm_block = f'''
PIMPLE
{{
    nOuterCorrectors 2;
    nCorrectors     2;
    nNonOrthogonalCorrectors {n_non_orth_correctors};{pref_block}
}}
'''
            if self.config.solver in {"icoFoam", "pisoFoam"}:
                algorithm_block = f'''
PISO
{{
    nCorrectors     2;
    nNonOrthogonalCorrectors {n_non_orth_correctors};{pref_block}
}}
'''

            content += f'''
solvers
{{
    p
    {{
        solver          GAMG;
        tolerance       1e-06;
        relTol          0.01;
        smoother        GaussSeidel;
    }}

    pFinal
    {{
        $p;
        relTol          0;
    }}

    "{vector_solver_pattern}"
    {{
        solver          smoothSolver;
        smoother        symGaussSeidel;
        tolerance       1e-06;
        relTol          0.1;
    }}

    "{vector_solver_final_pattern}"
    {{
        $U;
        relTol          0;
    }}
}}
{algorithm_block}

// ************************************************************************* //
'''
        return content

    def _generate_block_mesh_dict(self) -> str:
        """Generate blockMeshDict file."""
        content = self._header("dictionary", "blockMeshDict")

        mp = self.config.mesh_params

        content += f'''
scale   1;

vertices
(
    ({mp.x_min} {mp.y_min} {mp.z_min})  // 0
    ({mp.x_max} {mp.y_min} {mp.z_min})  // 1
    ({mp.x_max} {mp.y_max} {mp.z_min})  // 2
    ({mp.x_min} {mp.y_max} {mp.z_min})  // 3
    ({mp.x_min} {mp.y_min} {mp.z_max})  // 4
    ({mp.x_max} {mp.y_min} {mp.z_max})  // 5
    ({mp.x_max} {mp.y_max} {mp.z_max})  // 6
    ({mp.x_min} {mp.y_max} {mp.z_max})  // 7
);

blocks
(
    hex (0 1 2 3 4 5 6 7) ({mp.n_cells_x} {mp.n_cells_y} {mp.n_cells_z}) simpleGrading ({mp.grading_x} {mp.grading_y} {mp.grading_z})
);

edges
(
);

boundary
(
'''
        # Generate boundary patches
        for name, patch_info in mp.patches.items():
            patch_type = patch_info.get("type", "patch")
            content += f"    {name}\n"
            content += f"    {{\n"
            content += f"        type {patch_type};\n"
            for key, value in patch_info.items():
                if key in {"type", "faces"}:
                    continue
                if isinstance(value, (list, tuple)):
                    rendered = f"({' '.join(str(item) for item in value)})"
                else:
                    rendered = str(value)
                content += f"        {key} {rendered};\n"
            content += f"        faces\n"
            content += f"        (\n"

            faces = patch_info.get("faces", [])
            for face in faces:
                if face == "x_min":
                    content += "            (0 4 7 3)\n"
                elif face == "x_max":
                    content += "            (1 2 6 5)\n"
                elif face == "y_min":
                    content += "            (0 1 5 4)\n"
                elif face == "y_max":
                    content += "            (3 7 6 2)\n"
                elif face == "z_min":
                    content += "            (0 3 2 1)\n"
                elif face == "z_max":
                    content += "            (4 5 6 7)\n"

            content += f"        );\n"
            content += f"    }}\n"

        content += ''');

mergePatchPairs
(
);

// ************************************************************************* //
'''
        return content

    def _generate_background_mesh_dict(self) -> str:
        """Generate background blockMeshDict for snappyHexMesh."""
        content = self._header("dictionary", "blockMeshDict")

        sp = self.config.snappy_params

        content += f'''
scale   1;

vertices
(
    ({sp.x_min} {sp.y_min} {sp.z_min})  // 0
    ({sp.x_max} {sp.y_min} {sp.z_min})  // 1
    ({sp.x_max} {sp.y_max} {sp.z_min})  // 2
    ({sp.x_min} {sp.y_max} {sp.z_min})  // 3
    ({sp.x_min} {sp.y_min} {sp.z_max})  // 4
    ({sp.x_max} {sp.y_min} {sp.z_max})  // 5
    ({sp.x_max} {sp.y_max} {sp.z_max})  // 6
    ({sp.x_min} {sp.y_max} {sp.z_max})  // 7
);

blocks
(
    hex (0 1 2 3 4 5 6 7) ({sp.n_cells_x} {sp.n_cells_y} {sp.n_cells_z}) simpleGrading (1 1 1);
);

edges
(
);

boundary
(
);

mergePatchPairs
(
);

// ************************************************************************* //
'''
        return content

    def _generate_snappy_hex_mesh_dict(self) -> str:
        """Generate complete snappyHexMeshDict file."""
        content = self._header("dictionary", "snappyHexMeshDict")

        sp = self.config.snappy_params

        # Header
        content += f'''
// Which of the steps to run
castellatedMesh {str(sp.castellated_mesh).lower()};
snap             {str(sp.snap).lower()};
addLayers        {str(sp.add_layers_phase).lower()};

// Geometry definition
geometry
{{
'''
        # Add STL files
        for stl_entry in sp.stl_files:
            stl_name = stl_entry.get("name", "geometry")
            stl_path = stl_entry.get("path", "geometry.stl")
            stl_type = stl_entry.get("type", "triSurfaceMesh")

            content += f'''    {stl_name}
    {{
        type {stl_type};
        file "{stl_path}";
    }}

'''

        # Add refinement regions
        for region in sp.refinement_regions:
            content += f'''    {region.name}
    {{
        type searchable'''

            if region.type == "box":
                content += f'''Box;
        min ({region.min_point[0]} {region.min_point[1]} {region.min_point[2]});
        max ({region.max_point[0]} {region.max_point[1]} {region.max_point[2]});
    }}

'''
            elif region.type == "sphere":
                content += f'''Sphere;
        centre ({region.centre[0]} {region.centre[1]} {region.centre[2]});
        radius {region.radius};
    }}

'''
            elif region.type == "cylinder":
                content += f'''Cylinder;
        point1 ({region.point1[0]} {region.point1[1]} {region.point1[2]});
        point2 ({region.point2[0]} {region.point2[1]} {region.point2[2]});
        radius {region.radius};
    }}

'''

        content += f'''}}

// Castellated mesh controls
castellatedMeshControls
{{
    maxLocalCells {sp.max_local_cells};
    maxGlobalCells {sp.max_global_cells};
    minRefinementCells {sp.min_refinement_cells};
    maxLoadUnbalance {sp.max_load_unbalance};
    nCellsBetweenLevels {sp.n_cells_between_levels};

    features
    (
    );

    refinementSurfaces
    {{
'''

        # Add surface refinement
        for stl_entry in sp.stl_files:
            stl_name = stl_entry.get("name", "geometry")
            min_level, max_level = stl_entry.get("refinement_level", sp.surface_refinement_level)

            content += f'''        {stl_name}
        {{
            level ({min_level} {max_level});
        }}
'''

        content += f'''    }}

    resolveFeatureAngle {sp.resolve_feature_angle};

    refinementRegions
    {{
'''

        # Add volume refinement regions
        for region in sp.refinement_regions:
            content += f'''        {region.name}
        {{
            mode inside;
            levels ((1E15 {region.level}));
        }}
'''

        content += f'''    }}

    locationInMesh ({sp.location_in_mesh[0]} {sp.location_in_mesh[1]} {sp.location_in_mesh[2]});
    allowFreeStandingZoneFaces {str(sp.allow_free_standing_zone_faces).lower()};
}}

// Snap controls
snapControls
{{
    nSmoothPatch {sp.n_smooth_patch};
    tolerance {sp.tolerance};
    nSolveIter {sp.n_solve_iter};
    nRelaxIter {sp.n_relax_iter};
    nFeatureSnapIter {sp.n_feature_snap_iter};
    implicitFeatureSnap {str(sp.implicit_feature_snap).lower()};
    explicitFeatureSnap {str(sp.explicit_feature_snap).lower()};
    multiRegionFeatureSnap {str(sp.multi_region_feature_snap).lower()};
}}

// Add layers controls
addLayersControls
{{
    relativeSizes {str(sp.add_layers_controls.relative_sizes).lower()};

    layers
    {{
'''

        # Add boundary layer specifications
        for patch_name, layer_spec in sp.add_layers_controls.layers.items():
            n_layers = layer_spec.get("nSurfaceLayers", 3)
            content += f'''        {patch_name}
        {{
            nSurfaceLayers {n_layers};
        }}
'''

        content += f'''    }}

    expansionRatio {sp.add_layers_controls.expansion_ratio};
    finalLayerThickness {sp.add_layers_controls.final_layer_thickness};
    minThickness {sp.add_layers_controls.min_thickness};
    nGrow {sp.add_layers_controls.n_grow};
    featureAngle {sp.add_layers_controls.feature_angle};
    slipFeatureAngle {sp.add_layers_controls.slip_feature_angle};
    nRelaxIter {sp.add_layers_controls.n_relax_iter};
    nSmoothSurfaceNormals {sp.add_layers_controls.n_smooth_surface_normals};
    nSmoothNormals {sp.add_layers_controls.n_smooth_normals};
    nSmoothThickness {sp.add_layers_controls.n_smooth_thickness};
    maxFaceThicknessRatio {sp.add_layers_controls.max_face_thickness_ratio};
    maxThicknessToMedialRatio {sp.add_layers_controls.max_thickness_to_medial_ratio};
    minMedianAxisAngle {sp.add_layers_controls.min_median_axis_angle};
    nBufferCellsNoExtrude {sp.add_layers_controls.n_buffer_cells_no_extrude};
    nLayerIter {sp.add_layers_controls.n_layer_iter};
}}

// Mesh quality controls
meshQualityControls
{{
    maxNonOrtho {sp.mesh_quality.max_non_orthogonality};
    maxBoundarySkewness {sp.mesh_quality.max_skewness};
    maxInternalSkewness {sp.mesh_quality.max_skewness};
    maxConcave 80;
    minVol {sp.mesh_quality.min_volume};
    minTetQuality {sp.mesh_quality.min_tet_quality};
    minArea -1;
    minTwist 0.02;
    minDeterminant 0.001;
    minFaceWeight {sp.mesh_quality.min_face_weight};
    minVolRatio 0.01;
    minTriangleTwist -1;

    nSmoothScale 4;
    errorReduction 0.75;

    relaxed
    {{
        maxNonOrtho 75;
    }}
}}

// Advanced settings
mergeTolerance 1e-6;

// ************************************************************************* //
'''
        return content

    def _generate_decompose_par_dict(self, n_processors: int = 4,
                                       method: str = "scotch") -> str:
        """Generate decomposeParDict file for parallel decomposition.

        Args:
            n_processors: Number of processors
            method: Decomposition method (simple, hierarchical, scotch)

        Returns:
            Content of decomposeParDict file
        """
        content = self._header("dictionary", "decomposeParDict")

        content += f'''
numberOfSubdomains {n_processors};

method          {method};
'''

        if method == "simple":
            n_x, n_y, n_z = _factorize_processors(n_processors)

            content += f'''
simpleCoeffs
{{
    n               ({n_x} {n_y} {n_z});
    delta           0.001;
}}
'''
        elif method == "hierarchical":
            n_x, n_y, n_z = _factorize_processors(n_processors)

            content += f'''
hierarchicalCoeffs
{{
    n               ({n_x} {n_y} {n_z});
    delta           0.001;
    order           xyz;
}}
'''
        elif method == "scotch":
            # Scotch method (load-balanced)
            weights = " ".join(["1"] * n_processors)
            content += '''
scotchCoeffs
{
'''
            content += f"    processorWeights ({weights});\n"
            content += '''
}
'''

        content += '''
distributed     no;

roots           ();

// ************************************************************************* //
'''
        return content

    def _generate_allrun(self) -> str:
        """Generate Allrun script."""
        solver_token = _sanitize_solver_token_for_script(self.config.solver)
        if self.config.use_snappy and self.config.snappy_params:
            # snappyHexMesh workflow
            script = f'''#!/bin/bash
cd "${{0%/*}}" || exit
. ${{WM_PROJECT_DIR:?}}/bin/tools/RunFunctions

# Generate background mesh
runApplication blockMesh

# Run snappyHexMesh
runApplication snappyHexMesh -overwrite

# Check mesh quality
runApplication checkMesh

# Run solver
runApplication {solver_token}

#------------------------------------------------------------------------------
'''
        elif self.config.flow_type == FlowType.MULTIPHASE:
            # VOF workflow: needs setFields to initialize alpha
            script = f'''#!/bin/bash
cd "${{0%/*}}" || exit
. ${{WM_PROJECT_DIR:?}}/bin/tools/RunFunctions

runApplication blockMesh
runApplication checkMesh
runApplication setFields
runApplication {solver_token}

#------------------------------------------------------------------------------
'''
        elif self.config.flow_type == FlowType.COMPRESSIBLE:
            # Compressible flow: may need setFields for shock tube
            template_id = self.config.geometry_params.get("template_id")
            if template_id == "shock_tube":
                script = f'''#!/bin/bash
cd "${{0%/*}}" || exit
. ${{WM_PROJECT_DIR:?}}/bin/tools/RunFunctions

runApplication blockMesh
runApplication checkMesh
runApplication setFields
runApplication {solver_token}

#------------------------------------------------------------------------------
'''
            else:
                script = f'''#!/bin/bash
cd "${{0%/*}}" || exit
. ${{WM_PROJECT_DIR:?}}/bin/tools/RunFunctions

runApplication blockMesh
runApplication checkMesh
runApplication {solver_token}

#------------------------------------------------------------------------------
'''
        else:
            # Standard blockMesh workflow
            script = f'''#!/bin/bash
cd "${{0%/*}}" || exit
. ${{WM_PROJECT_DIR:?}}/bin/tools/RunFunctions

runApplication blockMesh
runApplication checkMesh
runApplication {solver_token}

#------------------------------------------------------------------------------
'''
        return script

    def _generate_allclean(self) -> str:
        """Generate Allclean script."""
        return '''#!/bin/bash
cd "${0%/*}" || exit
. ${WM_PROJECT_DIR:?}/bin/tools/CleanFunctions

cleanCase

#------------------------------------------------------------------------------
'''


def create_case_config_from_template(
    template_id: str,
    parameters: Dict[str, Any],
    case_path: str
) -> CaseConfig:
    """
    Create a CaseConfig from a template and user parameters.

    Args:
        template_id: ID of the template to use
        parameters: User-provided parameter values
        case_path: Path where the case will be created

    Returns:
        Complete CaseConfig object
    """
    template = get_template(template_id)
    if template is None:
        raise ValueError(f"Unknown template: {template_id}")

    user_parameters = dict(parameters)

    # Validate parameters
    errors = template.validate_parameters(user_parameters)
    if errors:
        raise ValueError(f"Parameter validation failed: {'; '.join(errors)}")

    # Get fluid properties
    fluid_name = user_parameters.get("fluid", "water")
    fluid_props = get_fluid_properties(fluid_name)
    if fluid_props is None:
        fluid_props = {"nu": 1e-6, "rho": 1000}

    # Determine flow characteristics
    flow_type = FlowType(template.category) if template.category in [e.value for e in FlowType] else FlowType.INCOMPRESSIBLE

    # Time type based on solver
    transient_solvers = ["icoFoam", "pimpleFoam", "pisoFoam", "interFoam", "interIsoFoam", "rhoCentralFoam"]
    time_type = TimeType.TRANSIENT if template.solver in transient_solvers else TimeType.STEADY

    # Calculate Reynolds number - handle different template parameter names
    if template_id in ["dam_break", "bubble_rising"]:
        # Multiphase: use characteristic length (tank or column width)
        char_length = user_parameters.get("tank_width", user_parameters.get("column_width", 0.1))
        char_velocity = 1.0  # Gravity-driven, Re not critical
    elif template_id == "backward_step":
        char_length = user_parameters.get("step_height", 0.0127)
        char_velocity = user_parameters.get("inlet_velocity", 44.2)
    elif template_id == "channel_flow":
        char_length = _coerce_float_scalar(user_parameters.get("half_height", 1.0), "half_height")
        # For channel flow, use friction Reynolds number to estimate bulk velocity
        re_tau = _coerce_float_scalar(user_parameters.get("re_tau", 395.0), "re_tau")
        char_velocity = re_tau * fluid_props["nu"] / char_length * 20  # Rough estimate
    elif template_id == "flat_plate":
        char_length = user_parameters.get("plate_length", 1.0)
        char_velocity = user_parameters.get("inlet_velocity", 10.0)
    elif template_id == "mixing_elbow":
        char_length = user_parameters.get("pipe_diameter", 0.1)
        char_velocity = user_parameters.get("inlet_velocity", 1.0)
    elif template_id == "shock_tube":
        char_length = user_parameters.get("tube_length", 10.0)
        char_velocity = 1.0  # Not relevant for compressible shock problem
    elif template_id == "supersonic_nozzle":
        import math
        throat_area = _coerce_float_scalar(user_parameters.get("throat_area", 0.0005), "throat_area")
        char_length = math.sqrt(throat_area)  # Characteristic length from throat
        char_velocity = 1.0  # Not relevant for compressible nozzle
    else:
        char_length = user_parameters.get("diameter", user_parameters.get("width", 0.1))
        char_velocity = user_parameters.get("inlet_velocity", user_parameters.get("lid_velocity", 1.0))

    char_length = _coerce_float_scalar(char_length, "characteristic_length")
    if char_length <= 0:
        raise ValueError("characteristic_length 必须 > 0")
    char_velocity = _coerce_float_scalar(char_velocity, "characteristic_velocity")

    re = estimate_reynolds_number(char_velocity, char_length, fluid_props["nu"])

    # Determine turbulence model, constrained by template/solver capabilities.
    if flow_type == FlowType.MULTIPHASE:
        turbulence = TurbulenceType.LAMINAR
    elif flow_type == FlowType.COMPRESSIBLE:
        turbulence = TurbulenceType.LAMINAR  # Compressible templates usually laminar for shock capturing
    else:
        turbulence = recommend_turbulence_model(re)
        solver_info = get_solver_info(template.solver)
        if not template.supports_turbulence:
            turbulence = TurbulenceType.LAMINAR
        elif solver_info is not None and not solver_info.supports_turbulence:
            turbulence = TurbulenceType.LAMINAR

    # Create boundary definitions based on template
    boundaries = _create_boundaries_for_template(template_id, user_parameters)

    # Create mesh parameters based on template
    mesh_params = _create_mesh_for_template(template_id, user_parameters)

    # Control parameters - adjust for different templates
    if flow_type == FlowType.MULTIPHASE:
        control = {
            "end_time": user_parameters.get("end_time", 1.0),
            "delta_t": 0.001,  # Will be adjusted by adaptive time stepping
            "write_interval": 0.05,
        }
    elif flow_type == FlowType.COMPRESSIBLE:
        control = {
            "end_time": user_parameters.get("end_time", 0.01),
            "delta_t": 0.00001,  # Very small for rhoCentralFoam
            "write_interval": 0.001,
        }
    elif template_id == "channel_flow":
        control = {
            "end_time": user_parameters.get("end_time", 100.0),
            "delta_t": 0.001,
            "write_interval": 1.0,
        }
    else:
        control = {
            "end_time": user_parameters.get("end_time", 1000 if time_type == TimeType.STEADY else 10),
            "delta_t": 1 if time_type == TimeType.STEADY else 0.001,
            "write_interval": 100 if time_type == TimeType.STEADY else 0.1,
        }

    # Determine if this is a closed system (needs pressure reference point)
    is_closed = template_id in ["cavity_flow", "natural_convection"]

    # Store template_id in geometry_params for later use
    geometry_params = dict(user_parameters)
    geometry_params["template_id"] = template_id

    return CaseConfig(
        case_path=Path(case_path),
        solver=template.solver,
        flow_type=flow_type,
        time_type=time_type,
        turbulence_type=turbulence,
        is_2d=template.is_2d,
        fluid_properties=fluid_props,
        geometry_params=geometry_params,
        boundary_definitions=boundaries,
        mesh_params=mesh_params,
        control_params=control,
        reynolds_number=re,
        is_closed_system=is_closed,
    )


def _create_boundaries_for_template(template_id: str, params: Dict[str, Any]) -> List[BoundaryDefinition]:
    """Create boundary definitions based on template type."""
    if template_id == "pipe_flow":
        return [
            BoundaryDefinition("inlet", BoundaryType.INLET, "velocity"),
            BoundaryDefinition("outlet", BoundaryType.OUTLET, "pressure"),
            BoundaryDefinition("walls", BoundaryType.WALL, "no_slip"),
            BoundaryDefinition("frontAndBack", BoundaryType.EMPTY),
        ]
    elif template_id == "cavity_flow":
        return [
            BoundaryDefinition("movingWall", BoundaryType.WALL, "moving"),
            BoundaryDefinition("fixedWalls", BoundaryType.WALL, "no_slip"),
            BoundaryDefinition("frontAndBack", BoundaryType.EMPTY),
        ]
    elif template_id == "cylinder_flow":
        return [
            BoundaryDefinition("inlet", BoundaryType.INLET, "velocity"),
            BoundaryDefinition("outlet", BoundaryType.OUTLET, "pressure"),
            BoundaryDefinition("cylinder", BoundaryType.WALL, "no_slip"),
            BoundaryDefinition("top", BoundaryType.SYMMETRY),
            BoundaryDefinition("bottom", BoundaryType.SYMMETRY),
            BoundaryDefinition("frontAndBack", BoundaryType.EMPTY),
        ]
    elif template_id == "natural_convection":
        return [
            BoundaryDefinition("hotWall", BoundaryType.WALL, "no_slip"),
            BoundaryDefinition("coldWall", BoundaryType.WALL, "no_slip"),
            BoundaryDefinition("topAndBottom", BoundaryType.WALL, "no_slip"),
            BoundaryDefinition("frontAndBack", BoundaryType.EMPTY),
        ]
    # --- NEW TEMPLATES ---
    elif template_id == "dam_break":
        return [
            BoundaryDefinition("walls", BoundaryType.WALL, "no_slip"),
            BoundaryDefinition("atmosphere", BoundaryType.OUTLET, "atmosphere"),
            BoundaryDefinition("frontAndBack", BoundaryType.EMPTY),
        ]
    elif template_id == "bubble_rising":
        return [
            BoundaryDefinition("walls", BoundaryType.WALL, "no_slip"),
            BoundaryDefinition("bottom", BoundaryType.WALL, "no_slip"),
            BoundaryDefinition("atmosphere", BoundaryType.OUTLET, "atmosphere"),
            BoundaryDefinition("frontAndBack", BoundaryType.EMPTY),
        ]
    elif template_id == "backward_step":
        return [
            BoundaryDefinition("inlet", BoundaryType.INLET, "velocity"),
            BoundaryDefinition("outlet", BoundaryType.OUTLET, "pressure"),
            BoundaryDefinition("upperWall", BoundaryType.WALL, "no_slip"),
            BoundaryDefinition("lowerWall", BoundaryType.WALL, "no_slip"),
            BoundaryDefinition("frontAndBack", BoundaryType.EMPTY),
        ]
    elif template_id == "channel_flow":
        return [
            BoundaryDefinition("inlet", BoundaryType.INLET, "cyclic"),
            BoundaryDefinition("outlet", BoundaryType.OUTLET, "cyclic"),
            BoundaryDefinition("topWall", BoundaryType.WALL, "no_slip"),
            BoundaryDefinition("bottomWall", BoundaryType.WALL, "no_slip"),
            BoundaryDefinition("frontAndBack", BoundaryType.EMPTY),
        ]
    elif template_id == "heat_exchanger":
        return [
            BoundaryDefinition("inlet", BoundaryType.INLET, "velocity"),
            BoundaryDefinition("outlet", BoundaryType.OUTLET, "pressure"),
            BoundaryDefinition("walls", BoundaryType.WALL, "no_slip"),
            BoundaryDefinition("frontAndBack", BoundaryType.EMPTY),
        ]
    elif template_id == "flat_plate":
        return [
            BoundaryDefinition("inlet", BoundaryType.INLET, "velocity"),
            BoundaryDefinition("outlet", BoundaryType.OUTLET, "pressure"),
            BoundaryDefinition("plate", BoundaryType.WALL, "no_slip"),
            BoundaryDefinition("top", BoundaryType.SYMMETRY),
            BoundaryDefinition("frontAndBack", BoundaryType.EMPTY),
        ]
    elif template_id == "mixing_elbow":
        return [
            BoundaryDefinition("inlet", BoundaryType.INLET, "velocity"),
            BoundaryDefinition("outlet", BoundaryType.OUTLET, "pressure"),
            BoundaryDefinition("walls", BoundaryType.WALL, "no_slip"),
        ]
    elif template_id == "shock_tube":
        return [
            BoundaryDefinition("inlet", BoundaryType.WALL, "no_slip"),
            BoundaryDefinition("outlet", BoundaryType.WALL, "no_slip"),
            BoundaryDefinition("walls", BoundaryType.SYMMETRY),
            BoundaryDefinition("frontAndBack", BoundaryType.EMPTY),
        ]
    elif template_id == "supersonic_nozzle":
        return [
            BoundaryDefinition("inlet", BoundaryType.INLET, "pressure"),
            BoundaryDefinition("outlet", BoundaryType.OUTLET, "pressure"),
            BoundaryDefinition("walls", BoundaryType.WALL, "no_slip"),
            BoundaryDefinition("frontAndBack", BoundaryType.EMPTY),
        ]
    else:
        return [
            BoundaryDefinition("inlet", BoundaryType.INLET),
            BoundaryDefinition("outlet", BoundaryType.OUTLET),
            BoundaryDefinition("walls", BoundaryType.WALL),
        ]


def _create_mesh_for_template(template_id: str, params: Dict[str, Any]) -> BlockMeshParams:
    """Create optimized mesh parameters based on template type.

    Automatically calculates:
    - Cell counts to maintain aspect ratio close to 1
    - Grading for boundary layer resolution in pipe flows
    - Adjusted density based on Reynolds number
    """
    mesh_density = params.get("mesh_density", "medium")
    density_multiplier = {"coarse": 0.5, "medium": 1.0, "fine": 2.0}.get(mesh_density, 1.0)

    # Get fluid properties for Reynolds number estimation
    fluid_name = params.get("fluid", "water")
    fluid_nu = {"water": 1e-6, "air": 1.5e-5, "oil": 1e-4}.get(fluid_name, 1e-6)

    if template_id == "pipe_flow":
        diameter = params.get("diameter", 0.1)
        length = params.get("length", 1.0)
        velocity = params.get("inlet_velocity", 1.0)
        Re = velocity * diameter / fluid_nu

        # Base radial cells: more cells for higher Re
        if Re < 2000:
            n_radial = int(15 * density_multiplier)
        elif Re < 10000:
            n_radial = int(25 * density_multiplier)
        else:
            n_radial = int(35 * density_multiplier)

        # Axial cells: maintain aspect ratio close to 1
        cell_size_y = diameter / n_radial
        n_axial = max(int(length / cell_size_y), int(20 * density_multiplier))

        # Limit axial cells to avoid excessively large meshes
        n_axial = min(n_axial, int(500 * density_multiplier))

        # Grading for boundary layer resolution
        # For turbulent flows, use grading to cluster cells near walls
        if Re > 4000:
            grading_y = 0.2  # Cells get smaller toward walls (symmetric grading)
        elif Re > 2000:
            grading_y = 0.5
        else:
            grading_y = 1.0  # No grading for laminar flows

        mesh = create_pipe_mesh_params(diameter, length, n_radial, n_axial, is_2d=True)
        mesh.grading_y = grading_y
        return mesh

    elif template_id == "cavity_flow":
        width = params.get("width", 0.1)
        height = params.get("height", 0.1)
        velocity = params.get("lid_velocity", 1.0)
        Re = velocity * width / fluid_nu

        # Cell count based on Re
        if Re < 1000:
            n_base = int(30 * density_multiplier)
        elif Re < 5000:
            n_base = int(50 * density_multiplier)
        else:
            n_base = int(80 * density_multiplier)

        # Adjust for non-square cavities to maintain aspect ratio ~1
        aspect = width / height
        if aspect > 1:
            n_x = n_base
            n_y = max(int(n_base / aspect), 10)
        else:
            n_y = n_base
            n_x = max(int(n_base * aspect), 10)

        return create_cavity_mesh_params(width, height, n_x=n_x, n_y=n_y, is_2d=True)

    elif template_id == "cylinder_flow":
        diameter = params.get("cylinder_diameter", 0.1)
        domain_mult = params.get("domain_length", 20.0)
        velocity = params.get("inlet_velocity", 1.0)
        Re = velocity * diameter / fluid_nu

        # More cells for higher Re
        if Re < 1000:
            n_base = int(30 * density_multiplier)
        elif Re < 10000:
            n_base = int(50 * density_multiplier)
        else:
            n_base = int(80 * density_multiplier)

        mesh_params = create_external_flow_mesh_params(diameter, domain_mult * diameter, n_cells_base=n_base)
        # Override patches to match cylinder_flow boundary definitions
        mesh_params.patches = {
            "inlet": {"type": "patch", "faces": ["x_min"]},
            "outlet": {"type": "patch", "faces": ["x_max"]},
            "top": {"type": "symmetry", "faces": ["y_max"]},
            "bottom": {"type": "symmetry", "faces": ["y_min"]},
            "frontAndBack": {"type": "empty", "faces": ["z_min", "z_max"]},
        }
        return mesh_params

    elif template_id == "natural_convection":
        width = params.get("width", 0.1)
        height = params.get("height", 0.1)
        n_base = int(50 * density_multiplier)

        # Adjust for non-square cavities
        aspect = width / height
        if aspect > 1:
            n_x = n_base
            n_y = max(int(n_base / aspect), 10)
        else:
            n_y = n_base
            n_x = max(int(n_base * aspect), 10)

        mesh = create_cavity_mesh_params(width, height, n_x=n_x, n_y=n_y, is_2d=True)
        # Add grading toward hot and cold walls for better temperature resolution
        mesh.grading_x = 0.5
        return mesh

    # --- NEW TEMPLATES ---
    elif template_id == "dam_break":
        tank_width = params.get("tank_width", 0.584)
        tank_height = params.get("tank_height", 0.584)
        n_base = int(46 * density_multiplier)  # Official tutorial uses 46

        # Maintain aspect ratio
        aspect = tank_width / tank_height
        n_x = n_base
        n_y = max(int(n_base / aspect), 20) if aspect > 1 else n_base

        mesh = create_cavity_mesh_params(tank_width, tank_height, n_x=n_x, n_y=n_y, is_2d=True)
        mesh.patches = {
            "walls": {"type": "wall", "faces": ["x_min", "y_min", "x_max"]},
            "atmosphere": {"type": "patch", "faces": ["y_max"]},
            "frontAndBack": {"type": "empty", "faces": ["z_min", "z_max"]},
        }
        return mesh

    elif template_id == "bubble_rising":
        column_width = params.get("column_width", 0.06)
        column_height = params.get("column_height", 0.12)
        bubble_d = params.get("bubble_diameter", 0.01)

        # Need fine mesh around bubble - at least 20 cells per bubble diameter
        cells_per_diameter = 20
        n_x = max(int(column_width / bubble_d * cells_per_diameter * density_multiplier), 30)
        n_y = max(int(column_height / bubble_d * cells_per_diameter * density_multiplier), 60)

        mesh = create_cavity_mesh_params(column_width, column_height, n_x=n_x, n_y=n_y, is_2d=True)
        mesh.patches = {
            "walls": {"type": "wall", "faces": ["x_min", "x_max"]},
            "bottom": {"type": "wall", "faces": ["y_min"]},
            "atmosphere": {"type": "patch", "faces": ["y_max"]},
            "frontAndBack": {"type": "empty", "faces": ["z_min", "z_max"]},
        }
        return mesh

    elif template_id == "backward_step":
        step_h = params.get("step_height", 0.0127)
        inlet_h = params.get("inlet_height", 0.0127)
        outlet_mult = params.get("outlet_length", 30.0)
        velocity = params.get("inlet_velocity", 44.2)

        # Total domain
        total_height = step_h + inlet_h  # Expansion ratio = 2
        outlet_length = step_h * outlet_mult
        inlet_length = step_h * 5  # 5H upstream

        Re = velocity * step_h / fluid_nu
        if Re < 5000:
            n_y = int(30 * density_multiplier)
        elif Re < 50000:
            n_y = int(50 * density_multiplier)
        else:
            n_y = int(80 * density_multiplier)

        # Cells in x: inlet + outlet
        cell_size = total_height / n_y
        n_x_inlet = max(int(inlet_length / cell_size), 10)
        n_x_outlet = max(int(outlet_length / cell_size), 50)

        # Create simple rectangular mesh (actual L-shape would need more complex blockMesh)
        mesh = BlockMeshParams()
        mesh.x_min = -inlet_length
        mesh.x_max = outlet_length
        mesh.y_min = 0
        mesh.y_max = total_height
        mesh.z_min = -0.005
        mesh.z_max = 0.005
        mesh.n_cells_x = n_x_inlet + n_x_outlet
        mesh.n_cells_y = n_y
        mesh.n_cells_z = 1
        mesh.patches = {
            "inlet": {"type": "patch", "faces": ["x_min"]},
            "outlet": {"type": "patch", "faces": ["x_max"]},
            "upperWall": {"type": "wall", "faces": ["y_max"]},
            "lowerWall": {"type": "wall", "faces": ["y_min"]},
            "frontAndBack": {"type": "empty", "faces": ["z_min", "z_max"]},
        }
        return mesh

    elif template_id == "channel_flow":
        half_h = params.get("half_height", 1.0)
        length = params.get("length", 6.283)  # 2*pi
        span = params.get("span", 3.142)  # pi
        re_tau = params.get("re_tau", 395.0)

        # For LES/DNS: need fine resolution, ~y+ < 1 at wall
        # Estimate: n_y ~ 2 * Re_tau for well-resolved LES
        n_y = max(int(re_tau * density_multiplier), 50)
        # Streamwise and spanwise: coarser
        n_x = max(int(n_y * length / (2 * half_h) * 0.5), 30)
        n_z = max(int(n_y * span / (2 * half_h) * 0.5), 20)

        mesh = BlockMeshParams()
        mesh.x_min = 0
        mesh.x_max = length
        mesh.y_min = 0
        mesh.y_max = 2 * half_h
        mesh.z_min = 0
        mesh.z_max = span
        mesh.n_cells_x = n_x
        mesh.n_cells_y = n_y
        mesh.n_cells_z = n_z
        # Grading toward walls
        mesh.grading_y = 10.0  # Expand toward center (cells smaller at walls)
        mesh.patches = {
            "inlet": {"type": "cyclic", "faces": ["x_min"], "neighbourPatch": "outlet"},
            "outlet": {"type": "cyclic", "faces": ["x_max"], "neighbourPatch": "inlet"},
            "topWall": {"type": "wall", "faces": ["y_max"]},
            "bottomWall": {"type": "wall", "faces": ["y_min"]},
            "frontAndBack": {"type": "cyclic", "faces": ["z_min", "z_max"]},
        }
        return mesh

    elif template_id == "heat_exchanger":
        diameter = params.get("diameter", 0.05)
        length = params.get("length", 1.0)
        velocity = params.get("inlet_velocity", 1.0)
        Re = velocity * diameter / fluid_nu

        # Similar to pipe_flow but with focus on thermal boundary layer
        if Re < 2000:
            n_radial = int(20 * density_multiplier)
        elif Re < 10000:
            n_radial = int(35 * density_multiplier)
        else:
            n_radial = int(50 * density_multiplier)

        cell_size_y = diameter / n_radial
        n_axial = max(int(length / cell_size_y), int(30 * density_multiplier))
        n_axial = min(n_axial, int(500 * density_multiplier))

        # Grading for thermal boundary layer
        grading_y = 0.3 if Re > 2000 else 1.0

        mesh = create_pipe_mesh_params(diameter, length, n_radial, n_axial, is_2d=True)
        mesh.grading_y = grading_y
        return mesh

    elif template_id == "flat_plate":
        plate_length = params.get("plate_length", 1.0)
        domain_height = params.get("domain_height", 0.1)
        velocity = params.get("inlet_velocity", 10.0)
        Re = velocity * plate_length / fluid_nu

        # Streamwise cells - more at leading edge
        if Re < 100000:
            n_x = int(60 * density_multiplier)
        elif Re < 1000000:
            n_x = int(100 * density_multiplier)
        else:
            n_x = int(150 * density_multiplier)

        # Wall-normal cells - clustered near plate
        n_y = int(40 * density_multiplier)

        # Lead-in section upstream of plate (10% of plate length)
        lead_in = plate_length * 0.1

        mesh = BlockMeshParams()
        mesh.x_min = -lead_in
        mesh.x_max = plate_length
        mesh.y_min = 0
        mesh.y_max = domain_height
        mesh.z_min = -0.005
        mesh.z_max = 0.005
        mesh.n_cells_x = n_x
        mesh.n_cells_y = n_y
        mesh.n_cells_z = 1
        # Grading: cluster cells near plate surface
        mesh.grading_y = 20.0  # Strong expansion ratio away from wall
        mesh.patches = {
            "inlet": {"type": "patch", "faces": ["x_min"]},
            "outlet": {"type": "patch", "faces": ["x_max"]},
            "plate": {"type": "wall", "faces": ["y_min"]},
            "top": {"type": "symmetry", "faces": ["y_max"]},
            "frontAndBack": {"type": "empty", "faces": ["z_min", "z_max"]},
        }
        return mesh

    elif template_id == "mixing_elbow":
        pipe_d = params.get("pipe_diameter", 0.1)
        inlet_len = params.get("inlet_length", 0.5)
        outlet_len = params.get("outlet_length", 0.5)
        bend_r = params.get("bend_radius", 0.15)
        velocity = params.get("inlet_velocity", 1.0)
        Re = velocity * pipe_d / fluid_nu

        # Simplified as straight pipe (actual elbow needs snappyHexMesh or multi-block)
        # Use a rectangular domain approximating the elbow unfolded
        total_length = inlet_len + (3.14159 * bend_r / 2) + outlet_len

        if Re < 5000:
            n_cross = int(20 * density_multiplier)
        elif Re < 50000:
            n_cross = int(30 * density_multiplier)
        else:
            n_cross = int(45 * density_multiplier)

        cell_size = pipe_d / n_cross
        n_axial = max(int(total_length / cell_size), int(40 * density_multiplier))
        n_axial = min(n_axial, int(500 * density_multiplier))

        mesh = BlockMeshParams()
        mesh.x_min = 0
        mesh.x_max = total_length
        mesh.y_min = -pipe_d / 2
        mesh.y_max = pipe_d / 2
        mesh.z_min = -pipe_d / 2
        mesh.z_max = pipe_d / 2
        mesh.n_cells_x = n_axial
        mesh.n_cells_y = n_cross
        mesh.n_cells_z = n_cross
        mesh.grading_y = 0.3
        mesh.grading_z = 0.3
        mesh.patches = {
            "inlet": {"type": "patch", "faces": ["x_min"]},
            "outlet": {"type": "patch", "faces": ["x_max"]},
            "walls": {"type": "wall", "faces": ["y_min", "y_max", "z_min", "z_max"]},
        }
        return mesh

    elif template_id == "shock_tube":
        tube_length = params.get("tube_length", 10.0)
        tube_height = 0.1  # Fixed height for 2D

        # Fine mesh for shock capturing
        n_x = int(1000 * density_multiplier)  # Lots of cells along length
        n_y = 1  # Single cell in y-direction
        n_z = 1  # Single cell in z-direction

        mesh = BlockMeshParams()
        mesh.x_min = 0
        mesh.x_max = tube_length
        mesh.y_min = -tube_height / 2
        mesh.y_max = tube_height / 2
        mesh.z_min = -tube_height / 2
        mesh.z_max = tube_height / 2
        mesh.n_cells_x = n_x
        mesh.n_cells_y = n_y
        mesh.n_cells_z = n_z
        mesh.patches = {
            "inlet": {"type": "wall", "faces": ["x_min"]},
            "outlet": {"type": "wall", "faces": ["x_max"]},
            "walls": {"type": "symmetry", "faces": ["y_min", "y_max"]},
            "frontAndBack": {"type": "empty", "faces": ["z_min", "z_max"]},
        }
        return mesh

    elif template_id == "supersonic_nozzle":
        length = params.get("length", 0.3)
        inlet_area = params.get("inlet_area", 0.001)
        throat_area = params.get("throat_area", 0.0005)
        outlet_area = params.get("outlet_area", 0.00125)

        import math
        inlet_r = math.sqrt(inlet_area / math.pi)
        throat_r = math.sqrt(throat_area / math.pi)
        outlet_r = math.sqrt(outlet_area / math.pi)
        max_r = max(inlet_r, outlet_r)

        # Fine mesh for shock/expansion capturing
        n_x = int(200 * density_multiplier)
        n_y = int(30 * density_multiplier)

        mesh = BlockMeshParams()
        mesh.x_min = 0
        mesh.x_max = length
        mesh.y_min = 0
        mesh.y_max = max_r
        mesh.z_min = -0.001
        mesh.z_max = 0.001
        mesh.n_cells_x = n_x
        mesh.n_cells_y = n_y
        mesh.n_cells_z = 1
        mesh.grading_y = 1.0
        mesh.patches = {
            "inlet": {"type": "patch", "faces": ["x_min"]},
            "outlet": {"type": "patch", "faces": ["x_max"]},
            "walls": {"type": "wall", "faces": ["y_max"]},
            "frontAndBack": {"type": "empty", "faces": ["z_min", "z_max"]},
        }
        return mesh

    else:
        return BlockMeshParams()
