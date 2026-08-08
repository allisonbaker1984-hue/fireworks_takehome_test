# Text-to-SQL CLI

An interactive terminal tool that converts natural-language questions into SQL, runs them against your database, and returns the results. Built on Fireworks-hosted open-source models.

## Prerequisites

- [`uv`](https://github.com/astral-sh/uv) (Python package/environment manager)
- `curl` and `sqlite3` on your PATH (only needed for the sample-database setup step below, not required once you're pointing the CLI at your own database)
- A Fireworks AI API key (sign up and grab a key at [fireworks.ai](https://fireworks.ai))

## Setup

1. **Open a terminal in this folder.** After unzipping, navigate into the folder that contains this README (the same folder as `pyproject.toml` and `setup.sh`):
   ```bash
   cd path/to/this-folder
   ```
   Every command below assumes you're running it from here.

2. **Install dependencies:**
   ```bash
   uv sync
   ```

3. **Set your Fireworks API key** as an environment variable:
   ```bash
   export FIREWORKS_API_KEY=your_key_here
   ```
   Alternatively, copy `.env.example` to `.env` and fill in your key there; it's loaded automatically.

4. **(This demo only) Download the sample database:**
   ```bash
   ./setup.sh
   ```
   This fetches the Chinook sample database (a digital music store) into `data/Chinook.db`. To point the CLI at a different SQLite database instead, skip this step, see "Using your own database" below.

## Required environment variables

| Variable | Required | Description |
|---|---|---|
| `FIREWORKS_API_KEY` | Yes | Your Fireworks AI API key. |
| `FIREWORKS_MODEL` | No | Overrides the default model (`accounts/fireworks/models/gpt-oss-120b`). Only needed if you want to test a different Fireworks model. |

## Running the CLI

```bash
uv run cli
```

(or, with the environment manually activated: `python -m src.cli`)

This launches an interactive session:

```
Loading database: data/Chinook.db
Connected. Model: accounts/fireworks/models/gpt-oss-120b
Ask a question about the database, or type 'exit'/'quit' to leave.

> What are the top 5 best-selling genres by total sales?
Generating SQL...

SQL: SELECT g.Name AS Genre, SUM(il.UnitPrice * il.Quantity) AS TotalSales ...

Results (5 rows):
             Genre  TotalSales
              Rock      826.65
             Latin      382.14
             Metal      261.36
Alternative & Punk      241.56
          TV Shows       93.53

(1 attempt, 1.32s)
```

- Ask follow-up questions in the same session (for example, "now sort that by country"); conversation context carries forward.
- Type `exit` or `quit` (case-insensitive) to end the session, or press Ctrl+C / Ctrl+D at any time.

## Using your own database

The CLI works against any SQLite database. It introspects the schema live rather than relying on anything hardcoded for the sample data. To point it at your own database, change `DB_PATH` in `src/cli.py` to your database file's path. (A `--db` command-line flag is a natural next step if this becomes a recurring workflow, flagged in the accompanying email as a near-term follow-up.)

## What's included

- `src/`: the CLI and agent implementation
- `data/dev_questions.json`: the 10 development questions this system was validated against
- `dev_answers.json`: this system's outputs (generated SQL and a human-readable answer) for each of those 10 questions
- `email_to_raul.md`: a summary of what was built, how it performed, how it was validated, and next steps
