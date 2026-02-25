"""OpenFOAM case validation system."""

from typing import Dict, Any, List, Optional, Tuple
from pathlib import Path
from dataclasses import dataclass
import subprocess
import re
import os


@dataclass
class ValidationResult:
    """Result of a validation check."""
    passed: bool
    message: str
    details: Optional[str] = None
    severity: str = "info"  # info, warning, error


@dataclass
class ValidationReport:
    """Complete validation report."""
    case_path: str
    results: List[ValidationResult]

    @property
    def passed(self) -> bool:
        """Check if all validations passed."""
        return all(r.passed for r in self.results if r.severity == "error")

    @property
    def has_warnings(self) -> bool:
        """Check if there are any warnings."""
        return any(r.severity == "warning" and not r.passed for r in self.results)

    def summary(self) -> str:
        """Generate a human-readable summary."""
        lines = [f"验证报告: {self.case_path}", "=" * 50]

        errors = [r for r in self.results if r.severity == "error" and not r.passed]
        warnings = [r for r in self.results if r.severity == "warning" and not r.passed]
        passed = [r for r in self.results if r.passed]

        if self.passed:
            lines.append("✅ 验证通过")
        else:
            lines.append("❌ 验证失败")

        if errors:
            lines.append(f"\n错误 ({len(errors)}):")
            for r in errors:
                lines.append(f"  ❌ {r.message}")
                if r.details:
                    lines.append(f"     {r.details}")

        if warnings:
            lines.append(f"\n警告 ({len(warnings)}):")
            for r in warnings:
                lines.append(f"  ⚠️ {r.message}")
                if r.details:
                    lines.append(f"     {r.details}")

        if passed:
            lines.append(f"\n通过 ({len(passed)}):")
            for r in passed:
                lines.append(f"  ✅ {r.message}")

        return "\n".join(lines)


class CaseValidator:
    """Validates OpenFOAM cases through multiple checks."""

    def __init__(self, case_path: str):
        self.case_path = Path(case_path)
        self.results: List[ValidationResult] = []
        self._cached_solver: Optional[str] = None

    def _detect_solver_from_control_dict(self) -> str:
        """Best-effort solver detection from system/controlDict."""
        if self._cached_solver is not None:
            return self._cached_solver

        solver = ""
        control_file = self.case_path / "system" / "controlDict"
        if control_file.exists():
            try:
                content = control_file.read_text(encoding="utf-8", errors="replace")
                match = re.search(r"\bapplication\s+([^;\s]+)\s*;", content)
                if match:
                    solver = match.group(1).strip()
            except Exception:
                solver = ""

        self._cached_solver = solver
        return solver

    def _is_rho_central_case(self) -> bool:
        """Identify rhoCentralFoam-style compressible cases."""
        solver = self._detect_solver_from_control_dict().lower()
        if solver == "rhocentralfoam":
            return True

        thermo_file = self.case_path / "constant" / "thermophysicalProperties"
        transport_file = self.case_path / "constant" / "transportProperties"
        return thermo_file.exists() and not transport_file.exists()

    def _required_files(self) -> List[str]:
        """Compute required files by solver/type to reduce false positives."""
        common = ["system/controlDict", "system/fvSchemes", "system/fvSolution", "0/U", "0/p"]
        if self._is_rho_central_case():
            return common + ["0/T", "constant/thermophysicalProperties"]
        return common + ["constant/transportProperties"]

    def _syntax_files(self) -> List[str]:
        """Dictionary files that should pass basic syntax checks."""
        files = ["system/controlDict", "system/fvSchemes", "system/fvSolution"]
        if self._is_rho_central_case():
            files.append("constant/thermophysicalProperties")
        else:
            files.append("constant/transportProperties")
        return files

    def validate_all(self, run_openfoam_checks: bool = True) -> ValidationReport:
        """Run all validation checks."""
        self.results = []

        # Syntax and structure checks (Priority 2)
        self._validate_directory_structure()
        self._validate_file_syntax()
        self._validate_boundary_consistency()

        # Physical reasonableness checks (Priority 3)
        self._validate_physical_parameters()

        # OpenFOAM actual validation (Priority 1 - highest)
        if run_openfoam_checks:
            self._run_openfoam_validation()

        return ValidationReport(
            case_path=str(self.case_path),
            results=self.results
        )

    def _validate_directory_structure(self):
        """Check that required directories and files exist."""
        required_dirs = ["0", "constant", "system"]
        required_files = self._required_files()

        for dir_name in required_dirs:
            dir_path = self.case_path / dir_name
            if dir_path.exists():
                self.results.append(ValidationResult(
                    passed=True,
                    message=f"目录 {dir_name}/ 存在"
                ))
            else:
                self.results.append(ValidationResult(
                    passed=False,
                    message=f"缺少必需目录: {dir_name}/",
                    severity="error"
                ))

        for file_name in required_files:
            file_path = self.case_path / file_name
            if file_path.exists():
                self.results.append(ValidationResult(
                    passed=True,
                    message=f"文件 {file_name} 存在"
                ))
            else:
                self.results.append(ValidationResult(
                    passed=False,
                    message=f"缺少必需文件: {file_name}",
                    severity="error"
                ))

    def _validate_file_syntax(self):
        """Validate OpenFOAM dictionary syntax."""
        dict_files = self._syntax_files()

        for file_name in dict_files:
            file_path = self.case_path / file_name
            if not file_path.exists():
                continue

            try:
                content = file_path.read_text(encoding="utf-8", errors="replace")
                errors = self._check_dict_syntax(content)
                if errors:
                    self.results.append(ValidationResult(
                        passed=False,
                        message=f"{file_name} 语法错误",
                        details="; ".join(errors),
                        severity="error"
                    ))
                else:
                    self.results.append(ValidationResult(
                        passed=True,
                        message=f"{file_name} 语法检查通过"
                    ))
            except Exception as e:
                self.results.append(ValidationResult(
                    passed=False,
                    message=f"无法读取 {file_name}",
                    details=str(e),
                    severity="error"
                ))

    def _check_dict_syntax(self, content: str) -> List[str]:
        """Check basic OpenFOAM dictionary syntax."""
        errors = []

        def _check_pairs(open_ch: str, close_ch: str, label: str) -> None:
            depth = 0
            line_no = 1
            for ch in content:
                if ch == "\n":
                    line_no += 1
                    continue
                if ch == open_ch:
                    depth += 1
                elif ch == close_ch:
                    if depth == 0:
                        errors.append(f"{label}顺序错误: 第 {line_no} 行出现多余 `{close_ch}`")
                        return
                    depth -= 1

            if depth != 0:
                errors.append(f"{label}不匹配: 缺少 {depth} 个 `{close_ch}`")

        _check_pairs("{", "}", "花括号")
        _check_pairs("(", ")", "圆括号")

        # Check for FoamFile header
        if "FoamFile" not in content:
            errors.append("缺少 FoamFile 头部")

        return errors

    def _validate_boundary_consistency(self):
        """Check that boundary names are consistent across field files."""
        # Get boundaries from blockMeshDict or polyMesh/boundary
        mesh_boundaries = self._get_mesh_boundaries()
        if not mesh_boundaries:
            return

        # Check field files in 0/ directory
        zero_dir = self.case_path / "0"
        if not zero_dir.exists():
            return

        core_fields = {"U", "p", "T", "p_rgh", "alpha.water", "e", "h", "rho"}

        for field_file in zero_dir.iterdir():
            if field_file.is_file():
                try:
                    content = field_file.read_text(encoding="utf-8", errors="replace")
                    field_boundaries = self._extract_boundaries_from_field(content)

                    # Check for missing boundaries
                    missing = mesh_boundaries - field_boundaries
                    if missing:
                        severity = "error" if field_file.name in core_fields else "warning"
                        self.results.append(ValidationResult(
                            passed=False,
                            message=f"{field_file.name} 缺少边界定义",
                            details=f"缺少: {', '.join(sorted(missing))}",
                            severity=severity
                        ))

                    # Check for extra boundaries
                    extra = field_boundaries - mesh_boundaries
                    if extra:
                        self.results.append(ValidationResult(
                            passed=False,
                            message=f"{field_file.name} 有多余的边界定义",
                            details=f"多余: {', '.join(sorted(extra))}",
                            severity="warning"
                        ))

                except Exception:
                    pass

    def _extract_named_block(self, content: str, block_name: str) -> Optional[str]:
        """Extract body text of a named dictionary block."""
        start_match = re.search(rf"\b{re.escape(block_name)}\b\s*\{{", content)
        if not start_match:
            return None

        brace_start = content.find("{", start_match.start())
        if brace_start < 0:
            return None

        depth = 0
        for idx in range(brace_start, len(content)):
            ch = content[idx]
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return content[brace_start + 1 : idx]
        return None

    def _get_mesh_boundaries(self) -> set:
        boundaries = set()

        # Prefer runtime boundary file (most accurate after mesh generation).
        poly_boundary = self.case_path / "constant" / "polyMesh" / "boundary"
        if poly_boundary.exists():
            try:
                content = poly_boundary.read_text(encoding="utf-8", errors="replace")
                start_match = re.search(r"^\s*\d+\s*$\s*\n\s*\(", content, flags=re.MULTILINE)
                parse_region = content[start_match.end() :] if start_match else content
                for match in re.finditer(
                    r"^\s*([A-Za-z_][A-Za-z0-9_.-]*)\s*$\s*\n\s*\{",
                    parse_region,
                    flags=re.MULTILINE,
                ):
                    boundaries.add(match.group(1))
            except Exception:
                boundaries = set()

        if boundaries:
            return boundaries

        # Fallback: parse boundary section from system/blockMeshDict.
        block_mesh = self.case_path / "system" / "blockMeshDict"
        if not block_mesh.exists():
            return boundaries

        try:
            content = block_mesh.read_text(encoding="utf-8", errors="replace")
            boundary_keyword = re.search(r"\bboundary\b", content)
            if not boundary_keyword:
                return boundaries

            list_start = content.find("(", boundary_keyword.end())
            if list_start < 0:
                return boundaries

            depth = 0
            list_end = -1
            for idx in range(list_start, len(content)):
                ch = content[idx]
                if ch == "(":
                    depth += 1
                elif ch == ")":
                    depth -= 1
                    if depth == 0:
                        list_end = idx
                        break
            if list_end < 0:
                return boundaries

            section = content[list_start + 1 : list_end]
            for match in re.finditer(
                r"^\s*([A-Za-z_][A-Za-z0-9_.-]*)\s*$\s*\n\s*\{",
                section,
                flags=re.MULTILINE,
            ):
                boundaries.add(match.group(1))
        except Exception:
            pass
        return boundaries

    def _extract_boundaries_from_field(self, content: str) -> set:
        """Extract boundary names from a field file."""
        boundaries = set()
        boundary_body = self._extract_named_block(content, "boundaryField")
        if not boundary_body:
            return boundaries

        pending_name: Optional[str] = None
        brace_depth = 0

        for line in boundary_body.split('\n'):
            # Strip inline comments.
            line = line.split("//", 1)[0]
            stripped = line.strip()
            if not stripped:
                continue

            if brace_depth == 0:
                inline_match = re.match(r'^([A-Za-z_][A-Za-z0-9_.-]*)\s*\{', stripped)
                if inline_match:
                    boundaries.add(inline_match.group(1))
                    brace_depth += stripped.count("{") - stripped.count("}")
                    pending_name = None
                    continue

                name_match = re.match(r'^([A-Za-z_][A-Za-z0-9_.-]*)\s*$', stripped)
                if name_match:
                    pending_name = name_match.group(1)
                    continue

                if pending_name and stripped.startswith("{"):
                    boundaries.add(pending_name)
                    pending_name = None
                    brace_depth += stripped.count("{") - stripped.count("}")
                    continue

            brace_depth += stripped.count("{") - stripped.count("}")
            if brace_depth < 0:
                brace_depth = 0

        return boundaries

    def _validate_physical_parameters(self):
        """Check physical reasonableness of parameters."""
        # Check transportProperties
        transport_file = self.case_path / "constant" / "transportProperties"
        if transport_file.exists():
            try:
                content = transport_file.read_text()
                # Extract viscosity
                nu_match = re.search(r'nu\s+\[.*?\]\s+([\d.e+-]+)', content)
                if nu_match:
                    nu = float(nu_match.group(1))
                    if nu < 1e-10 or nu > 1:
                        self.results.append(ValidationResult(
                            passed=False,
                            message="运动粘度值可能不合理",
                            details=f"nu = {nu} m²/s (通常水为 1e-6, 空气为 1.5e-5)",
                            severity="warning"
                        ))
                    else:
                        self.results.append(ValidationResult(
                            passed=True,
                            message="运动粘度值在合理范围内"
                        ))
            except Exception:
                pass

        # Check controlDict
        control_file = self.case_path / "system" / "controlDict"
        if control_file.exists():
            try:
                content = control_file.read_text()
                # Check deltaT
                dt_match = re.search(r'deltaT\s+([\d.e+-]+)', content)
                if dt_match:
                    dt = float(dt_match.group(1))
                    if dt <= 0:
                        self.results.append(ValidationResult(
                            passed=False,
                            message="时间步长必须为正值",
                            details=f"deltaT = {dt}",
                            severity="error"
                        ))
            except Exception:
                pass

        # Validate boundary condition logic
        self._validate_boundary_condition_logic()

    def _validate_boundary_condition_logic(self):
        """Check boundary condition combinations for physical reasonableness."""
        zero_dir = self.case_path / "0"
        if not zero_dir.exists():
            return

        # Check velocity field (U) for boundary condition logic
        u_file = zero_dir / "U"
        if u_file.exists():
            try:
                content = u_file.read_text()
                bc_types = self._extract_boundary_types(content)

                if bc_types:
                    # Check for all zeroGradient (under-constrained)
                    all_zero_grad = all(
                        t in ['zeroGradient', 'slip', 'symmetry', 'symmetryPlane', 'empty']
                        for t in bc_types.values()
                    )
                    if all_zero_grad:
                        self.results.append(ValidationResult(
                            passed=False,
                            message="速度场边界条件可能欠约束",
                            details="所有边界都是 zeroGradient/slip 类型，没有入口或壁面条件",
                            severity="warning"
                        ))

                    # Check for no inlet
                    has_inlet = any(
                        t in ['fixedValue', 'uniformFixedValue', 'flowRateInletVelocity',
                              'turbulentInlet', 'mappedFixedValue']
                        for t in bc_types.values()
                    )
                    if not has_inlet and not all_zero_grad:
                        # Check if it's a lid-driven cavity (has movingWall)
                        has_moving_wall = any(
                            t in ['movingWallVelocity', 'fixedValue']
                            for t in bc_types.values()
                        )
                        if not has_moving_wall:
                            self.results.append(ValidationResult(
                                passed=False,
                                message="速度场缺少入口边界条件",
                                details="建议至少有一个 fixedValue 或 flowRateInletVelocity 入口",
                                severity="warning"
                            ))
                    else:
                        self.results.append(ValidationResult(
                            passed=True,
                            message="速度场边界条件检查通过"
                        ))

            except Exception:
                pass

        # Check pressure field (p) for boundary condition logic
        p_file = zero_dir / "p"
        if p_file.exists():
            try:
                content = p_file.read_text()
                bc_types = self._extract_boundary_types(content)

                if bc_types:
                    # Pressure should have at least one reference (fixedValue or totalPressure)
                    has_pressure_ref = any(
                        t in ['fixedValue', 'totalPressure', 'uniformTotalPressure',
                              'fixedMean', 'prghTotalPressure']
                        for t in bc_types.values()
                    )

                    # Check for all fixedValue (over-constrained for incompressible)
                    all_fixed = all(
                        t in ['fixedValue', 'totalPressure', 'empty', 'symmetry', 'symmetryPlane']
                        for t in bc_types.values()
                    )
                    if all_fixed:
                        # This might be okay for some cases, just a warning
                        self.results.append(ValidationResult(
                            passed=True,
                            message="压力场边界条件检查通过",
                            details="所有边界都指定了压力值",
                            severity="info"
                        ))
                    elif has_pressure_ref:
                        self.results.append(ValidationResult(
                            passed=True,
                            message="压力场边界条件检查通过"
                        ))
                    else:
                        self.results.append(ValidationResult(
                            passed=False,
                            message="压力场缺少参考值",
                            details="建议至少有一个边界设置 fixedValue 或 totalPressure",
                            severity="warning"
                        ))

            except Exception:
                pass

        # Check if turbulence fields exist and have proper inlet conditions
        self._validate_turbulence_inlet_conditions()

    def _extract_boundary_types(self, content: str) -> Dict[str, str]:
        """Extract boundary names and their types from a field file."""
        boundaries = {}
        in_boundary_field = False
        current_boundary = None
        brace_depth = 0

        for line in content.split('\n'):
            stripped = line.strip()

            if 'boundaryField' in line:
                in_boundary_field = True
                continue

            if in_boundary_field:
                if '{' in line:
                    brace_depth += line.count('{')
                if '}' in line:
                    brace_depth -= line.count('}')

                if brace_depth <= 0 and '}' in line:
                    break

                # Detect boundary name (line with just a word, followed by { on next line)
                if brace_depth == 1 and stripped and not stripped.startswith('//'):
                    if '{' not in stripped and '}' not in stripped and 'type' not in stripped:
                        current_boundary = stripped.rstrip(';')

                # Extract type
                if current_boundary and 'type' in stripped:
                    type_match = re.search(r'type\s+(\w+)', stripped)
                    if type_match:
                        boundaries[current_boundary] = type_match.group(1)
                        current_boundary = None

        return boundaries

    def _validate_turbulence_inlet_conditions(self):
        """Check that turbulence fields have proper inlet conditions."""
        zero_dir = self.case_path / "0"

        # Check if turbulence fields exist
        turbulence_fields = ['k', 'epsilon', 'omega', 'nut', 'nuTilda']
        existing_turb_fields = []

        for field in turbulence_fields:
            if (zero_dir / field).exists():
                existing_turb_fields.append(field)

        if not existing_turb_fields:
            # No turbulence fields, probably laminar case
            return

        # Get inlet boundaries from U file
        u_file = zero_dir / "U"
        inlet_boundaries = []

        if u_file.exists():
            try:
                content = u_file.read_text()
                bc_types = self._extract_boundary_types(content)

                for boundary, bc_type in bc_types.items():
                    if bc_type in ['fixedValue', 'uniformFixedValue', 'flowRateInletVelocity',
                                   'turbulentInlet', 'mappedFixedValue']:
                        inlet_boundaries.append(boundary)
            except Exception:
                pass

        if not inlet_boundaries:
            return

        # Check each turbulence field for proper inlet conditions
        for turb_field in existing_turb_fields:
            if turb_field == 'nut':
                # nut is usually calculated, not specified
                continue

            field_file = zero_dir / turb_field
            try:
                content = field_file.read_text()
                bc_types = self._extract_boundary_types(content)

                for inlet in inlet_boundaries:
                    if inlet in bc_types:
                        bc_type = bc_types[inlet]
                        # Inlet should not be zeroGradient for turbulence quantities
                        if bc_type == 'zeroGradient':
                            self.results.append(ValidationResult(
                                passed=False,
                                message=f"{turb_field} 入口边界条件可能不正确",
                                details=f"入口 '{inlet}' 使用 zeroGradient，建议使用 fixedValue 或 turbulentIntensityKineticEnergyInlet",
                                severity="warning"
                            ))
                        else:
                            self.results.append(ValidationResult(
                                passed=True,
                                message=f"{turb_field} 入口边界条件检查通过"
                            ))
                            break  # Only need one passing check per field
            except Exception:
                pass

    def _run_openfoam_validation(self):
        """Run actual OpenFOAM validation commands."""
        # Check if OpenFOAM is available
        of_available = self._check_openfoam_available()
        if not of_available:
            self.results.append(ValidationResult(
                passed=False,
                message="OpenFOAM 环境未检测到",
                details="请确保已加载 OpenFOAM 环境 (source $WM_PROJECT_DIR/etc/bashrc)",
                severity="warning"
            ))
            return

        # Run blockMesh if blockMeshDict exists
        block_mesh_dict = self.case_path / "system" / "blockMeshDict"
        if block_mesh_dict.exists():
            self._run_block_mesh()

        # Run checkMesh if mesh exists
        poly_mesh = self.case_path / "constant" / "polyMesh"
        if poly_mesh.exists():
            self._run_check_mesh()

    def _check_openfoam_available(self) -> bool:
        """Check if OpenFOAM commands are available."""
        try:
            result = subprocess.run(
                ["which", "blockMesh"],
                capture_output=True,
                text=True,
                timeout=5
            )
            return result.returncode == 0
        except Exception:
            return False

    def _run_block_mesh(self):
        """Run blockMesh and capture results."""
        try:
            result = subprocess.run(
                ["blockMesh"],
                cwd=self.case_path,
                capture_output=True,
                text=True,
                timeout=60
            )

            if result.returncode == 0:
                # Extract cell count
                cells_match = re.search(r'cells:\s+(\d+)', result.stdout)
                cell_count = cells_match.group(1) if cells_match else "未知"

                self.results.append(ValidationResult(
                    passed=True,
                    message="blockMesh 执行成功",
                    details=f"生成了 {cell_count} 个单元"
                ))
            else:
                self.results.append(ValidationResult(
                    passed=False,
                    message="blockMesh 执行失败",
                    details=result.stderr[:500] if result.stderr else result.stdout[:500],
                    severity="error"
                ))
        except subprocess.TimeoutExpired:
            self.results.append(ValidationResult(
                passed=False,
                message="blockMesh 执行超时",
                severity="error"
            ))
        except Exception as e:
            self.results.append(ValidationResult(
                passed=False,
                message="blockMesh 执行出错",
                details=str(e),
                severity="error"
            ))

    def _run_check_mesh(self):
        """Run checkMesh and analyze results."""
        try:
            result = subprocess.run(
                ["checkMesh"],
                cwd=self.case_path,
                capture_output=True,
                text=True,
                timeout=120
            )

            output = result.stdout + result.stderr

            # Check for mesh errors
            if "FOAM FATAL ERROR" in output:
                self.results.append(ValidationResult(
                    passed=False,
                    message="checkMesh 报告严重错误",
                    details=self._extract_foam_error(output),
                    severity="error"
                ))
                return

            # Check mesh quality metrics
            quality_issues = []

            # Non-orthogonality
            non_ortho_match = re.search(r'Max non-orthogonality\s*=\s*([\d.]+)', output)
            if non_ortho_match:
                max_non_ortho = float(non_ortho_match.group(1))
                if max_non_ortho > 70:
                    quality_issues.append(f"最大非正交性 {max_non_ortho:.1f}° > 70° (高)")
                elif max_non_ortho > 65:
                    quality_issues.append(f"最大非正交性 {max_non_ortho:.1f}° > 65° (中等)")

            # Skewness
            skew_match = re.search(r'Max skewness\s*=\s*([\d.]+)', output)
            if skew_match:
                max_skew = float(skew_match.group(1))
                if max_skew > 4:
                    quality_issues.append(f"最大偏斜度 {max_skew:.2f} > 4 (高)")

            # Aspect ratio
            aspect_match = re.search(r'Max aspect ratio\s*=\s*([\d.]+)', output)
            if aspect_match:
                max_aspect = float(aspect_match.group(1))
                if max_aspect > 100:
                    quality_issues.append(f"最大纵横比 {max_aspect:.1f} > 100 (高)")

            if quality_issues:
                self.results.append(ValidationResult(
                    passed=False,
                    message="网格质量警告",
                    details="; ".join(quality_issues),
                    severity="warning"
                ))

            if "Mesh OK" in output:
                self.results.append(ValidationResult(
                    passed=True,
                    message="checkMesh 验证通过",
                    details="网格质量良好"
                ))
            else:
                self.results.append(ValidationResult(
                    passed=True,
                    message="checkMesh 完成",
                    details="未检测到严重问题"
                ))

        except subprocess.TimeoutExpired:
            self.results.append(ValidationResult(
                passed=False,
                message="checkMesh 执行超时",
                severity="warning"
            ))
        except Exception as e:
            self.results.append(ValidationResult(
                passed=False,
                message="checkMesh 执行出错",
                details=str(e),
                severity="warning"
            ))

    def _extract_foam_error(self, output: str) -> str:
        """Extract the relevant error message from FOAM output and provide suggestions."""
        lines = output.split('\n')
        error_lines = []
        capture = False

        for line in lines:
            if "FOAM FATAL ERROR" in line:
                capture = True
            if capture:
                error_lines.append(line)
                if len(error_lines) > 10:
                    break

        error_text = '\n'.join(error_lines) if error_lines else output[:500]

        # Add helpful suggestion based on error patterns
        suggestion = self._get_error_suggestion(output)
        if suggestion:
            error_text += f"\n\n💡 建议: {suggestion}"

        return error_text if error_text else "未知错误"

    def _get_error_suggestion(self, error_output: str) -> str:
        """Get helpful suggestion based on error pattern."""
        error_patterns = {
            # Mesh errors
            "negative cell volume": "检查 blockMeshDict 中的点坐标顺序，确保形成右手坐标系",
            "face vertices are not planar": "检查网格点坐标，确保面上的点共面",
            "empty patch": "确保 empty 边界类型用于 2D 仿真的前后面",
            "Boundary definition is too short": "检查边界定义数量，可能遗漏了某些边界",

            # Solver errors
            "Maximum number of iterations exceeded": "尝试：1) 降低松弛因子 (U: 0.3-0.5, p: 0.1-0.2)  2) 改用一阶格式 (upwind)  3) 检查边界条件",
            "Continuity error": "可能原因：1) 边界条件不匹配（入口流量≠出口流量）2) 网格质量差  3) 时间步长过大",
            "floating point exception": "数值发散，尝试：1) 减小时间步长  2) 降低松弛因子  3) 使用一阶格式  4) 检查初始场",
            "Negative": "出现负值，检查：1) 初始场是否合理  2) 边界条件是否正确  3) 时间步长是否过大",

            # Boundary condition errors
            "Unknown patch type": "检查边界类型名称是否拼写正确",
            "patch not found": "检查边界名称是否与 blockMeshDict 中的定义一致",
            "dimensions not equal": "检查场变量的单位维度是否正确",
            "size of field": "场变量大小与网格不匹配，尝试删除时间步目录后重新运行",

            # File errors
            "cannot find file": "检查文件路径和文件名是否正确",
            "cannot open file": "检查文件权限和路径",
            "error reading": "检查文件格式是否正确，括号是否匹配",

            # Physics errors
            "Courant Number": "CFL 数过大，减小时间步长或使用自适应时间步",
            "bounding k": "湍动能 k 被限制，检查入口边界条件和初始场",
            "bounding epsilon": "耗散率被限制，检查入口边界条件和初始场",

            # Pressure-related errors
            "pressure reference": "封闭系统需要设置 pRefCell 和 pRefValue，在 fvSolution 中添加",
            "singular matrix": "矩阵奇异，可能是：1) 封闭系统缺少压力参考点  2) 边界条件过约束或欠约束",
        }

        error_lower = error_output.lower()
        for pattern, suggestion in error_patterns.items():
            if pattern.lower() in error_lower:
                return suggestion

        return ""


# Common OpenFOAM error patterns and their suggestions (exported for use by other modules)
OPENFOAM_ERROR_PATTERNS = {
    "Maximum number of iterations exceeded": {
        "zh": "迭代次数超过最大值",
        "suggestion": "降低松弛因子或改用一阶格式",
        "severity": "error"
    },
    "Continuity error": {
        "zh": "连续性误差",
        "suggestion": "检查边界条件匹配性和网格质量",
        "severity": "warning"
    },
    "floating point exception": {
        "zh": "浮点异常（数值发散）",
        "suggestion": "减小时间步长、降低松弛因子、使用一阶格式",
        "severity": "error"
    },
    "Negative": {
        "zh": "出现负值",
        "suggestion": "检查初始场、边界条件和时间步长",
        "severity": "warning"
    },
    "Courant Number": {
        "zh": "CFL 数过大",
        "suggestion": "减小时间步长或启用自适应时间步",
        "severity": "warning"
    },
    "singular matrix": {
        "zh": "矩阵奇异",
        "suggestion": "检查压力参考点设置和边界条件约束",
        "severity": "error"
    },
    "bounding k": {
        "zh": "湍动能被限制",
        "suggestion": "检查入口湍流参数和初始场",
        "severity": "warning"
    },
}


def parse_solver_log_errors(log_content: str) -> List[Dict[str, str]]:
    """
    Parse solver log for errors and warnings with suggestions.

    Args:
        log_content: Content of the solver log file

    Returns:
        List of dicts with 'pattern', 'message', 'suggestion', 'severity'
    """
    found_issues = []
    log_lower = log_content.lower()

    for pattern, info in OPENFOAM_ERROR_PATTERNS.items():
        if pattern.lower() in log_lower:
            found_issues.append({
                "pattern": pattern,
                "message": info["zh"],
                "suggestion": info["suggestion"],
                "severity": info["severity"]
            })

    return found_issues


def validate_case(case_path: str, run_openfoam: bool = True) -> ValidationReport:
    """
    Validate an OpenFOAM case.

    Args:
        case_path: Path to the case directory
        run_openfoam: Whether to run actual OpenFOAM validation commands

    Returns:
        ValidationReport with all results
    """
    validator = CaseValidator(case_path)
    return validator.validate_all(run_openfoam_checks=run_openfoam)


def generate_case_checklist(config: Any, mesh_warnings: List[str] = None) -> List[str]:
    """
    Generate a post-creation checklist for case verification.

    Args:
        config: CaseConfig object with case parameters
        mesh_warnings: List of mesh quality warnings (if any)

    Returns:
        List of checklist items (markdown format)
    """
    from ..knowledge.solvers import TimeType, TurbulenceType

    checklist = []
    checklist.append("## 案例检查清单")
    checklist.append("")
    checklist.append("创建案例后，请验证以下内容：")
    checklist.append("")

    # 1. Boundary conditions
    checklist.append("### 1. 边界条件合理性")
    checklist.append("- [ ] 入口边界条件符合物理情况（速度、压力、湍流参数）")
    checklist.append("- [ ] 出口边界条件允许流体自由流出")
    checklist.append("- [ ] 壁面边界条件设置正确（无滑移/滑移/对称）")

    # For turbulent flows, add turbulence BC check
    if config.turbulence_type != TurbulenceType.LAMINAR:
        checklist.append("- [ ] 湍流入口条件 (k, epsilon/omega) 已正确指定")

    # For heat transfer, add temperature BC check
    if hasattr(config, 'has_heat_transfer') and config.has_heat_transfer:
        checklist.append("- [ ] 温度边界条件与物理场景匹配")

    checklist.append("")

    # 2. Mesh quality
    checklist.append("### 2. 网格质量")
    checklist.append("- [ ] 运行 `checkMesh` 验证网格质量")
    checklist.append("- [ ] 非正交性 < 75° （建议 < 70°）")
    checklist.append("- [ ] 长宽比 < 20:1 （建议 < 10:1）")

    if mesh_warnings:
        checklist.append("- [ ] **解决网格警告**:")
        for w in mesh_warnings[:3]:  # Show first 3 warnings
            checklist.append(f"  - {w}")

    checklist.append("")

    # 3. y+ value for turbulent flows
    if config.turbulence_type != TurbulenceType.LAMINAR:
        checklist.append("### 3. y+ 值检查（湍流）")
        # Since we typically use k-epsilon by default, give general guidance
        checklist.append("- [ ] k-epsilon 壁面函数: 第一层网格 y+ 在 30-300 范围")
        checklist.append("- [ ] k-omega SST: y+ < 1 （低雷诺数）或 y+ > 30 （壁面函数）")
        checklist.append("- [ ] 运行案例后检查 yPlus 输出")
        checklist.append("")

    # 4. Initial fields
    checklist.append("### 4. 初始场稳定性")
    checklist.append("- [ ] 压力初始场与边界条件兼容")
    checklist.append("- [ ] 速度初始场合理（通常设为入口速度或0）")

    if config.turbulence_type != TurbulenceType.LAMINAR:
        checklist.append("- [ ] 湍流场初始值合理（k, epsilon/omega > 0）")

    checklist.append("")

    # 5. Time step for transient cases
    if config.time_type == TimeType.TRANSIENT:
        checklist.append("### 5. 时间步长（瞬态）")
        checklist.append("- [ ] CFL 数 < 1 （或启用自适应时间步）")
        checklist.append("- [ ] 初始时间步足够小以确保稳定启动")

        # Add specific check for compressible flows
        if hasattr(config, 'flow_type') and 'compressible' in str(config.flow_type).lower():
            checklist.append("- [ ] 可压缩流：maxCo 通常设为 0.2-0.5")

        # Add specific check for multiphase flows
        if 'interFoam' in config.solver or 'multiphase' in str(getattr(config, 'flow_type', '')).lower():
            checklist.append("- [ ] 多相流：maxAlphaCo < 1 以保持界面清晰")

        checklist.append("")

    # 6. Solver settings
    checklist.append("### 6. 求解器设置")
    checklist.append(f"- [ ] 求解器 `{config.solver}` 适合当前流动类型")

    if config.reynolds_number:
        re = config.reynolds_number
        if re < 2300:
            checklist.append(f"- [ ] Re={re:.0f} < 2300: 层流求解器或层流模型合适")
        elif 2300 <= re < 4000:
            checklist.append(f"- [ ] Re={re:.0f} 在过渡区: 考虑使用瞬态求解器捕捉不稳定性")
        else:
            checklist.append(f"- [ ] Re={re:.0f} > 4000: 湍流模型已启用")

    checklist.append("- [ ] 松弛因子设置合理（高 Re 更保守，低 Re 更激进）")
    checklist.append("")

    # 7. Convergence monitoring
    checklist.append("### 7. 收敛监控")
    checklist.append("- [ ] controlDict 中已启用 residual 监控")
    checklist.append("- [ ] 稳态求解: 残差 < 1e-4 或持续减小")
    checklist.append("- [ ] 瞬态求解: 每个时间步内残差收敛")

    if hasattr(config, 'boundaries') and any('cylinder' in str(b).lower() or 'airfoil' in str(b).lower() for b in config.boundaries):
        checklist.append("- [ ] 已启用力系数监控（升力、阻力）")

    checklist.append("")

    # 8. Performance
    checklist.append("### 8. 计算性能")

    if config.mesh_params:
        from ..knowledge.mesh_strategies import estimate_cell_count
        total_cells = estimate_cell_count(config.mesh_params)

        if total_cells < 10000:
            checklist.append(f"- [ ] 网格规模: {total_cells:,} 单元 → 单核运行即可")
        elif total_cells < 100000:
            checklist.append(f"- [ ] 网格规模: {total_cells:,} 单元 → 建议 2-4 核并行")
        else:
            checklist.append(f"- [ ] 网格规模: {total_cells:,} 单元 → 建议 8+ 核并行")

    checklist.append("")

    return checklist


def generate_performance_recommendations(cell_count: int, config: Any) -> List[str]:
    """
    Generate performance optimization recommendations based on case size.

    Args:
        cell_count: Total number of mesh cells
        config: CaseConfig object with case parameters

    Returns:
        List of recommendation lines (markdown format)
    """
    from ..knowledge.solvers import TimeType

    recommendations = []
    recommendations.append("## 性能优化建议")
    recommendations.append("")

    # Parallel computation recommendations
    if cell_count < 10000:
        recommendations.append(f"**网格规模**: {cell_count:,} 单元（小型）")
        recommendations.append("- ✅ 单核运行效率最高")
        recommendations.append("- 预计求解时间: 几分钟到几十分钟")
    elif cell_count < 100000:
        recommendations.append(f"**网格规模**: {cell_count:,} 单元（中型）")
        recommendations.append("- ✅ 推荐使用 2-4 核并行")
        recommendations.append("- 使用 `openfoam_run_parallel` 工具运行")
        recommendations.append(f"- 示例: `n_processors=4` 可提速约 3-3.5 倍")
        recommendations.append("- 预计求解时间: 几十分钟到几小时")
    else:
        recommendations.append(f"**网格规模**: {cell_count:,} 单元（大型）")
        recommendations.append("- ✅ 推荐使用 8+ 核并行")
        recommendations.append("- 使用 `openfoam_run_parallel` 工具运行")

        if cell_count < 500000:
            recommendations.append(f"- 示例: `n_processors=8` 可提速约 6-7 倍")
        else:
            recommendations.append(f"- 示例: `n_processors=16` 可提速约 12-14 倍")
            recommendations.append(f"- 考虑使用集群或云计算资源")

        recommendations.append("- 预计求解时间: 几小时到几天")

    recommendations.append("")

    # Solver-specific recommendations
    if config.time_type == TimeType.STEADY:
        recommendations.append("**稳态求解优化**:")
        recommendations.append("- 使用渐进式松弛因子（从保守到激进）")
        recommendations.append("- 监控残差，当残差 < 1e-4 时可提前停止")
        recommendations.append("- 对于高 Re 流动，先用一阶格式求解，再切换到高阶格式")
    else:
        recommendations.append("**瞬态求解优化**:")
        recommendations.append("- 启用自适应时间步（adjustTimeStep true）")
        recommendations.append("- 初始使用小时间步，稳定后可增大")
        recommendations.append("- 使用 writeInterval 控制输出频率，避免过多 I/O")

        # Specific for compressible flows
        if 'rhoCentralFoam' in config.solver:
            recommendations.append("- 可压缩流: maxCo 保持在 0.2-0.5")

        # Specific for multiphase flows
        if 'interFoam' in config.solver:
            recommendations.append("- 多相流: maxAlphaCo < 1 以保证界面清晰")

    recommendations.append("")

    # Memory and storage recommendations
    if cell_count > 1000000:
        recommendations.append("**资源需求估算**:")
        recommendations.append(f"- 内存需求: 约 {cell_count * 2 / 1000000:.1f} - {cell_count * 4 / 1000000:.1f} GB")
        recommendations.append(f"- 存储空间: 建议预留 20-50 GB（包含时间步输出）")
        recommendations.append("")

    return recommendations
