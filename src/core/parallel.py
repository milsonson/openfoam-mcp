"""OpenFOAM parallel computing utilities."""

from typing import Optional, Callable
from pathlib import Path
import subprocess
import re

from ..io_utils import atomic_write_text
from .runner import RunResult, RunStatus, SolverRunner


def _upsert_entry(content: str, key: str, value: str) -> str:
    """Insert or update a top-level dictionary entry."""
    pattern = rf"(^\s*{re.escape(key)}\b\s+)([^;]+)(;)"
    if re.search(pattern, content, re.MULTILINE):
        return re.sub(pattern, rf"\g<1>{value}\g<3>", content, count=1, flags=re.MULTILINE)

    marker = "// ************************************************************************* //"
    entry = f"\n{key} {value};\n"
    if marker in content:
        return content.replace(marker, entry + "\n" + marker, 1)
    return content + entry


def _find_named_block_span(content: str, block_name: str) -> Optional[tuple[int, int]]:
    """Return [start, end) span for a named OpenFOAM dictionary block."""
    match = re.search(rf"\b{re.escape(block_name)}\b\s*\{{", content)
    if not match:
        return None

    start = match.start()
    brace_start = content.find("{", match.start())
    if brace_start == -1:
        return None

    depth = 0
    for idx in range(brace_start, len(content)):
        ch = content[idx]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                end = idx + 1
                while end < len(content) and content[end] in " \t\r\n":
                    end += 1
                return start, end
    return None


def _remove_named_block(content: str, block_name: str) -> str:
    """Remove a named dictionary block when present."""
    span = _find_named_block_span(content, block_name)
    if not span:
        return content
    return content[: span[0]] + content[span[1] :]


def _upsert_named_block(content: str, block_name: str, entries: list[str]) -> str:
    """Insert or replace a named dictionary block with provided entries."""
    block_lines = [block_name, "{"] + [f"    {entry}" for entry in entries] + ["}", ""]
    block_text = "\n".join(block_lines)

    span = _find_named_block_span(content, block_name)
    if span:
        return content[: span[0]] + block_text + content[span[1] :]

    marker = "// ************************************************************************* //"
    if marker in content:
        return content.replace(marker, block_text + marker, 1)
    return content + ("\n" if not content.endswith("\n") else "") + block_text


def _factorize_processors(n_processors: int) -> tuple[int, int, int]:
    """Map processor count to a near-balanced 3D decomposition.

    The function greedily distributes prime factors to the smallest axis so that
    the resulting `(n_x, n_y, n_z)` values remain as balanced as possible while
    guaranteeing `n_x * n_y * n_z == n_processors`.
    """
    if n_processors < 1:
        raise ValueError("n_processors 必须 >= 1")

    factors = [1, 1, 1]
    remaining = n_processors
    divisor = 2

    while divisor * divisor <= remaining:
        while remaining % divisor == 0:
            idx = factors.index(min(factors))
            factors[idx] *= divisor
            remaining //= divisor
        divisor += 1

    if remaining > 1:
        idx = factors.index(min(factors))
        factors[idx] *= remaining

    factors.sort(reverse=True)
    return factors[0], factors[1], factors[2]


def decompose_case(
    case_path: str,
    n_processors: int = 4,
    method: str = "scotch",
    timeout: float = 300,
    force: bool = True,
) -> RunResult:
    """
    Decompose a case for parallel execution.

    Args:
        case_path: Path to the case directory
        n_processors: Number of processors
        method: Decomposition method (simple, hierarchical, scotch)
        timeout: Maximum execution time in seconds
        force: Whether to pass -force to decomposePar

    Returns:
        RunResult with execution details
    """
    # Ensure decomposeParDict exists and matches requested processor count.
    decompose_dict = Path(case_path) / "system" / "decomposeParDict"
    if decompose_dict.exists():
        content = decompose_dict.read_text(encoding="utf-8", errors="replace")
        content = _upsert_entry(content, "numberOfSubdomains", str(n_processors))
        content = _upsert_entry(content, "method", method)

        if method == "scotch":
            weights = " ".join(["1"] * n_processors)
            content = _remove_named_block(content, "simpleCoeffs")
            content = _remove_named_block(content, "hierarchicalCoeffs")
            content = _upsert_named_block(
                content,
                "scotchCoeffs",
                [f"processorWeights ({weights});"],
            )
        atomic_write_text(decompose_dict, content)
    else:
        content = _generate_decompose_par_dict_content(n_processors, method)
        atomic_write_text(decompose_dict, content)

    runner = SolverRunner(case_path)
    args = ["-force"] if force else []
    return runner.run_command("decomposePar", args=args, timeout=timeout)


def reconstruct_case(
    case_path: str,
    latest_time: bool = False,
    timeout: float = 600
) -> RunResult:
    """
    Reconstruct a parallel case.

    Args:
        case_path: Path to the case directory
        latest_time: Only reconstruct the latest time
        timeout: Maximum execution time in seconds

    Returns:
        RunResult with execution details
    """
    args = []
    if latest_time:
        args.append("-latestTime")

    runner = SolverRunner(case_path)
    return runner.run_command("reconstructPar", args=args, timeout=timeout)


def run_parallel(
    case_path: str,
    solver: str,
    n_processors: int = 4,
    hostfile: Optional[str] = None,
    timeout: Optional[float] = None,
    on_progress: Optional[Callable] = None
) -> RunResult:
    """
    Run an OpenFOAM solver in parallel using mpirun.

    Args:
        case_path: Path to the case directory
        solver: Solver name (e.g., "simpleFoam")
        n_processors: Number of processors
        hostfile: Optional path to MPI hostfile
        timeout: Maximum execution time in seconds
        on_progress: Callback for progress updates

    Returns:
        RunResult with execution details
    """
    # Build mpirun command
    args = ["-np", str(n_processors)]

    if hostfile:
        args.extend(["-hostfile", hostfile])

    args.append(solver)
    args.append("-parallel")

    # Use mpirun to run the solver
    runner = SolverRunner(case_path)
    return runner.run_command("mpirun", args=args, timeout=timeout, on_progress=on_progress)


def _generate_decompose_par_dict_content(n_processors: int = 4, method: str = "scotch") -> str:
    """Generate decomposeParDict file content.

    Args:
        n_processors: Number of processors
        method: Decomposition method (simple, hierarchical, scotch)

    Returns:
        Content of decomposeParDict file
    """
    header = '''/*--------------------------------*- C++ -*----------------------------------*\\
| =========                 |                                                 |
| \\\\      /  F ield         | OpenFOAM: The Open Source CFD Toolbox           |
|  \\\\    /   O peration     | Version:  v2312                                 |
|   \\\\  /    A nd           | Website:  www.openfoam.com                      |
|    \\\\/     M anipulation  |                                                 |
\\*---------------------------------------------------------------------------*/
FoamFile
{
    version     2.0;
    format      ascii;
    class       dictionary;
    object      decomposeParDict;
}
// * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * //
'''

    content = header + f'''
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
