#!/usr/bin/env python3
"""Integration tests for OpenFOAM MCP Server."""

import sys
import shutil
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent))


def test_module_imports():
    """Test that all modules can be imported."""
    print("Testing module imports...")
    
    from src.knowledge.solvers import (
        FlowType, TimeType, TurbulenceType,
        select_solver, estimate_reynolds_number, recommend_turbulence_model
    )
    from src.knowledge.boundaries import (
        BoundaryType, BoundaryDefinition, get_boundary_conditions, get_fluid_properties
    )
    from src.knowledge.mesh_strategies import (
        BlockMeshParams, create_pipe_mesh_params, MeshStrategy
    )
    from src.templates.registry import list_templates, get_template
    from src.core.generator import CaseConfig, OpenFOAMGenerator
    from src.core.postprocess import ResidualData, ForceCoefficients
    from src.core.parallel import decompose_case, run_parallel, reconstruct_case
    
    print("✅ All modules imported successfully")


def test_template_registry():
    """Test template registry functionality."""
    print("\nTesting template registry...")

    from src.templates.registry import list_templates, get_template

    templates = list_templates()
    assert len(templates) == 13, f"Expected 13 templates, got {len(templates)}"

    template_ids = [t.id for t in templates]
    expected_ids = [
        'pipe_flow', 'cavity_flow', 'cylinder_flow', 'natural_convection',
        'dam_break', 'bubble_rising', 'backward_step', 'channel_flow', 'heat_exchanger',
        'flat_plate', 'mixing_elbow', 'shock_tube', 'supersonic_nozzle'
    ]
    for tid in expected_ids:
        assert tid in template_ids, f"Missing template: {tid}"

    # Test get_template
    pipe = get_template('pipe_flow')
    assert pipe is not None
    assert pipe.solver == 'simpleFoam'

    # Test multiphase template
    dam = get_template('dam_break')
    assert dam is not None
    assert dam.solver == 'interFoam'
    assert dam.category == 'multiphase'

    # Test compressible template
    shock = get_template('shock_tube')
    assert shock is not None
    assert shock.solver == 'rhoCentralFoam'
    assert shock.category == 'compressible'

    print(f"✅ Template registry: {len(templates)} templates verified")


def test_solver_selection():
    """Test solver selection logic."""
    print("\nTesting solver selection...")
    
    from src.knowledge.solvers import (
        FlowType, TimeType, TurbulenceType,
        select_solver, estimate_reynolds_number, recommend_turbulence_model
    )
    
    # Test Reynolds estimation
    re = estimate_reynolds_number(velocity=1.0, length_scale=0.05, kinematic_viscosity=1e-6)
    assert abs(re - 50000) < 0.01, f"Expected Re≈50000, got {re}"
    
    # Test turbulence recommendation
    turb = recommend_turbulence_model(re)
    assert turb == TurbulenceType.RANS
    
    # Test solver selection
    solver = select_solver(FlowType.INCOMPRESSIBLE, TimeType.STEADY, TurbulenceType.RANS)
    assert solver == 'simpleFoam'
    
    print(f"✅ Solver selection: Re={re}, turbulence={turb.value}, solver={solver}")


def test_case_generation():
    """Test case file generation."""
    print("\nTesting case generation...")
    
    from src.core.generator import CaseConfig, OpenFOAMGenerator
    from src.knowledge.solvers import FlowType, TimeType, TurbulenceType
    from src.knowledge.mesh_strategies import BlockMeshParams
    from src.knowledge.boundaries import BoundaryDefinition, BoundaryType
    
    case_path = Path('/tmp/openfoam_mcp_test')
    
    # Clean up
    if case_path.exists():
        shutil.rmtree(case_path)
    
    # Create config
    config = CaseConfig(
        case_path=case_path,
        solver='simpleFoam',
        flow_type=FlowType.INCOMPRESSIBLE,
        time_type=TimeType.STEADY,
        turbulence_type=TurbulenceType.RANS,
        is_2d=False,
        fluid_properties={'rho': 1000, 'nu': 1e-6},
        geometry_params={'diameter': 0.05, 'length': 0.5, 'inlet_velocity': 1.0},
        boundary_definitions=[
            BoundaryDefinition(name='inlet', physical_type=BoundaryType.INLET,
                               subtype='velocity', parameters={'velocity': (1.0, 0, 0)}),
            BoundaryDefinition(name='outlet', physical_type=BoundaryType.OUTLET,
                               subtype='pressure', parameters={'pressure': 0}),
            BoundaryDefinition(name='walls', physical_type=BoundaryType.WALL,
                               subtype='no_slip', parameters={})
        ],
        mesh_params=BlockMeshParams(
            x_min=0, x_max=0.5, y_min=-0.025, y_max=0.025, z_min=-0.025, z_max=0.025,
            n_cells_x=50, n_cells_y=20, n_cells_z=20,
            grading_x=1.0, grading_y=1.0, grading_z=1.0,
            patches={
                'inlet': {'type': 'patch', 'faces': ['x_min']},
                'outlet': {'type': 'patch', 'faces': ['x_max']},
                'walls': {'type': 'wall', 'faces': ['y_min', 'y_max', 'z_min', 'z_max']}
            }
        ),
        control_params={'deltaT': 1, 'endTime': 1000, 'writeInterval': 100},
        reynolds_number=50000
    )
    
    # Generate files
    generator = OpenFOAMGenerator(config)
    files = generator.generate_all()
    
    # Write to disk
    case_path.mkdir(parents=True, exist_ok=True)
    for rel_path, content in files.items():
        file_path = case_path / rel_path
        file_path.parent.mkdir(parents=True, exist_ok=True)
        with open(file_path, 'w') as f:
            f.write(content)
    
    # Verify
    assert (case_path / '0' / 'U').exists(), "Missing 0/U"
    assert (case_path / '0' / 'p').exists(), "Missing 0/p"
    assert (case_path / 'system' / 'blockMeshDict').exists(), "Missing blockMeshDict"
    assert (case_path / 'system' / 'controlDict').exists(), "Missing controlDict"
    
    # Verify hex block has semicolon
    with open(case_path / 'system' / 'blockMeshDict') as f:
        content = f.read()
    assert 'hex (0 1 2 3 4 5 6 7)' in content
    assert ';' in content.split('hex')[1][:100], "Missing semicolon in hex block"
    
    print(f"✅ Case generation: {len(files)} files created at {case_path}")


def test_postprocess_data_structures():
    """Test postprocessing data structures."""
    print("\nTesting postprocessing data structures...")
    
    from src.core.postprocess import ResidualData, ForceCoefficients
    
    # Test ResidualData
    residual = ResidualData(
        field_name='Ux',
        iterations=[1, 2, 3],
        times=[1.0, 2.0, 3.0],
        initial_residuals=[0.1, 0.05, 0.01],
        final_residuals=[0.05, 0.01, 0.001]
    )
    assert residual.field_name == 'Ux'
    assert len(residual.iterations) == 3
    
    # Test ForceCoefficients
    forces = ForceCoefficients(
        times=[1.0, 2.0],
        cd=[0.5, 0.45],
        cl=[0.1, 0.08],
        cm=[0.01, 0.009]
    )
    assert forces.cd[0] == 0.5
    
    print("✅ Postprocessing data structures verified")


def run_all_tests():
    """Run all integration tests."""
    print("=" * 60)
    print("OpenFOAM MCP Server - Integration Tests")
    print("=" * 60)
    
    tests = [
        test_module_imports,
        test_template_registry,
        test_solver_selection,
        test_case_generation,
        test_postprocess_data_structures,
    ]
    
    passed = 0
    failed = 0
    
    for test in tests:
        try:
            test()
            passed += 1
        except Exception as e:
            failed += 1
            print(f"❌ {test.__name__} failed: {e}")
    
    print("\n" + "=" * 60)
    print(f"Results: {passed} passed, {failed} failed")
    print("=" * 60)
    
    return failed == 0


if __name__ == '__main__':
    success = run_all_tests()
    sys.exit(0 if success else 1)
