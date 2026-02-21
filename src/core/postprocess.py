"""OpenFOAM post-processing utilities."""

from typing import Dict, List, Tuple, Optional
from pathlib import Path
import re
from dataclasses import dataclass, field
from datetime import datetime

_TIME_RE = re.compile(r"Time\s*=\s*([\d.e+-]+)")
_ITER_RE = re.compile(r"Iteration[:\s]+(\d+)", re.IGNORECASE)
_RESIDUAL_RE = re.compile(
    r"Solving for (\w+),\s*"
    r"Initial residual\s*=\s*([\d.e+-]+).*?"
    r"Final residual\s*=\s*([\d.e+-]+)"
)


@dataclass
class ResidualData:
    """Residual data extracted from OpenFOAM log files."""
    field_name: str
    iterations: List[int] = field(default_factory=list)
    times: List[float] = field(default_factory=list)
    initial_residuals: List[float] = field(default_factory=list)
    final_residuals: List[float] = field(default_factory=list)


@dataclass
class ForceCoefficients:
    """Force coefficients data."""
    times: List[float] = field(default_factory=list)
    cd: List[float] = field(default_factory=list)  # Drag coefficient
    cl: List[float] = field(default_factory=list)  # Lift coefficient
    cm: List[float] = field(default_factory=list)  # Moment coefficient


def parse_log_file(log_file_path: str) -> Dict[str, ResidualData]:
    """
    Parse OpenFOAM log file and extract residual data.

    Args:
        log_file_path: Path to the log file (or stdout from solver)

    Returns:
        Dictionary mapping field names to ResidualData objects
    """
    residuals: Dict[str, ResidualData] = {}
    current_time = 0.0
    current_iteration = 0

    log_path = Path(log_file_path)
    if not log_path.exists():
        raise FileNotFoundError(f"Log file not found: {log_file_path}")

    with open(log_path, 'r', encoding='utf-8', errors='replace') as f:
        for line in f:
            # Parse time
            time_match = _TIME_RE.search(line)
            if time_match:
                current_time = float(time_match.group(1))

            # Parse iteration (for steady-state solvers)
            iter_match = _ITER_RE.search(line)
            if iter_match:
                current_iteration = int(iter_match.group(1))

            # Parse residuals
            # Format: "Solving for <field>, Initial residual = <value>, Final residual = <value>, No Iterations <n>"
            residual_match = _RESIDUAL_RE.search(line)

            if residual_match:
                field_name = residual_match.group(1)
                initial_res = float(residual_match.group(2))
                final_res = float(residual_match.group(3))

                # Initialize field data if not seen before
                if field_name not in residuals:
                    residuals[field_name] = ResidualData(field_name=field_name)

                # Append data
                residuals[field_name].times.append(current_time)
                residuals[field_name].iterations.append(current_iteration)
                residuals[field_name].initial_residuals.append(initial_res)
                residuals[field_name].final_residuals.append(final_res)

    return residuals


def generate_residual_plot(
    residuals: Dict[str, ResidualData],
    output_path: str,
    plot_type: str = "final",
    log_scale: bool = True,
    title: str = "Residuals"
) -> str:
    """
    Generate residual plot using matplotlib.

    Args:
        residuals: Dictionary of residual data from parse_log_file()
        output_path: Path to save the plot (PNG file)
        plot_type: Type of residuals to plot ("final" or "initial")
        log_scale: Use logarithmic scale for y-axis
        title: Plot title

    Returns:
        Path to the generated plot file
    """
    try:
        import matplotlib
        matplotlib.use('Agg')  # Non-interactive backend
        import matplotlib.pyplot as plt
    except ImportError:
        raise ImportError(
            "matplotlib is required for plotting. "
            "Install it with: pip install matplotlib"
        )

    fig, ax = plt.subplots(figsize=(10, 6))

    # Plot residuals for each field
    for field_name, data in residuals.items():
        if plot_type == "final":
            values = data.final_residuals
        else:
            values = data.initial_residuals

        # Use iterations if available, otherwise use times
        if data.iterations and max(data.iterations) > 0:
            x_values = data.iterations
            x_label = "Iteration"
        else:
            x_values = data.times
            x_label = "Time (s)"

        ax.plot(x_values, values, marker='o', markersize=3,
                label=field_name, linewidth=1.5)

    # Configure plot
    ax.set_xlabel(x_label, fontsize=12)
    ax.set_ylabel(f"{plot_type.capitalize()} Residual", fontsize=12)
    ax.set_title(title, fontsize=14, fontweight='bold')
    ax.legend(loc='best', frameon=True, shadow=True)
    ax.grid(True, alpha=0.3, linestyle='--')

    if log_scale:
        ax.set_yscale('log')

    plt.tight_layout()

    # Save plot
    output_file = Path(output_path)
    output_file.parent.mkdir(parents=True, exist_ok=True)
    plt.savefig(output_file, dpi=150, bbox_inches='tight')
    plt.close()

    return str(output_file)


def extract_force_coefficients(
    case_path: str,
    function_object_name: str = "forces"
) -> Optional[ForceCoefficients]:
    """
    Extract force coefficients from postProcessing directory.

    Args:
        case_path: Path to the OpenFOAM case
        function_object_name: Name of the force/forceCoeffs function object

    Returns:
        ForceCoefficients object or None if not found
    """
    case = Path(case_path)

    # Look for force coefficients file
    postproc_dir = case / "postProcessing" / function_object_name

    if not postproc_dir.exists():
        return None

    # Find the latest time directory
    time_dirs = [d for d in postproc_dir.iterdir() if d.is_dir()]
    if not time_dirs:
        return None

    # Sort by name (assuming numeric time values)
    time_dirs.sort(key=lambda x: float(x.name) if x.name.replace('.', '').isdigit() else 0)
    latest_dir = time_dirs[-1]

    # Look for coefficient file
    coeff_file = latest_dir / "coefficient.dat"
    if not coeff_file.exists():
        # Try alternative naming
        coeff_file = latest_dir / "forceCoeffs.dat"
        if not coeff_file.exists():
            return None

    # Parse the file
    coeffs = ForceCoefficients()

    with open(coeff_file, 'r', encoding='utf-8', errors='replace') as f:
        for line in f:
            # Skip comments and empty lines
            if line.startswith('#') or not line.strip():
                continue

            # Parse data line
            # Format: time Cd Cl Cm (Cd_f Cd_p Cl_f Cl_p ...)
            parts = line.split()
            if len(parts) >= 4:
                try:
                    time = float(parts[0])
                    cd = float(parts[1])
                    cl = float(parts[2])
                    cm = float(parts[3])

                    coeffs.times.append(time)
                    coeffs.cd.append(cd)
                    coeffs.cl.append(cl)
                    coeffs.cm.append(cm)
                except (ValueError, IndexError):
                    continue

    return coeffs if coeffs.times else None


def generate_force_coefficients_plot(
    coeffs: ForceCoefficients,
    output_path: str,
    title: str = "Force Coefficients"
) -> str:
    """
    Generate force coefficients plot.

    Args:
        coeffs: ForceCoefficients object
        output_path: Path to save the plot
        title: Plot title

    Returns:
        Path to the generated plot file
    """
    try:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
    except ImportError:
        raise ImportError(
            "matplotlib is required for plotting. "
            "Install it with: pip install matplotlib"
        )

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(10, 8), sharex=True)

    # Plot drag and lift coefficients
    ax1.plot(coeffs.times, coeffs.cd, 'b-', label='Cd (Drag)', linewidth=1.5)
    ax1.plot(coeffs.times, coeffs.cl, 'r-', label='Cl (Lift)', linewidth=1.5)
    ax1.set_ylabel('Force Coefficients', fontsize=12)
    ax1.legend(loc='best', frameon=True, shadow=True)
    ax1.grid(True, alpha=0.3, linestyle='--')
    ax1.set_title(title, fontsize=14, fontweight='bold')

    # Plot moment coefficient
    ax2.plot(coeffs.times, coeffs.cm, 'g-', label='Cm (Moment)', linewidth=1.5)
    ax2.set_xlabel('Time (s)', fontsize=12)
    ax2.set_ylabel('Moment Coefficient', fontsize=12)
    ax2.legend(loc='best', frameon=True, shadow=True)
    ax2.grid(True, alpha=0.3, linestyle='--')

    plt.tight_layout()

    # Save plot
    output_file = Path(output_path)
    output_file.parent.mkdir(parents=True, exist_ok=True)
    plt.savefig(output_file, dpi=150, bbox_inches='tight')
    plt.close()

    return str(output_file)


def extract_probe_data(
    case_path: str,
    probe_name: str = "probes"
) -> Dict[str, List[Tuple[float, List[float]]]]:
    """
    Extract probe data from postProcessing directory.

    Args:
        case_path: Path to the OpenFOAM case
        probe_name: Name of the probe function object

    Returns:
        Dictionary mapping field names to list of (time, values) tuples
    """
    case = Path(case_path)
    probe_dir = case / "postProcessing" / probe_name

    if not probe_dir.exists():
        return {}

    # Find time directories
    time_dirs = [d for d in probe_dir.iterdir() if d.is_dir()]
    if not time_dirs:
        return {}

    time_dirs.sort(key=lambda x: float(x.name) if x.name.replace('.', '').isdigit() else 0)
    latest_dir = time_dirs[-1]

    # Read probe data files
    probe_data: Dict[str, List[Tuple[float, List[float]]]] = {}

    for data_file in latest_dir.glob("*.dat"):
        field_name = data_file.stem
        probe_data[field_name] = []

        with open(data_file, 'r', encoding='utf-8', errors='replace') as f:
            for line in f:
                if line.startswith('#') or not line.strip():
                    continue

                parts = line.split()
                if len(parts) >= 2:
                    try:
                        time = float(parts[0])
                        values = [float(v) for v in parts[1:]]
                        probe_data[field_name].append((time, values))
                    except (ValueError, IndexError):
                        continue

    return probe_data
