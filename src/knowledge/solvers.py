"""OpenFOAM Knowledge Base - Solver selection rules."""

from typing import Dict, Any, Optional
from dataclasses import dataclass
from enum import Enum


class FlowType(str, Enum):
    """Types of flow problems."""
    INCOMPRESSIBLE = "incompressible"
    COMPRESSIBLE = "compressible"
    HEAT_TRANSFER = "heat_transfer"
    MULTIPHASE = "multiphase"


class TimeType(str, Enum):
    """Time dependency of the problem."""
    STEADY = "steady"
    TRANSIENT = "transient"


class TurbulenceType(str, Enum):
    """Turbulence modeling approach."""
    LAMINAR = "laminar"
    RANS = "rans"  # Reynolds-Averaged Navier-Stokes
    LES = "les"    # Large Eddy Simulation


@dataclass
class SolverInfo:
    """Information about an OpenFOAM solver."""
    name: str
    description: str
    flow_type: FlowType
    time_type: TimeType
    supports_turbulence: bool = True
    supports_heat: bool = False
    typical_applications: list[str] = None

    def __post_init__(self):
        if self.typical_applications is None:
            self.typical_applications = []


# Solver selection knowledge base
SOLVERS: Dict[str, SolverInfo] = {
    # Incompressible solvers
    "simpleFoam": SolverInfo(
        name="simpleFoam",
        description="稳态不可压缩流求解器（SIMPLE算法）",
        flow_type=FlowType.INCOMPRESSIBLE,
        time_type=TimeType.STEADY,
        supports_turbulence=True,
        typical_applications=["管道流动", "外部绕流", "内部流动"]
    ),
    "icoFoam": SolverInfo(
        name="icoFoam",
        description="瞬态不可压缩层流求解器",
        flow_type=FlowType.INCOMPRESSIBLE,
        time_type=TimeType.TRANSIENT,
        supports_turbulence=False,
        typical_applications=["层流管道流", "方腔流"]
    ),
    "pimpleFoam": SolverInfo(
        name="pimpleFoam",
        description="瞬态不可压缩流求解器（PIMPLE算法）",
        flow_type=FlowType.INCOMPRESSIBLE,
        time_type=TimeType.TRANSIENT,
        supports_turbulence=True,
        typical_applications=["非定常流动", "涡脱落", "周期性流动"]
    ),
    "pisoFoam": SolverInfo(
        name="pisoFoam",
        description="瞬态不可压缩流求解器（PISO算法）",
        flow_type=FlowType.INCOMPRESSIBLE,
        time_type=TimeType.TRANSIENT,
        supports_turbulence=True,
        typical_applications=["瞬态流动", "大时间步长计算"]
    ),

    # Compressible solvers
    "rhoSimpleFoam": SolverInfo(
        name="rhoSimpleFoam",
        description="稳态可压缩流求解器",
        flow_type=FlowType.COMPRESSIBLE,
        time_type=TimeType.STEADY,
        supports_turbulence=True,
        typical_applications=["高速内流", "喷嘴流动"]
    ),
    "rhoPimpleFoam": SolverInfo(
        name="rhoPimpleFoam",
        description="瞬态可压缩流求解器",
        flow_type=FlowType.COMPRESSIBLE,
        time_type=TimeType.TRANSIENT,
        supports_turbulence=True,
        typical_applications=["声学", "压力波传播"]
    ),
    "sonicFoam": SolverInfo(
        name="sonicFoam",
        description="瞬态跨音速/超音速流求解器",
        flow_type=FlowType.COMPRESSIBLE,
        time_type=TimeType.TRANSIENT,
        supports_turbulence=True,
        typical_applications=["超音速流", "激波"]
    ),
    "rhoCentralFoam": SolverInfo(
        name="rhoCentralFoam",
        description="密度基可压缩流求解器（中心差分格式，适用于激波捕捉）",
        flow_type=FlowType.COMPRESSIBLE,
        time_type=TimeType.TRANSIENT,
        supports_turbulence=True,
        supports_heat=True,
        typical_applications=["激波管", "超音速喷管", "高马赫数流动", "爆炸波"]
    ),

    # Heat transfer solvers
    "buoyantSimpleFoam": SolverInfo(
        name="buoyantSimpleFoam",
        description="稳态浮力驱动流求解器",
        flow_type=FlowType.HEAT_TRANSFER,
        time_type=TimeType.STEADY,
        supports_turbulence=True,
        supports_heat=True,
        typical_applications=["自然对流", "散热器"]
    ),
    "buoyantPimpleFoam": SolverInfo(
        name="buoyantPimpleFoam",
        description="瞬态浮力驱动流求解器",
        flow_type=FlowType.HEAT_TRANSFER,
        time_type=TimeType.TRANSIENT,
        supports_turbulence=True,
        supports_heat=True,
        typical_applications=["非定常传热", "热羽流"]
    ),

    # Multiphase solvers
    "interFoam": SolverInfo(
        name="interFoam",
        description="两相流VOF求解器（不可压缩、等温）",
        flow_type=FlowType.MULTIPHASE,
        time_type=TimeType.TRANSIENT,
        supports_turbulence=True,
        supports_heat=False,
        typical_applications=["溃坝", "气泡上升", "自由表面流", "液滴碰撞"]
    ),
    "interIsoFoam": SolverInfo(
        name="interIsoFoam",
        description="两相流VOF求解器（isoAdvector界面重构）",
        flow_type=FlowType.MULTIPHASE,
        time_type=TimeType.TRANSIENT,
        supports_turbulence=True,
        supports_heat=False,
        typical_applications=["高精度界面捕捉", "气泡动力学"]
    ),
}


# Solver selection rules
SOLVER_SELECTION_RULES: Dict[str, Dict[str, Any]] = {
    "incompressible": {
        "steady": {
            "laminar": "simpleFoam",
            "turbulent": "simpleFoam"
        },
        "transient": {
            "laminar": "icoFoam",
            "turbulent": "pimpleFoam"
        }
    },
    "compressible": {
        "steady": {
            "laminar": "rhoSimpleFoam",
            "turbulent": "rhoSimpleFoam"
        },
        "transient": {
            "laminar": "rhoPimpleFoam",
            "turbulent": "rhoPimpleFoam"
        },
        "supersonic": "rhoCentralFoam",
        "shock": "rhoCentralFoam"
    },
    "heat_transfer": {
        "steady": {
            "natural_convection": "buoyantSimpleFoam",
            "forced_convection": "buoyantSimpleFoam"
        },
        "transient": {
            "natural_convection": "buoyantPimpleFoam",
            "forced_convection": "buoyantPimpleFoam"
        }
    },
    "multiphase": {
        "transient": {
            "laminar": "interFoam",
            "turbulent": "interFoam"
        }
    }
}


def select_solver(
    flow_type: FlowType,
    time_type: TimeType,
    turbulence: TurbulenceType = TurbulenceType.LAMINAR,
    mach_number: Optional[float] = None,
    has_shocks: bool = False
) -> str:
    """
    Select appropriate OpenFOAM solver based on problem characteristics.

    Args:
        flow_type: Type of flow (incompressible, compressible, heat_transfer)
        time_type: Time dependency (steady or transient)
        turbulence: Turbulence modeling approach
        mach_number: Mach number for compressible flows
        has_shocks: Whether strong shocks are expected (prefer rhoCentralFoam)

    Returns:
        Name of the recommended solver
    """
    # Handle supersonic/shock cases - prefer rhoCentralFoam for shock capturing
    if flow_type == FlowType.COMPRESSIBLE:
        if has_shocks or (mach_number and mach_number > 1.0):
            return "rhoCentralFoam"
        elif mach_number and mach_number > 0.8:
            return "rhoCentralFoam"  # Transonic also benefits from rhoCentralFoam

    rules = SOLVER_SELECTION_RULES.get(flow_type.value, {})
    time_rules = rules.get(time_type.value, {})

    if isinstance(time_rules, str):
        return time_rules

    turbulence_key = "turbulent" if turbulence != TurbulenceType.LAMINAR else "laminar"
    return time_rules.get(turbulence_key, "simpleFoam")


def estimate_reynolds_number(
    velocity: float,
    length_scale: float,
    kinematic_viscosity: float = 1e-6  # Water at 20°C
) -> float:
    """
    Estimate Reynolds number.

    Args:
        velocity: Characteristic velocity (m/s)
        length_scale: Characteristic length (m)
        kinematic_viscosity: Kinematic viscosity (m²/s)

    Returns:
        Reynolds number
    """
    return velocity * length_scale / kinematic_viscosity


def recommend_turbulence_model(reynolds_number: float) -> TurbulenceType:
    """
    Recommend turbulence modeling approach based on Reynolds number.

    Args:
        reynolds_number: Flow Reynolds number

    Returns:
        Recommended turbulence type
    """
    if reynolds_number < 2300:
        return TurbulenceType.LAMINAR
    elif reynolds_number < 10000:
        # Transitional - use RANS for safety
        return TurbulenceType.RANS
    else:
        return TurbulenceType.RANS


def get_solver_info(solver_name: str) -> Optional[SolverInfo]:
    """Get detailed information about a solver."""
    return SOLVERS.get(solver_name)


def calculate_yplus_first_layer(
    velocity: float,
    length_scale: float,
    kinematic_viscosity: float,
    target_yplus: float = 30.0
) -> Dict[str, float]:
    """Calculate first cell layer height for target y+ value.

    Uses flat plate turbulent boundary layer correlations.

    Args:
        velocity: Free-stream or bulk velocity [m/s]
        length_scale: Characteristic length (diameter, chord, etc.) [m]
        kinematic_viscosity: Kinematic viscosity [m^2/s]
        target_yplus: Target y+ value (30-300 for wall functions, <1 for low-Re)

    Returns:
        Dictionary with y+ analysis results
    """
    import math

    Re = velocity * length_scale / kinematic_viscosity

    # Skin friction coefficient (Schlichting correlation for turbulent flat plate)
    if Re > 0:
        Cf = 0.058 * Re**(-0.2)
    else:
        return {"error": "Reynolds number must be positive"}

    # Wall shear stress (normalized): tau_w = 0.5 * Cf * rho * U^2
    # Friction velocity: u_tau = sqrt(tau_w / rho) = U * sqrt(0.5 * Cf)
    u_tau = velocity * math.sqrt(0.5 * Cf)

    # First cell height: y = y+ * nu / u_tau
    if u_tau > 0:
        first_cell_height = target_yplus * kinematic_viscosity / u_tau
    else:
        first_cell_height = 0.001  # fallback

    # Recommended y+ ranges for different turbulence approaches
    recommended = {}
    if target_yplus >= 30:
        recommended["approach"] = "Wall functions (standard)"
        recommended["yplus_range"] = "30 - 300"
        recommended["note"] = "Suitable for k-epsilon, k-omega SST with wall functions"
    elif target_yplus >= 1:
        recommended["approach"] = "Low-Reynolds / transitional"
        recommended["yplus_range"] = "1 - 5"
        recommended["note"] = "Better accuracy near walls, more cells needed"
    else:
        recommended["approach"] = "DNS / Resolved boundary layer"
        recommended["yplus_range"] = "< 1"
        recommended["note"] = "Full boundary layer resolution, very fine mesh"

    return {
        "reynolds_number": Re,
        "skin_friction_Cf": Cf,
        "friction_velocity_u_tau": u_tau,
        "target_yplus": target_yplus,
        "first_cell_height_m": first_cell_height,
        "first_cell_height_mm": first_cell_height * 1000,
        "recommended_approach": recommended["approach"],
        "recommended_yplus_range": recommended["yplus_range"],
        "note": recommended["note"],
    }
