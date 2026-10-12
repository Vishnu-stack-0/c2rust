from .llm_service import ask_groq


def repair_rust_code(c_code, rust_code, problem):
    prompt = f"""
You previously translated a C program into Rust.

The Rust program has a problem.

C program:
{c_code}

Current Rust program:
{rust_code}

Problem:
{problem}

Fix the Rust program.

Rules:
1. Preserve the C program's observable behaviour.
2. Use safe Rust wherever possible.
3. Keep the same output format.
4. Return only the complete corrected Rust code.
5. Do not include explanations.
"""

    return ask_groq(prompt)


def build_compile_problem(compile_result):
    return f"""
The Rust program failed to compile.

Compiler stage: {compile_result.get("stage", "unknown")}

Compiler stderr:
{compile_result.get("stderr", "")}

Compiler stdout:
{compile_result.get("stdout", "")}
"""


def build_output_mismatch_problem(report):
    return f"""
The Rust program compiled successfully,
but its output does not match the C program.

C stdout:
{report.get("c_stdout", "")}

Rust stdout:
{report.get("rust_stdout", "")}

C exit code:
{report.get("c_returncode", "")}

Rust exit code:
{report.get("rust_returncode", "")}

C stderr:
{report.get("c_stderr", "")}

Rust stderr:
{report.get("rust_stderr", "")}
"""