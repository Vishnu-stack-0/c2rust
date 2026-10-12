import logging
import os
import re

import requests
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

GROQ_API_KEY = os.getenv("GROQ_API_KEY")
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"

# Set GROQ_MODEL in .env to override. Use the exact model ID from your Groq dashboard.
MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-20b")

# Seconds to wait for a single Groq response.
REQUEST_TIMEOUT = int(os.getenv("GROQ_TIMEOUT", "90"))

SYSTEM_PROMPT = (
    "You are an expert C-to-Rust translator. "
    "Return only complete Rust code. "
    "Do not include explanations. "
    "Do not wrap the code in Markdown code fences."
)

_FENCED_BLOCK_RE = re.compile(
    r"```(?:rust|rs)?[ \t]*\r?\n(.*?)```",
    re.DOTALL | re.IGNORECASE,
)


def strip_code_fences(text: str) -> str:
    """
    Remove Markdown code fences from model output.

    Models often wrap code in ```rust ... ``` even when told not to.
    Those backticks are not valid Rust and break compilation.
    """
    if not text:
        return ""

    match = _FENCED_BLOCK_RE.search(text)
    if match:
        return match.group(1).strip()

    # Handle an opening fence with no closing fence.
    cleaned = re.sub(
        r"^\s*```(?:rust|rs)?[ \t]*\r?\n",
        "",
        text,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(r"\r?\n?```\s*$", "", cleaned)

    return cleaned.strip()


def ask_groq(prompt: str) -> str:
    if not GROQ_API_KEY:
        raise ValueError(
            "GROQ_API_KEY is missing. "
            "Add it to your .env file."
        )

    response = requests.post(
        GROQ_URL,
        headers={
            "Authorization": f"Bearer {GROQ_API_KEY}",
            "Content-Type": "application/json",
        },
        json={
            "model": MODEL,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.1,
        },
        timeout=REQUEST_TIMEOUT,
    )

    response.raise_for_status()

    content = response.json()["choices"][0]["message"]["content"]

    if not content or not content.strip():
        raise ValueError("Groq returned an empty response.")

    return strip_code_fences(content)


def translate_c_to_rust(c_code: str) -> str:
    prompt = f"""
Translate the following C program into safe, idiomatic Rust.

Rules:
1. Preserve observable behaviour.
2. Use safe Rust.
3. Avoid unsafe unless necessary.
4. Keep the same output format.
5. Return only Rust code.

C code:
{c_code}
"""

    return ask_groq(prompt)


def translate_c_to_rust_with_context(
    c_code: str,
    rag_context: str,
) -> str:
    prompt = f"""
You are translating C code to Rust.

Use the following similar examples and rules as guidance:

{rag_context}

Now translate this C program into safe, idiomatic Rust.

Rules:
1. Preserve observable behaviour.
2. Use safe Rust.
3. Avoid unsafe unless necessary.
4. Keep the same output format.
5. Return only Rust code.

C code:
{c_code}
"""

    return ask_groq(prompt)