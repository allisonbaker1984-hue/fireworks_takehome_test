"""
Run the text-to-SQL agent against every question in data/dev_questions.json
and write dev_answers.json in the same {id: {sql, answer}} shape as
data/dev_answers_example.json.

Usage:
    uv run python generate_dev_answers.py
"""

import json
from pathlib import Path
from typing import Tuple

from src.agent import TextToSQLAgent
from src.utils import load_db

DB_PATH = "data/Chinook.db"
QUESTIONS_PATH = "data/dev_questions.json"
OUTPUT_PATH = "dev_answers.json"


def _answer_for(agent: TextToSQLAgent, question: str) -> Tuple[str, str]:
    """
    Run one question through the agent and return (sql, answer) matching
    the dev_answers.json schema, whatever the outcome -- success, exhausted
    retries, or judged unanswerable.
    """
    result = agent.ask(question)

    if result.unanswerable:
        return "", "Could not be answered from the given database schema."

    if result.error:
        return (
            result.sql,
            f"Query failed after {result.attempts} attempt(s): {result.error}",
        )

    answer = agent.summarize(question, result.rows or [])
    return result.sql, answer


def main() -> None:
    conn = load_db(DB_PATH)
    questions = json.loads(Path(QUESTIONS_PATH).read_text())

    answers = {}

    print(f"Running {len(questions)} dev questions against the agent...\n")
    for i, q in enumerate(questions, start=1):
        qid, question = q["id"], q["question"]
        print(f"[{i}/{len(questions)}] {qid}: {question}")

        # Fresh agent per question -- dev_questions.json entries are
        # independent questions, not a follow-up conversation, so reusing
        # one agent across them would let context from an earlier question
        # bleed into a later answer (same reasoning as scratch_test.py).
        agent = TextToSQLAgent(conn)

        try:
            sql, answer = _answer_for(agent, question)
        except Exception as e:
            # Don't let one bad question abort the whole run -- record the
            # failure and keep going so the output always covers all 10.
            sql, answer = "", f"Generation failed: {e}"

        answers[qid] = {"sql": sql, "answer": answer}
        print(f"    sql:    {sql or '(none)'}")
        print(f"    answer: {answer}\n")

    Path(OUTPUT_PATH).write_text(
        json.dumps(answers, indent=2, ensure_ascii=False) + "\n"
    )
    print(f"Wrote {len(answers)} answers to {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
