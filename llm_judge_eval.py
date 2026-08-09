"""
LLM-as-judge evaluation: grade dev_answers.json's human-readable answers
against the gold answers in data/dev_questions_with_answers.json, using an
LLM to judge substantive correctness rather than a mechanical value
comparison.

This exists to cover a known gap in results_match (utils.py): 2 of the 10
dev questions are substantively correct but fail value-containment matching
purely because of a name-concatenation formatting difference (see
email_to_raul.md). An LLM judge can recognize that kind of equivalence
directly.

Usage:
    uv run python llm_judge_eval.py
"""

import json
import os
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

from src.agent import DEFAULT_MODEL, FIREWORKS_BASE_URL, judge_answer

load_dotenv()

ANSWERS_PATH = "data/dev_answers.json"
GOLD_PATH = "data/dev_questions_with_answers.json"
OUTPUT_PATH = "llm_judge_results.json"


def main() -> None:
    api_key = os.environ.get("FIREWORKS_API_KEY")
    if not api_key:
        raise RuntimeError("FIREWORKS_API_KEY is not set.")
    client = OpenAI(api_key=api_key, base_url=FIREWORKS_BASE_URL)

    generated = json.loads(Path(ANSWERS_PATH).read_text())
    gold = {q["id"]: q for q in json.loads(Path(GOLD_PATH).read_text())}

    results = {}
    correct_count = 0

    print(f"Judging {len(generated)} answers with model: {DEFAULT_MODEL}\n")
    for qid in sorted(generated.keys()):
        question = gold[qid]["question"]
        reference_answer = gold[qid]["gold_answer"]
        candidate_answer = generated[qid]["answer"]

        verdict = judge_answer(
            client, DEFAULT_MODEL, question, reference_answer, candidate_answer
        )
        correct_count += verdict.correct

        results[qid] = {
            "question": question,
            "reference_answer": reference_answer,
            "candidate_answer": candidate_answer,
            "correct": verdict.correct,
            "reasoning": verdict.reasoning,
        }

        status = "CORRECT" if verdict.correct else "INCORRECT"
        print(f"[{qid}] {status}: {verdict.reasoning}")

    print(f"\nLLM judge: {correct_count}/{len(generated)} correct")

    Path(OUTPUT_PATH).write_text(json.dumps(results, indent=2, ensure_ascii=False) + "\n")
    print(f"Wrote full verdicts to {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
