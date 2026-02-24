"""OpenFOAM solver runner and process management."""

from typing import Dict, List, Optional, Callable
from pathlib import Path
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
import logging
import subprocess
import threading
import queue
import os
import re
import time
from shutil import which


logger = logging.getLogger(__name__)
DEFAULT_LOG_TAIL_LINES = 50
ERROR_MESSAGE_MAX_CHARS = 500
CONVERGENCE_RESIDUAL_THRESHOLD = 1e-3
_LOG_FILE_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9_.-]+")


class RunStatus(str, Enum):
    """Status of a solver run."""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class RunProgress:
    """Progress information for a running solver."""
    status: RunStatus
    current_time: float = 0.0
    end_time: float = 0.0
    iteration: int = 0
    residuals: Dict[str, float] = field(default_factory=dict)
    elapsed_seconds: float = 0.0
    estimated_remaining: Optional[float] = None
    last_update: datetime = field(default_factory=datetime.now)


@dataclass
class RunResult:
    """Result of a solver run."""
    status: RunStatus
    return_code: int
    stdout: str
    stderr: str
    duration_seconds: float
    final_residuals: Dict[str, float]
    converged: bool = False
    error_message: Optional[str] = None


class SolverRunner:
    """Manages OpenFOAM solver execution."""

    def __init__(self, case_path: str):
        self.case_path = Path(case_path)
        self.process: Optional[subprocess.Popen] = None
        self.progress = RunProgress(status=RunStatus.PENDING)
        self._output_queue: queue.Queue = queue.Queue()
        self._monitor_thread: Optional[threading.Thread] = None
        self._stop_monitoring = threading.Event()
        self._stdout_lines: List[str] = []
        self._stderr_lines: List[str] = []
        self._start_time: Optional[datetime] = None
        self._state_lock = threading.RLock()
        self._process_lock = threading.RLock()

    def _resolve_command(self, command: str) -> Optional[str]:
        """Resolve command path from explicit path, PATH, or FOAM_APPBIN."""
        if any(ch.isspace() for ch in command) or "\x00" in command:
            return None

        # Explicit path-like command
        if "/" in command:
            candidate = Path(command)
            return str(candidate) if candidate.is_file() and os.access(candidate, os.X_OK) else None

        path = which(command)
        if path:
            return path

        foam_appbin = os.environ.get("FOAM_APPBIN")
        if foam_appbin:
            candidate = Path(foam_appbin) / command
            if candidate.is_file():
                return str(candidate)

        return None

    def _cleanup_process(self) -> None:
        """Close process pipes and clear process handle."""
        with self._process_lock:
            proc = self.process
            if not proc:
                return

            for pipe in (proc.stdout, proc.stderr):
                if pipe:
                    try:
                        pipe.close()
                    except Exception:
                        pass

            self.process = None

    def run_command(
        self,
        command: str,
        args: List[str] = None,
        timeout: Optional[float] = None,
        on_progress: Optional[Callable[[RunProgress], None]] = None
    ) -> RunResult:
        """
        Run an OpenFOAM command and wait for completion.

        Args:
            command: The command to run (e.g., "simpleFoam", "blockMesh")
            args: Additional arguments
            timeout: Maximum execution time in seconds
            on_progress: Callback for progress updates (called after completion)

        Returns:
            RunResult with execution details
        """
        if args is None:
            args = []

        resolved_command = self._resolve_command(command)
        if not resolved_command:
            return RunResult(
                status=RunStatus.FAILED,
                return_code=-1,
                stdout='',
                stderr=f"命令未找到: {command}",
                duration_seconds=0,
                final_residuals={},
                error_message=f"命令 '{command}' 未找到。请确保 OpenFOAM 环境已正确设置。"
            )

        full_command = [resolved_command] + args
        logger.info(
            "开始执行命令 command=%s case_path=%s args=%s timeout=%s",
            command,
            self.case_path,
            args,
            timeout,
        )
        self._start_time = datetime.now()
        with self._state_lock:
            self.progress = RunProgress(status=RunStatus.RUNNING)
            self._stdout_lines = []
            self._stderr_lines = []

        try:
            with self._process_lock:
                self.process = subprocess.Popen(
                    full_command,
                    cwd=self.case_path,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    # Use default (block) buffering for communicate() safety
                )

            # Use communicate() exclusively — do NOT mix with a reader thread
            # on the same pipe, as that causes a race condition / deadlock.
            try:
                with self._process_lock:
                    proc = self.process
                if proc is None:
                    raise RuntimeError("进程启动失败")
                stdout, stderr = proc.communicate(timeout=timeout)
            except subprocess.TimeoutExpired:
                with self._process_lock:
                    proc = self.process
                if proc is None:
                    raise RuntimeError("进程状态异常")
                proc.kill()
                stdout, stderr = proc.communicate()
                with self._state_lock:
                    self._stdout_lines = stdout.split('\n')
                    self._stderr_lines = stderr.split('\n')
                # Parse progress from collected output
                for line in stdout.split('\n'):
                    self._parse_progress_line(line)

                logger.warning(
                    "命令执行超时 command=%s case_path=%s timeout=%s",
                    command,
                    self.case_path,
                    timeout,
                )

                return RunResult(
                    status=RunStatus.FAILED,
                    return_code=-1,
                    stdout=stdout,
                    stderr=stderr,
                    duration_seconds=self._get_elapsed_seconds(),
                    final_residuals=self.progress.residuals,
                    error_message="执行超时"
                )

            with self._state_lock:
                self._stdout_lines = stdout.split('\n')
                self._stderr_lines = stderr.split('\n')

            # Parse progress from the collected output
            for line in self._stdout_lines:
                self._parse_progress_line(line)

            if on_progress:
                on_progress(self.progress)

            # Determine result
            duration = self._get_elapsed_seconds()

            with self._process_lock:
                proc = self.process
            return_code = proc.returncode if proc else -1
            if return_code == 0:
                converged = self._check_convergence(stdout)
                logger.info(
                    "命令执行完成 command=%s case_path=%s return_code=0 converged=%s duration=%.3f",
                    command,
                    self.case_path,
                    converged,
                    duration,
                )
                return RunResult(
                    status=RunStatus.COMPLETED,
                    return_code=0,
                    stdout=stdout,
                    stderr=stderr,
                    duration_seconds=duration,
                    final_residuals=self.progress.residuals,
                    converged=converged
                )
            else:
                error_msg = self._extract_error_message(stdout + stderr)
                logger.error(
                    "命令执行失败 command=%s case_path=%s return_code=%s error=%s",
                    command,
                    self.case_path,
                    return_code,
                    error_msg,
                )
                return RunResult(
                    status=RunStatus.FAILED,
                    return_code=return_code,
                    stdout=stdout,
                    stderr=stderr,
                    duration_seconds=duration,
                    final_residuals=self.progress.residuals,
                    error_message=error_msg
                )

        except FileNotFoundError:
            return RunResult(
                status=RunStatus.FAILED,
                return_code=-1,
                stdout='',
                stderr=f"命令未找到: {command}",
                duration_seconds=0,
                final_residuals={},
                error_message=f"命令 '{command}' 未找到。请确保 OpenFOAM 环境已正确设置。"
            )
        except Exception as e:
            with self._state_lock:
                stdout_text = '\n'.join(self._stdout_lines)
                stderr_text = '\n'.join(self._stderr_lines)
            return RunResult(
                status=RunStatus.FAILED,
                return_code=-1,
                stdout=stdout_text,
                stderr=stderr_text + f"\n{str(e)}",
                duration_seconds=self._get_elapsed_seconds(),
                final_residuals={},
                error_message=str(e)
            )
        finally:
            self._cleanup_process()

    def run_async(
        self,
        command: str,
        args: List[str] = None,
        on_progress: Optional[Callable[[RunProgress], None]] = None
    ) -> None:
        """
        Start a solver run in the background.

        Args:
            command: The command to run
            args: Additional arguments
            on_progress: Callback for progress updates
        """
        if args is None:
            args = []

        resolved_command = self._resolve_command(command)
        if not resolved_command:
            raise FileNotFoundError(f"命令未找到: {command}")

        full_command = [resolved_command] + args
        self._start_time = datetime.now()
        with self._state_lock:
            self.progress = RunProgress(status=RunStatus.RUNNING)
            self._stdout_lines = []
            self._stderr_lines = []

        with self._process_lock:
            self.process = subprocess.Popen(
                full_command,
                cwd=self.case_path,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )

        # Start monitoring thread
        self._stop_monitoring.clear()
        self._monitor_thread = threading.Thread(
            target=self._monitor_output,
            args=(on_progress,)
        )
        self._monitor_thread.daemon = True
        self._monitor_thread.start()

    def cancel(self) -> bool:
        """
        Cancel a running solver.

        Returns:
            True if cancelled, False if no process was running
        """
        with self._process_lock:
            proc = self.process

        if proc and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()

            with self._state_lock:
                self.progress.status = RunStatus.CANCELLED
            self._stop_monitoring.set()
            if self._monitor_thread and self._monitor_thread.is_alive():
                self._monitor_thread.join(timeout=2)
            # For synchronous run_command(), avoid closing pipes while
            # communicate() may still be active in another thread.
            if self._monitor_thread is not None:
                self._cleanup_process()
            return True
        return False

    def get_status(self) -> RunProgress:
        """Get current run status and progress."""
        with self._process_lock:
            proc = self.process

        if proc:
            poll = proc.poll()
            with self._state_lock:
                if poll is None:
                    self.progress.status = RunStatus.RUNNING
                elif poll == 0:
                    self.progress.status = RunStatus.COMPLETED
                else:
                    self.progress.status = RunStatus.FAILED

                self.progress.elapsed_seconds = self._get_elapsed_seconds()

        with self._state_lock:
            return self.progress

    def get_log_tail(self, lines: int = DEFAULT_LOG_TAIL_LINES) -> str:
        """Get the last N lines of output."""
        with self._state_lock:
            all_lines = list(self._stdout_lines) + list(self._stderr_lines)
        return '\n'.join(all_lines[-lines:])

    def _monitor_output(self, on_progress: Optional[Callable[[RunProgress], None]]):
        """Monitor process output in a separate thread."""
        with self._process_lock:
            proc = self.process
        if not proc:
            return

        try:
            while not self._stop_monitoring.is_set():
                with self._process_lock:
                    proc = self.process
                if not proc or proc.poll() is not None:
                    break

                try:
                    # Read stdout
                    if proc.stdout:
                        line = proc.stdout.readline()
                        if line:
                            with self._state_lock:
                                self._stdout_lines.append(line.rstrip())
                            self._parse_progress_line(line)

                            if on_progress:
                                on_progress(self.progress)
                except Exception as exc:
                    with self._state_lock:
                        self._stderr_lines.append(f"[monitor_error] {exc}")
                    self._stop_monitoring.set()
                    break

                time.sleep(0.1)
        finally:
            with self._process_lock:
                proc = self.process
            if proc and proc.stdout:
                try:
                    while True:
                        line = proc.stdout.readline()
                        if not line:
                            break
                        with self._state_lock:
                            self._stdout_lines.append(line.rstrip())
                        self._parse_progress_line(line)
                except Exception:
                    logger.exception("monitor 输出收尾阶段异常")
            with self._process_lock:
                live_proc = self.process
            final_status: Optional[RunStatus] = None
            if live_proc is None:
                final_status = RunStatus.CANCELLED
            else:
                poll = live_proc.poll()
                if poll is None:
                    final_status = RunStatus.RUNNING
                elif poll == 0:
                    final_status = RunStatus.COMPLETED
                else:
                    final_status = RunStatus.FAILED

            with self._state_lock:
                if self.progress.status == RunStatus.RUNNING and final_status is not None:
                    self.progress.status = final_status

    def _parse_progress_line(self, line: str):
        """Parse a log line for progress information."""
        with self._state_lock:
            # Parse time step
            time_match = re.search(r'Time\s*=\s*([\d.e+-]+)', line)
            if time_match:
                self.progress.current_time = float(time_match.group(1))

            # Parse iteration
            iter_match = re.search(r'Iteration\s*(\d+)', line, re.IGNORECASE)
            if iter_match:
                self.progress.iteration = int(iter_match.group(1))

            # Parse residuals
            # Format: "Solving for U, Initial residual = X, Final residual = Y"
            residual_match = re.search(
                r'Solving for (\w+),.*?Final residual\s*=\s*([\d.e+-]+)',
                line
            )
            if residual_match:
                field = residual_match.group(1)
                residual = float(residual_match.group(2))
                self.progress.residuals[field] = residual

            self.progress.last_update = datetime.now()
            self.progress.elapsed_seconds = self._get_elapsed_seconds()

    def _get_elapsed_seconds(self) -> float:
        """Get elapsed time since start."""
        if self._start_time:
            return (datetime.now() - self._start_time).total_seconds()
        return 0.0

    def _check_convergence(self, output: str) -> bool:
        """Check if the solver converged."""
        # Look for convergence indicators
        if "solution converged" in output.lower():
            return True
        if re.search(r"^\s*End\s*$", output, flags=re.MULTILINE) and "FOAM FATAL" not in output:
            # Check final residuals
            with self._state_lock:
                residuals = dict(self.progress.residuals)
            if not residuals:
                return False
            for field, residual in residuals.items():
                if residual > CONVERGENCE_RESIDUAL_THRESHOLD:
                    return False
            return True
        return False

    def _extract_error_message(self, output: str) -> str:
        """Extract error message from output."""
        # Look for FOAM errors
        foam_error = re.search(
            r'FOAM FATAL ERROR[:\s]*(.*?)(?:From function|$)',
            output,
            re.DOTALL | re.IGNORECASE
        )
        if foam_error:
            return foam_error.group(1).strip()[:ERROR_MESSAGE_MAX_CHARS]

        # Look for other errors
        error_match = re.search(r'error[:\s]+(.*)', output, re.IGNORECASE)
        if error_match:
            return error_match.group(1).strip()[:ERROR_MESSAGE_MAX_CHARS]

        return "未知错误"


def _normalize_solver_log_name(solver: str) -> str:
    """Normalize solver token/path into a safe log filename."""
    token = Path(solver).name.strip()
    if not token:
        token = "solver"
    token = _LOG_FILE_SAFE_NAME_RE.sub("_", token)
    return f"log.{token}"


def persist_solver_log(case_path: str, solver: str, result: RunResult) -> None:
    """Persist solver stdout/stderr as case log file for status/plot tools."""
    case_dir = Path(case_path)
    if not case_dir.exists() or not case_dir.is_dir():
        return

    log_path = case_dir / _normalize_solver_log_name(solver)
    chunks: list[str] = []
    if result.stdout:
        chunks.append(result.stdout.rstrip("\n"))
    if result.stderr:
        if chunks:
            chunks.append("")
        chunks.append("[stderr]")
        chunks.append(result.stderr.rstrip("\n"))
    if not chunks:
        chunks.append(f"# status: {result.status.value}")

    payload = "\n".join(chunks)
    if not payload.endswith("\n"):
        payload += "\n"

    try:
        log_path.write_text(payload, encoding="utf-8")
    except Exception:
        logger.exception("写入求解器日志失败 case_path=%s solver=%s", case_path, solver)


def run_solver(
    case_path: str,
    solver: str,
    timeout: Optional[float] = None,
    on_progress: Optional[Callable[[RunProgress], None]] = None
) -> RunResult:
    """
    Run an OpenFOAM solver.

    Args:
        case_path: Path to the case directory
        solver: Solver name (e.g., "simpleFoam")
        timeout: Maximum execution time in seconds
        on_progress: Callback for progress updates

    Returns:
        RunResult with execution details
    """
    runner = SolverRunner(case_path)
    result = runner.run_command(solver, timeout=timeout, on_progress=on_progress)
    persist_solver_log(case_path, solver, result)
    return result


def run_mesh_generation(case_path: str, timeout: float = 300) -> RunResult:
    """
    Run mesh generation (blockMesh).

    Args:
        case_path: Path to the case directory
        timeout: Maximum execution time in seconds

    Returns:
        RunResult with execution details
    """
    runner = SolverRunner(case_path)
    return runner.run_command("blockMesh", timeout=timeout)


def run_mesh_check(case_path: str, timeout: float = 120) -> RunResult:
    """
    Run mesh quality check (checkMesh).

    Args:
        case_path: Path to the case directory
        timeout: Maximum execution time in seconds

    Returns:
        RunResult with execution details
    """
    runner = SolverRunner(case_path)
    return runner.run_command("checkMesh", timeout=timeout)
