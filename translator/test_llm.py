from translator.llm_service import ask_groq

prompt = """
Translate this C program to Rust.
Return only Rust code.

#include <stdio.h>

int main() {
    printf("Hello from C\\n");
    return 0;
}
"""

print(ask_groq(prompt))