"""OpenFOAM Knowledge Base - Boundary condition mappings."""

from typing import Dict, Any, Optional, List
from dataclasses import dataclass, field
from enum import Enum


class BoundaryType(str, Enum):
    """Physical boundary types."""
    INLET = "inlet"
    OUTLET = "outlet"
    WALL = "wall"
    SYMMETRY = "symmetry"
    CYCLIC = "cyclic"
    EMPTY = "empty"  # For 2D cases


class InletType(str, Enum):
    """Types of inlet boundary conditions."""
    VELOCITY = "velocity"
    PRESSURE = "pressure"
    MASS_FLOW = "mass_flow"


class OutletType(str, Enum):
    """Types of outlet boundary conditions."""
    PRESSURE = "pressure"
    OUTFLOW = "outflow"
    CONVECTIVE = "convective"


class WallType(str, Enum):
    """Types of wall boundary conditions."""
    NO_SLIP = "no_slip"
    SLIP = "slip"
    MOVING = "moving"


@dataclass
class BoundaryCondition:
    """Represents a boundary condition for a field."""
    bc_type: str
    value: Optional[Any] = None
    extra_params: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to OpenFOAM dictionary format."""
        result = {"type": self.bc_type}
        if self.value is not None:
            result["value"] = self.value
        result.update(self.extra_params)
        return result


# Boundary condition mappings for different fields
BC_MAPPINGS: Dict[str, Dict[str, Dict[str, BoundaryCondition]]] = {
    "inlet": {
        "velocity": {
            "U": BoundaryCondition("fixedValue", "uniform (1 0 0)"),
            "p": BoundaryCondition("zeroGradient"),
            "k": BoundaryCondition("fixedValue", "uniform 0.1"),
            "epsilon": BoundaryCondition("fixedValue", "uniform 0.1"),
            "omega": BoundaryCondition("fixedValue", "uniform 1"),
            "nut": BoundaryCondition("calculated", "uniform 0"),
        },
        "pressure": {
            "U": BoundaryCondition("pressureInletVelocity", "uniform (0 0 0)"),
            "p": BoundaryCondition("fixedValue", "uniform 0"),
            "k": BoundaryCondition("fixedValue", "uniform 0.1"),
            "epsilon": BoundaryCondition("fixedValue", "uniform 0.1"),
            "omega": BoundaryCondition("fixedValue", "uniform 1"),
            "nut": BoundaryCondition("calculated", "uniform 0"),
        },
        "mass_flow": {
            "U": BoundaryCondition("flowRateInletVelocity", None, {
                "massFlowRate": "constant 0.1",
                "rhoInlet": 1000
            }),
            "p": BoundaryCondition("zeroGradient"),
            "k": BoundaryCondition("fixedValue", "uniform 0.1"),
            "epsilon": BoundaryCondition("fixedValue", "uniform 0.1"),
            "omega": BoundaryCondition("fixedValue", "uniform 1"),
            "nut": BoundaryCondition("calculated", "uniform 0"),
        },
    },
    "outlet": {
        "pressure": {
            "U": BoundaryCondition("zeroGradient"),
            "p": BoundaryCondition("fixedValue", "uniform 0"),
            "k": BoundaryCondition("zeroGradient"),
            "epsilon": BoundaryCondition("zeroGradient"),
            "omega": BoundaryCondition("zeroGradient"),
            "nut": BoundaryCondition("calculated", "uniform 0"),
        },
        "outflow": {
            "U": BoundaryCondition("zeroGradient"),
            "p": BoundaryCondition("zeroGradient"),
            "k": BoundaryCondition("zeroGradient"),
            "epsilon": BoundaryCondition("zeroGradient"),
            "omega": BoundaryCondition("zeroGradient"),
            "nut": BoundaryCondition("calculated", "uniform 0"),
        },
        "convective": {
            "U": BoundaryCondition("inletOutlet", "uniform (0 0 0)", {"inletValue": "uniform (0 0 0)"}),
            "p": BoundaryCondition("fixedValue", "uniform 0"),
            "k": BoundaryCondition("inletOutlet", "uniform 0.1", {"inletValue": "uniform 0.1"}),
            "epsilon": BoundaryCondition("inletOutlet", "uniform 0.1", {"inletValue": "uniform 0.1"}),
            "omega": BoundaryCondition("inletOutlet", "uniform 1", {"inletValue": "uniform 1"}),
            "nut": BoundaryCondition("calculated", "uniform 0"),
        },
    },
    "wall": {
        "no_slip": {
            "U": BoundaryCondition("noSlip"),
            "p": BoundaryCondition("zeroGradient"),
            "k": BoundaryCondition("kqRWallFunction", "$internalField"),
            "epsilon": BoundaryCondition("epsilonWallFunction", "$internalField"),
            "omega": BoundaryCondition("omegaWallFunction", "$internalField"),
            "nut": BoundaryCondition("nutkWallFunction", "uniform 0"),
        },
        "slip": {
            "U": BoundaryCondition("slip"),
            "p": BoundaryCondition("zeroGradient"),
            "k": BoundaryCondition("zeroGradient"),
            "epsilon": BoundaryCondition("zeroGradient"),
            "omega": BoundaryCondition("zeroGradient"),
            "nut": BoundaryCondition("calculated", "uniform 0"),
        },
        "moving": {
            "U": BoundaryCondition("fixedValue", "uniform (1 0 0)"),
            "p": BoundaryCondition("zeroGradient"),
            "k": BoundaryCondition("kqRWallFunction", "$internalField"),
            "epsilon": BoundaryCondition("epsilonWallFunction", "$internalField"),
            "omega": BoundaryCondition("omegaWallFunction", "$internalField"),
            "nut": BoundaryCondition("nutkWallFunction", "uniform 0"),
        },
    },
    "symmetry": {
        "default": {
            "U": BoundaryCondition("symmetry"),
            "p": BoundaryCondition("symmetry"),
            "k": BoundaryCondition("symmetry"),
            "epsilon": BoundaryCondition("symmetry"),
            "omega": BoundaryCondition("symmetry"),
            "nut": BoundaryCondition("symmetry"),
        },
    },
    "empty": {
        "default": {
            "U": BoundaryCondition("empty"),
            "p": BoundaryCondition("empty"),
            "k": BoundaryCondition("empty"),
            "epsilon": BoundaryCondition("empty"),
            "omega": BoundaryCondition("empty"),
            "nut": BoundaryCondition("empty"),
        },
    },
}


@dataclass
class BoundaryDefinition:
    """Complete definition of a boundary."""
    name: str
    physical_type: BoundaryType
    subtype: str = "default"
    parameters: Dict[str, Any] = field(default_factory=dict)


def get_boundary_conditions(
    boundary: BoundaryDefinition,
    fields: List[str],
    is_turbulent: bool = False
) -> Dict[str, Dict[str, Any]]:
    """
    Get boundary conditions for all specified fields.

    Args:
        boundary: Boundary definition
        fields: List of field names (e.g., ["U", "p"])
        is_turbulent: Whether the simulation uses turbulence modeling

    Returns:
        Dictionary mapping field names to their boundary conditions
    """
    result = {}
    bc_type = boundary.physical_type.value
    subtype = boundary.subtype

    if bc_type not in BC_MAPPINGS:
        raise ValueError(f"Unknown boundary type: {bc_type}")

    type_mappings = BC_MAPPINGS[bc_type]
    if subtype not in type_mappings:
        subtype = "default" if "default" in type_mappings else list(type_mappings.keys())[0]

    bc_mapping = type_mappings[subtype]

    for field_name in fields:
        if field_name in bc_mapping:
            bc = bc_mapping[field_name]
            result[field_name] = bc.to_dict()

            # Apply custom parameters
            if field_name in boundary.parameters:
                if "value" in boundary.parameters[field_name]:
                    result[field_name]["value"] = boundary.parameters[field_name]["value"]

    return result


def format_vector(x: float, y: float, z: float) -> str:
    """Format a 3D vector for OpenFOAM."""
    return f"uniform ({x} {y} {z})"


def format_scalar(value: float) -> str:
    """Format a scalar value for OpenFOAM."""
    return f"uniform {value}"


# Common fluid properties
FLUID_PROPERTIES = {
    "water": {
        "name": "水",
        "rho": 1000,      # kg/m³
        "nu": 1e-6,       # m²/s (kinematic viscosity)
        "mu": 1e-3,       # Pa·s (dynamic viscosity)
        "Cp": 4182,       # J/(kg·K)
        "k": 0.6,         # W/(m·K) (thermal conductivity)
        "Pr": 7.0,        # Prandtl number
    },
    "air": {
        "name": "空气",
        "rho": 1.225,     # kg/m³
        "nu": 1.5e-5,     # m²/s
        "mu": 1.8e-5,     # Pa·s
        "Cp": 1005,       # J/(kg·K)
        "k": 0.026,       # W/(m·K)
        "Pr": 0.71,       # Prandtl number
    },
    "oil": {
        "name": "机油",
        "rho": 880,       # kg/m³
        "nu": 1e-4,       # m²/s
        "mu": 0.088,      # Pa·s
        "Cp": 2000,       # J/(kg·K)
        "k": 0.15,        # W/(m·K)
        "Pr": 1170,       # Prandtl number
    },
}


def get_fluid_properties(fluid_name: str) -> Optional[Dict[str, Any]]:
    """Get properties of a common fluid."""
    return FLUID_PROPERTIES.get(fluid_name.lower())
