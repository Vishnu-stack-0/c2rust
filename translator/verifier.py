"""
Reliable C <-> Rust differential verification for Windows.

Discovery strategy (in order of preference):
1. Absolute paths via shutil.which + validation.
2. Common installation locations (MinGW, Rustup).
3. Prefer the GNU Rust target when MSVC linker is missing
   (only needs the already-present MinGW gcc).
4. Clear, actionable diagnostics - never claim success when
   a required tool is absent.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

CARGO_TARGET_DIR = Path(
    os.path.expandvars(r"%LOCALAPPDATA%\c2rust_cargo_target")
)

CARGO_TARGET_DIR.mkdir(parents=True, exist_ok=True)


logger = logging.getLogger(__name__)

# Rust failure stages caused by the machine's toolchain, not by the code.
# Repairing the Rust code cannot fix these, so the view must not retry.
ENVIRONMENT_RUST_STAGES = {
    "rust_linker_unavailable",
    "rust_toolchain_missing",
}

# ---------------------------------------------------------------------------
# Compiler / tool discovery
# ---------------------------------------------------------------------------


def _is_executable(path: Optional[str]) -> bool:
    if not path:
        return False
    p = Path(path)
    return p.is_file() and os.access(p, os.X_OK)


def _find_in_path(name: str) -> Optional[str]:
    """Return absolute path if the executable is found and is runnable."""
    path = shutil.which(name)
    if _is_executable(path):
        return path
    return None


def _find_gcc() -> Tuple[Optional[str], str]:
    """
    Locate a working GCC.
    Returns (absolute_path_or_None, diagnostic_message).
    """
    # 1. PATH
    gcc = _find_in_path("gcc")
    if gcc:
        return gcc, f"Found gcc in PATH: {gcc}"

    # 2. Common Windows locations
    candidates = [
        r"C:\MinGW\bin\gcc.exe",
        r"C:\mingw64\bin\gcc.exe",
        r"C:\msys64\mingw64\bin\gcc.exe",
        os.path.expandvars(r"%USERPROFILE%\scoop\apps\mingw\current\bin\gcc.exe"),
        os.path.expandvars(r"%LOCALAPPDATA%\Programs\mingw64\bin\gcc.exe"),
    ]
    for cand in candidates:
        if _is_executable(cand):
            return cand, f"Found gcc at known location: {cand}"

    # 3. Try to locate via where.exe (Windows)
    try:
        r = subprocess.run(
            ["where", "gcc"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if r.returncode == 0:
            for line in r.stdout.splitlines():
                line = line.strip()
                if _is_executable(line):
                    return line, f"Found gcc via where.exe: {line}"
    except Exception:
        pass

    return None, (
        "GCC was not found. "
        "Install MinGW-w64 and ensure its bin directory is on PATH, "
        "or place gcc.exe at C:\\MinGW\\bin\\gcc.exe."
    )


def _find_cargo_and_rustc() -> Tuple[Optional[str], Optional[str], str]:
    """
    Locate cargo and rustc independently.
    Returns (cargo_path, rustc_path, diagnostic).
    """
    cargo = _find_in_path("cargo")
    rustc = _find_in_path("rustc")

    # Fallback to default Rustup location
    rustup_bin = Path(os.path.expandvars(r"%USERPROFILE%\.cargo\bin"))
    if not cargo and (rustup_bin / "cargo.exe").is_file():
        cargo = str(rustup_bin / "cargo.exe")
    if not rustc and (rustup_bin / "rustc.exe").is_file():
        rustc = str(rustup_bin / "rustc.exe")

    if cargo and rustc:
        return cargo, rustc, f"Found cargo={cargo}, rustc={rustc}"
    if not cargo and not rustc:
        return None, None, (
            "Neither cargo nor rustc found. "
            "Install Rust from https://rustup.rs and ensure "
            "%USERPROFILE%\\.cargo\\bin is on PATH."
        )
    if not cargo:
        return None, rustc, "cargo not found (rustc is present)."
    return cargo, None, "rustc not found (cargo is present)."


def _detect_rust_target(rustc: str) -> str:
    """Return the active default host target, e.g. x86_64-pc-windows-msvc."""
    try:
        r = subprocess.run(
            [rustc, "-vV"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        for line in r.stdout.splitlines():
            if line.startswith("host:"):
                return line.split(":", 1)[1].strip()
    except Exception:
        pass
    return "x86_64-pc-windows-msvc"  # safe default on Windows


def _find_msvc_linker() -> Optional[str]:
    """
    Locate the real Microsoft link.exe.
    Explicitly rejects Git's Unix-style link and other non-MSVC tools.
    """
    candidates: List[str] = []

    # PATH first
    link = shutil.which("link")
    if link:
        candidates.append(link)

    # Common VS locations (newest first)
    vs_roots = [
        r"C:\Program Files\Microsoft Visual Studio\2022",
        r"C:\Program Files (x86)\Microsoft Visual Studio\2022",
        r"C:\Program Files\Microsoft Visual Studio\2019",
        r"C:\Program Files (x86)\Microsoft Visual Studio\2019",
    ]
    for root in vs_roots:
        if not os.path.isdir(root):
            continue
        for edition in ("Community", "Professional", "Enterprise", "BuildTools"):
            base = Path(root) / edition / "VC" / "Tools" / "MSVC"
            if base.is_dir():
                versions = sorted(base.iterdir(), reverse=True)
                for v in versions:
                    cand = v / "bin" / "Hostx64" / "x64" / "link.exe"
                    if cand.is_file():
                        candidates.append(str(cand))
                        break

    for cand in candidates:
        # Sanity-check: real MSVC linker prints "Microsoft (R) Incremental Linker"
        try:
            r = subprocess.run(
                [cand],
                capture_output=True,
                text=True,
                timeout=5,
            )
            output = r.stdout + r.stderr
            if "Microsoft" in output and "Incremental Linker" in output:
                return cand
        except Exception:
            continue

    return None


def discover_compilers() -> Dict[str, Any]:
    """
    Discover all required tools and decide the best strategy.
    Returns a dict that is also useful for diagnostics.
    """
    gcc, gcc_msg = _find_gcc()
    cargo, rustc, rust_msg = _find_cargo_and_rustc()

    result: Dict[str, Any] = {
        "gcc": gcc,
        "gcc_message": gcc_msg,
        "cargo": cargo,
        "rustc": rustc,
        "rust_message": rust_msg,
        "msvc_linker": None,
        "rust_target": None,
        "preferred_rust_target": None,
        "strategy": None,
        "errors": [],
    }

    if not gcc:
        result["errors"].append(gcc_msg)
    if not cargo or not rustc:
        result["errors"].append(rust_msg)

    if rustc:
        target = _detect_rust_target(rustc)
        result["rust_target"] = target

        if "msvc" in target:
            linker = _find_msvc_linker()
            result["msvc_linker"] = linker
            if linker:
                result["preferred_rust_target"] = target
                result["strategy"] = "msvc"
            else:
                result["preferred_rust_target"] = "x86_64-pc-windows-gnu"
                result["strategy"] = "gnu-fallback"
                result["errors"].append(
                    "MSVC linker (link.exe) not found. "
                    "Will attempt to build Rust with the GNU target "
                    "(x86_64-pc-windows-gnu), which needs the MinGW gcc "
                    "and the GNU target installed "
                    "(run: rustup target add x86_64-pc-windows-gnu). "
                    "To use the MSVC target permanently, install the "
                    "\"Desktop development with C++\" workload from Visual Studio Build Tools."
                )
        else:
            result["preferred_rust_target"] = target
            result["strategy"] = "gnu"
    else:
        result["strategy"] = "unavailable"

    return result


@lru_cache(maxsize=1)
def discover_compilers_cached() -> Dict[str, Any]:
    """
    Compiler discovery runs external processes (rustc -vV, link.exe, where).
    The results don't change within a server process, so cache them once.
    """
    return discover_compilers()


# ---------------------------------------------------------------------------
# Low-level command runner
# ---------------------------------------------------------------------------


def run_command(
    command: List[str],
    cwd: Optional[str] = None,
    timeout: int = 30,
    input_data: Optional[str] = None,
    env: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """Run a command, capture everything, never raise."""
    try:
        result = subprocess.run(
            command,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
            input=input_data,
            env=env,
        )
        return {
            "returncode": result.returncode,
            "stdout": result.stdout or "",
            "stderr": result.stderr or "",
        }
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout or ""
        stderr = exc.stderr or ""
        if isinstance(stdout, bytes):
            stdout = stdout.decode(errors="replace")
        if isinstance(stderr, bytes):
            stderr = stderr.decode(errors="replace")
        return {
            "returncode": -1,
            "stdout": stdout,
            "stderr": f"Process timed out after {timeout}s.\n{stderr}",
        }
    except FileNotFoundError as exc:
        return {
            "returncode": -1,
            "stdout": "",
            "stderr": f"Executable not found: {exc}",
        }
    except Exception as exc:
        return {
            "returncode": -1,
            "stdout": "",
            "stderr": f"Unexpected error: {exc}",
        }


# ---------------------------------------------------------------------------
# C compile + run
# ---------------------------------------------------------------------------


def compile_and_run_c(
    c_code: str,
    gcc_path: str,
    test_input: Optional[str] = None,
    compile_timeout: int = 30,
    run_timeout: int = 10,
) -> Dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="c2rust_c_") as folder:
        c_file = os.path.join(folder, "program.c")
        exe_file = os.path.join(folder, "program.exe")

        with open(c_file, "w", encoding="utf-8") as f:
            f.write(c_code)

        compile_res = run_command(
            [gcc_path, c_file, "-o", exe_file, "-std=c11", "-Wall"],
            cwd=folder,
            timeout=compile_timeout,
        )
        if compile_res["returncode"] != 0:
            return {
                "compiled": False,
                "stage": "c_compilation",
                **compile_res,
            }

        if not os.path.isfile(exe_file):
            return {
                "compiled": False,
                "stage": "c_compilation",
                "returncode": -1,
                "stdout": "",
                "stderr": "gcc succeeded but executable was not produced.",
            }

        run_res = run_command(
            [exe_file],
            cwd=folder,
            timeout=run_timeout,
            input_data=test_input,
        )
        return {
            "compiled": True,
            "stage": "c_execution",
            **run_res,
        }


# ---------------------------------------------------------------------------
# Rust compile + run
# ---------------------------------------------------------------------------


def _classify_rust_build_failure(build_output: str) -> str:
    """
    Work out whether a failed cargo build is a code problem or an
    environment problem. Only code problems can be fixed by the LLM.
    """
    text = build_output.lower()

    if "linker" in text or "link.exe" in text or "lld-link" in text:
        return "rust_linker_unavailable"

    if (
        "can't find crate" in text
        or "may not be installed" in text
        or "is not installed" in text
        or "rustup target add" in text
    ):
        return "rust_toolchain_missing"

    return "rust_compilation"


def compile_and_run_rust(
    rust_code: str,
    cargo_path: str,
    preferred_target: str,
    test_input: Optional[str] = None,
    compile_timeout: int = 90,
    run_timeout: int = 10,
) -> Dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="c2rust_rs_") as folder:
        src_dir = os.path.join(folder, "src")
        os.makedirs(src_dir, exist_ok=True)

        with open(os.path.join(folder, "Cargo.toml"), "w", encoding="utf-8") as f:
            f.write(
                "[package]\n"
                'name = "translated_program"\n'
                'version = "0.1.0"\n'
                'edition = "2021"\n'
            )

        with open(os.path.join(src_dir, "main.rs"), "w", encoding="utf-8") as f:
            f.write(rust_code)

        build_cmd = [cargo_path, "build", "--target", preferred_target]

        build_res = run_command(
            build_cmd,
            cwd=folder,
            timeout=compile_timeout,
            env={
                **os.environ,
                "CARGO_TERM_COLOR": "never",
                "CARGO_TARGET_DIR": str(CARGO_TARGET_DIR),
            },
        )

        if build_res["returncode"] != 0:
            build_output = build_res["stderr"] + build_res["stdout"]
            stage = _classify_rust_build_failure(build_output)

            return {
                "compiled": False,
                "stage": stage,
                **build_res,
            }

        exe_candidates = [
            os.path.join(
                str(CARGO_TARGET_DIR),
                preferred_target,
                "debug",
                "translated_program.exe",
            ),
            os.path.join(
                str(CARGO_TARGET_DIR),
                "debug",
                "translated_program.exe",
            ),
        ]

        exe_file = next(
            (p for p in exe_candidates if os.path.isfile(p)),
            None,
        )

        if not exe_file:
            return {
                "compiled": False,
                "stage": "rust_compilation",
                "returncode": -1,
                "stdout": build_res["stdout"],
                "stderr": (
                    build_res["stderr"]
                    + "\nExecutable not found after successful cargo build."
                ),
            }

        run_res = run_command(
            [exe_file],
            cwd=folder,
            timeout=run_timeout,
            input_data=test_input,
        )

        return {
            "compiled": True,
            "stage": "rust_execution",
            **run_res,
        }

# ---------------------------------------------------------------------------
# Comparison & status
# ---------------------------------------------------------------------------


def _normalize_output(text: str) -> str:
    """
    Documented normalization policy:
    - Strip trailing whitespace from every line
    - Normalize line endings to \\n
    - Strip a single trailing newline from the whole output
    Numeric values and empty lines are preserved exactly.
    """
    lines = [
        line.rstrip()
        for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    ]
    if lines and lines[-1] == "":
        lines.pop()
    return "\n".join(lines)


def compare_results(
    c_result: Dict[str, Any],
    rust_result: Dict[str, Any],
    discovery: Dict[str, Any],
) -> Dict[str, Any]:
    both_compiled = c_result.get("compiled", False) and rust_result.get("compiled", False)

    c_out = _normalize_output(c_result.get("stdout", ""))
    r_out = _normalize_output(rust_result.get("stdout", ""))
    output_match = both_compiled and c_out == r_out

    exit_code_match = (
        both_compiled
        and c_result.get("returncode") == rust_result.get("returncode")
    )

    rust_stage = rust_result.get("stage")

    # Decide status - never claim success when the environment is broken.
    # Environment problems are checked first: they are not the code's fault.
    if c_result.get("compiled") and rust_stage in ENVIRONMENT_RUST_STAGES:
        status = "verification_environment_error"
    elif discovery.get("errors") and not both_compiled:
        if not c_result.get("compiled"):
            status = "c_compilation_failed"
        elif not rust_result.get("compiled"):
            status = "rust_compilation_failed"
        else:
            status = "verification_environment_error"
    elif not c_result.get("compiled"):
        status = "c_compilation_failed"
    elif not rust_result.get("compiled"):
        status = "rust_compilation_failed"
    elif not both_compiled:
        status = "verification_incomplete"
    elif output_match and exit_code_match:
        status = "passed_for_test_case"
    else:
        status = "output_mismatch"

    return {
        "verification_status": status,
        "c_compiled": bool(c_result.get("compiled")),
        "rust_compiled": bool(rust_result.get("compiled")),
        "rust_stage": rust_stage,
        "output_match": output_match,
        "exit_code_match": exit_code_match,
        "functionally_equivalent_for_test": bool(
            both_compiled and output_match and exit_code_match
        ),
        "c_stdout": c_result.get("stdout", ""),
        "rust_stdout": rust_result.get("stdout", ""),
        "c_stderr": c_result.get("stderr", ""),
        "rust_stderr": rust_result.get("stderr", ""),
        "c_returncode": c_result.get("returncode", -1),
        "rust_returncode": rust_result.get("returncode", -1),
        "discovery": {
            "gcc": discovery.get("gcc"),
            "cargo": discovery.get("cargo"),
            "rustc": discovery.get("rustc"),
            "msvc_linker": discovery.get("msvc_linker"),
            "rust_target": discovery.get("rust_target"),
            "preferred_rust_target": discovery.get("preferred_rust_target"),
            "strategy": discovery.get("strategy"),
            "messages": discovery.get("errors", []),
        },
        "rust_static_check": None,
    }


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def _environment_error_report(
    discovery: Dict[str, Any],
    c_stderr: str,
    rust_stderr: str,
) -> Dict[str, Any]:
    return {
        "verification_status": "verification_environment_error",
        "c_compiled": False,
        "rust_compiled": False,
        "rust_stage": None,
        "output_match": False,
        "exit_code_match": False,
        "functionally_equivalent_for_test": False,
        "c_stdout": "",
        "rust_stdout": "",
        "c_stderr": c_stderr,
        "rust_stderr": rust_stderr,
        "c_returncode": -1,
        "rust_returncode": -1,
        "discovery": discovery,
    }


def verify(
    c_code: str,
    rust_code: str,
    test_input: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Full differential verification pipeline.

    1. Discover compilers (cached per process).
    2. Compile & run C (never execute if compile failed).
    3. Compile & run Rust with the chosen target.
    4. Compare outputs / exit codes under the documented normalization.
    5. Return a complete report with an accurate status.
    """
    discovery = discover_compilers_cached()

    if not discovery["gcc"]:
        return _environment_error_report(
            discovery,
            c_stderr=discovery["gcc_message"],
            rust_stderr=discovery.get("rust_message", ""),
        )

    if not discovery["cargo"] or not discovery["rustc"]:
        return _environment_error_report(
            discovery,
            c_stderr="",
            rust_stderr=discovery["rust_message"],
        )

    c_result = compile_and_run_c(
        c_code,
        gcc_path=discovery["gcc"],
        test_input=test_input,
    )

    preferred = discovery["preferred_rust_target"] or discovery["rust_target"]
    rust_result = compile_and_run_rust(
        rust_code,
        cargo_path=discovery["cargo"],
        preferred_target=preferred,
        test_input=test_input,
    )

    return compare_results(c_result, rust_result, discovery)