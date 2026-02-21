"""Core module for OpenFOAM MCP."""

from .generator import (
    CaseConfig,
    OpenFOAMGenerator,
    create_case_config_from_template,
)

from .validator import (
    ValidationResult,
    ValidationReport,
    CaseValidator,
    validate_case,
    parse_solver_log_errors,
    OPENFOAM_ERROR_PATTERNS,
)

from .runner import (
    RunStatus,
    RunProgress,
    RunResult,
    SolverRunner,
    run_solver,
    run_mesh_generation,
    run_mesh_check,
)

from .parallel import (
    decompose_case,
    reconstruct_case,
    run_parallel,
)

from .postprocess import (
    ResidualData,
    ForceCoefficients,
    parse_log_file,
    generate_residual_plot,
    extract_force_coefficients,
    generate_force_coefficients_plot,
    extract_probe_data,
)

__all__ = [
    # Generator
    "CaseConfig",
    "OpenFOAMGenerator",
    "create_case_config_from_template",
    # Validator
    "ValidationResult",
    "ValidationReport",
    "CaseValidator",
    "validate_case",
    "parse_solver_log_errors",
    "OPENFOAM_ERROR_PATTERNS",
    # Runner
    "RunStatus",
    "RunProgress",
    "RunResult",
    "SolverRunner",
    "run_solver",
    "run_mesh_generation",
    "run_mesh_check",
    # Parallel
    "decompose_case",
    "reconstruct_case",
    "run_parallel",
    # Postprocess
    "ResidualData",
    "ForceCoefficients",
    "parse_log_file",
    "generate_residual_plot",
    "extract_force_coefficients",
    "generate_force_coefficients_plot",
    "extract_probe_data",
]
