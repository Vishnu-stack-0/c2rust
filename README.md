# C to Rust Translator and Verifier

A Django-based web application that translates C programs into Rust using Groq LLM, Retrieval-Augmented Generation (RAG), ChromaDB, and automatic verification.

## Features

- Translate C source code into Rust
- Retrieve similar C-to-Rust examples using RAG
- Use ChromaDB as the vector knowledge base
- Generate Rust code using the Groq API
- Compile and run both C and Rust programs
- Compare program output and exit codes
- Automatically repair incorrect Rust code
- Display a detailed verification report
- Save translation history in SQLite
- Download the generated Rust file

## Technologies Used

- Python
- Django
- SQLite
- Groq API
- ChromaDB
- Rust
- Cargo
- GCC
- HTML, CSS, JavaScript

## Setup

1. Install dependencies:

```bash
pip install -r requirements.txt
```

2. Create a `.env` file:

```text
GROQ_API_KEY=your_groq_api_key
```

3. Run migrations:

```bash
python manage.py migrate
```

4. Start the server:

```bash
python manage.py runserver
```

5. Open the application:

```text
http://127.0.0.1:8000
```

## How It Works

1. The user submits C code.
2. ChromaDB retrieves similar C-to-Rust examples.
3. Groq generates Rust code using the RAG context.
4. GCC compiles and runs the C program.
5. Cargo compiles and runs the Rust program.
6. The system compares output and exit codes.
7. If verification fails, the LLM repairs the Rust code.
8. The final Rust code and verification report are displayed.

## Important Note

The system verifies functional equivalence for the provided test case. It does not perform formal mathematical equivalence checking for every possible input.