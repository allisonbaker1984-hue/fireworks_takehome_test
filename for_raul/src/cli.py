"""CLI entry point. Run with: uv run cli (or python -m src.cli)"""

import sys
from typing import Any, Dict, List

import pandas as pd

from src.agent import TextToSQLAgent, QueryResult
from src.utils import load_db

DB_PATH = "data/Chinook.db"

# Cap how many result rows get printed to the terminal. Some questions can
# legitimately return large result sets (e.g. "list every track"), and
# dumping thousands of rows would work against the "clean and readable"
# terminal experience this CLI is meant to demo.
MAX_DISPLAY_ROWS = 50


def _print_results(rows: List[Dict[str, Any]]) -> None:
    """Pretty-print query results as an aligned table, or a clear empty-result note."""
    if not rows:
        print("(0 rows)")
        return

    df = pd.DataFrame(rows)
    truncated = len(df) > MAX_DISPLAY_ROWS
    display_df = df.head(MAX_DISPLAY_ROWS) if truncated else df

    plural = "" if len(rows) == 1 else "s"
    print(f"Results ({len(rows)} row{plural}):")
    print(display_df.to_string(index=False))
    if truncated:
        print(f"... {len(df) - MAX_DISPLAY_ROWS} more row(s) not shown")


def _print_outcome(result: QueryResult) -> None:
    """
    Print one turn's outcome: the SQL (if the model produced any), then
    exactly one of results / an error / an unanswerable note, per the
    contract of TextToSQLAgent.ask().
    """
    if result.sql:
        print(f"SQL: {result.sql}")
        print()

    if result.unanswerable:
        print("Could not answer this from the current schema -- try rephrasing.")
    elif result.error:
        attempt_word = "attempt" if result.attempts == 1 else "attempts"
        print(f"Query failed after {result.attempts} {attempt_word}: {result.error}")
    else:
        _print_results(result.rows or [])
        attempt_word = "attempt" if result.attempts == 1 else "attempts"
        print(f"\n({result.attempts} {attempt_word}, {result.latency_seconds:.2f}s)")


def main() -> None:
    print(f"Loading database: {DB_PATH}")
    try:
        conn = load_db(DB_PATH)
        agent = TextToSQLAgent(conn)
    except (FileNotFoundError, RuntimeError) as e:
        # Clean, actionable message instead of a raw traceback on the two
        # most likely first-run failures: missing DB file, missing API key.
        print(f"Startup failed: {e}", file=sys.stderr)
        sys.exit(1)

    print(f"Connected. Model: {agent.model}")
    print("Ask a question about the database, or type 'exit'/'quit' to leave.\n")

    try:
        while True:
            try:
                question = input("> ").strip()
            except EOFError:
                # e.g. stdin piped in and now exhausted (Ctrl+D) -- treat
                # the same as an explicit exit rather than crashing.
                print()
                break

            if not question:
                continue
            if question.lower() in ("exit", "quit"):
                break

            print("Generating SQL...")
            try:
                result = agent.ask(question)
            except Exception as e:
                # A single bad turn (network hiccup, malformed API
                # response, etc.) shouldn't kill the whole session --
                # report it and let the user try again.
                print(f"Something went wrong answering that: {e}\n")
                continue

            print()
            _print_outcome(result)
            print()
    except KeyboardInterrupt:
        # Land on a clean new line instead of a raw ^C in the middle of
        # whatever was being printed.
        print()

    print("Goodbye!")


if __name__ == "__main__":
    main()
