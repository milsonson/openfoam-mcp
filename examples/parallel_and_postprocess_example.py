"""
Example usage of parallel computing and post-processing features.

This script demonstrates:
1. How to enable parallel decomposition in case generation
2. How to run parallel simulations
3. How to parse residuals and generate plots
4. How to extract and visualize force coefficients
"""

from pathlib import Path
from src.core import (
    # Case generation
    create_case_config_from_template,
    OpenFOAMGenerator,

    # Parallel computing
    decompose_case,
    run_parallel,
    reconstruct_case,

    # Post-processing
    parse_log_file,
    generate_residual_plot,
    extract_force_coefficients,
    generate_force_coefficients_plot,
)


def example_parallel_case_generation():
    """Example: Generate a case with parallel support."""

    # Create case configuration
    config = create_case_config_from_template(
        template_id="pipe_flow",
        parameters={
            "diameter": 0.1,
            "length": 1.0,
            "inlet_velocity": 1.0,
            "fluid": "water",
            "mesh_density": "medium",
        },
        case_path="/tmp/pipe_parallel"
    )

    # Generate case with parallel support
    generator = OpenFOAMGenerator(config)

    # Include parallel decomposition with 4 processors using scotch method
    files = generator.generate_all(
        include_parallel=True,
        n_processors=4,
        decomposition_method="scotch"
    )

    # Write case to disk
    created_files = generator.write_case()
    print(f"Created {len(created_files)} files")
    print("Parallel decomposition dictionary included: system/decomposeParDict")


def example_parallel_workflow():
    """Example: Complete parallel workflow."""

    case_path = "/tmp/pipe_parallel"

    # Step 1: Decompose the case
    print("Step 1: Decomposing case for parallel execution...")
    result = decompose_case(
        case_path=case_path,
        n_processors=4,
        method="scotch"
    )

    if result.status.value == "completed":
        print("  Decomposition successful")
    else:
        print(f"  Decomposition failed: {result.error_message}")
        return

    # Step 2: Run solver in parallel
    print("\nStep 2: Running solver in parallel...")
    result = run_parallel(
        case_path=case_path,
        solver="simpleFoam",
        n_processors=4,
        timeout=3600
    )

    if result.status.value == "completed":
        print("  Solver completed successfully")
    else:
        print(f"  Solver failed: {result.error_message}")
        return

    # Step 3: Reconstruct the case
    print("\nStep 3: Reconstructing case...")
    result = reconstruct_case(
        case_path=case_path,
        latest_time=False
    )

    if result.status.value == "completed":
        print("  Reconstruction successful")
    else:
        print(f"  Reconstruction failed: {result.error_message}")


def example_residual_plotting():
    """Example: Parse log file and generate residual plots."""

    case_path = "/tmp/pipe_parallel"
    log_file = Path(case_path) / "log.simpleFoam"

    if not log_file.exists():
        print(f"Log file not found: {log_file}")
        print("Run the solver first to generate logs")
        return

    # Parse residuals from log file
    print("Parsing residuals from log file...")
    residuals = parse_log_file(str(log_file))

    print(f"Found residual data for fields: {list(residuals.keys())}")

    # Generate residual plot
    output_plot = Path(case_path) / "residuals.png"
    plot_path = generate_residual_plot(
        residuals=residuals,
        output_path=str(output_plot),
        plot_type="final",
        log_scale=True,
        title="Convergence History - simpleFoam"
    )

    print(f"Residual plot saved to: {plot_path}")


def example_force_coefficients():
    """Example: Extract and plot force coefficients."""

    case_path = "/tmp/cylinder_flow"

    # Extract force coefficients
    print("Extracting force coefficients...")
    coeffs = extract_force_coefficients(
        case_path=case_path,
        function_object_name="forces"
    )

    if coeffs is None:
        print("No force coefficient data found")
        print("Make sure to include forceCoeffs function object in controlDict")
        return

    print(f"Found {len(coeffs.times)} time steps")
    print(f"Average Cd: {sum(coeffs.cd) / len(coeffs.cd):.4f}")
    print(f"Average Cl: {sum(coeffs.cl) / len(coeffs.cl):.4f}")

    # Generate plot
    output_plot = Path(case_path) / "force_coefficients.png"
    plot_path = generate_force_coefficients_plot(
        coeffs=coeffs,
        output_path=str(output_plot),
        title="Force Coefficients - Cylinder Flow"
    )

    print(f"Force coefficients plot saved to: {plot_path}")


def example_simple_decomposition_methods():
    """Example: Demonstrate different decomposition methods."""

    case_path = "/tmp/test_case"

    # Scotch method (recommended for most cases)
    print("1. Scotch decomposition (load-balanced):")
    decompose_case(case_path, n_processors=4, method="scotch")

    # Simple method (geometric decomposition)
    print("\n2. Simple decomposition (geometric):")
    decompose_case(case_path, n_processors=8, method="simple")

    # Hierarchical method (ordered geometric)
    print("\n3. Hierarchical decomposition (ordered geometric):")
    decompose_case(case_path, n_processors=16, method="hierarchical")


if __name__ == "__main__":
    print("=" * 60)
    print("OpenFOAM Parallel Computing and Post-Processing Examples")
    print("=" * 60)

    print("\n--- Example 1: Generate Case with Parallel Support ---")
    example_parallel_case_generation()

    print("\n--- Example 2: Complete Parallel Workflow ---")
    print("(Requires OpenFOAM environment and valid case)")
    # Uncomment to run:
    # example_parallel_workflow()

    print("\n--- Example 3: Residual Plotting ---")
    print("(Requires completed simulation with log file)")
    # Uncomment to run:
    # example_residual_plotting()

    print("\n--- Example 4: Force Coefficients ---")
    print("(Requires case with force function object)")
    # Uncomment to run:
    # example_force_coefficients()
