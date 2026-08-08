"""
Compare candidate Fireworks models on the 10 dev questions: accuracy (both
mechanical results_match AND an LLM-as-judge pass), latency, how often the
retry loop fired, and estimated cost.

Usage:
    uv run python compare_models.py
"""

import json
from pathlib import Path
from typing import Any, Dict

from src.agent import DEFAULT_MODEL, TextToSQLAgent, judge_answer
from src.utils import load_db, results_match

# Model used to judge every candidate's answers, held constant across the
# whole comparison so the judge itself isn't a variable between rows in the
# table. Uses our own shipped default rather than a separate "grader" model
# -- it already proved reliable as a judge in llm_judge_eval.py (10/10
# agreement with manual review on dev_answers.json).
JUDGE_MODEL = DEFAULT_MODEL

DB_PATH = "data/Chinook.db"
QUESTIONS_PATH = "data/dev_questions.json"
GOLD_PATH = "data/dev_questions_with_answers.json"
OUTPUT_PATH = "model_comparison.json"

# Per-1M-token serverless pricing, pulled from each model's page on
# https://fireworks.ai/models at the time this comparison was run. Fireworks'
# catalog and pricing both change over time -- confirm current pricing
# before relying on these numbers for a final cost commitment to a customer.
MODELS = {
    "accounts/fireworks/models/kimi-k2p7-code": {
        "input": 0.95,
        "cached_input": 0.19,
        "output": 4.00,
    },
    "accounts/fireworks/routers/kimi-k2p7-code-fast": {
        "input": 1.90,
        "cached_input": 0.38,
        "output": 8.00,
    },
    # Genuinely different model families (not just another serving tier of
    # the same weights), added to check whether Kimi is actually the best
    # available option here or just the only one we'd tried. Both are
    # dramatically cheaper per-token than either Kimi variant above.
    "accounts/fireworks/models/gpt-oss-120b": {
        "input": 0.15,
        "cached_input": 0.014,
        "output": 0.60,
    },
    "accounts/fireworks/models/deepseek-v4-flash": {
        "input": 0.14,
        "cached_input": 0.028,
        "output": 0.28,
    },
}


def estimate_cost_usd(
    prompt_tokens: int, cached_tokens: int, completion_tokens: int, pricing: Dict[str, float]
) -> float:
    """Blend cached vs. uncached input pricing with output pricing for one call/session."""
    uncached_tokens = max(prompt_tokens - cached_tokens, 0)
    total = (
        uncached_tokens * pricing["input"]
        + cached_tokens * pricing["cached_input"]
        + completion_tokens * pricing["output"]
    )
    return total / 1_000_000


def run_one_model(
    model: str, pricing: Dict[str, float], conn, questions, gold
) -> Dict[str, Any]:
    per_question = []

    for i, q in enumerate(questions, start=1):
        qid, question = q["id"], q["question"]
        # Fresh agent per question -- see generate_dev_answers.py for why.
        agent = TextToSQLAgent(conn, model=model)

        try:
            result = agent.ask(question)
            correct = (
                not result.error
                and not result.unanswerable
                and results_match(gold[qid]["expected_result"], result.rows or [])
            )

            # Judge on the raw executed rows against the gold prose answer,
            # rather than routing through summarize() first -- this is
            # testing whether the SQL got the right data, not the quality
            # of a separate summarization step (dev_answers.json's
            # LLM-judge pass already covers that layer).
            if result.rows:
                verdict = judge_answer(
                    agent.client,
                    JUDGE_MODEL,
                    question,
                    gold[qid]["gold_answer"],
                    json.dumps(result.rows, default=str),
                )
                llm_judge_correct = verdict.correct
                llm_judge_reasoning = verdict.reasoning
            else:
                llm_judge_correct = False
                llm_judge_reasoning = "No valid results to judge."

            record = {
                "id": qid,
                "sql": result.sql,
                "attempts": result.attempts,
                "latency_seconds": result.latency_seconds,
                "error": result.error,
                "unanswerable": result.unanswerable,
                "correct": correct,
                "llm_judge_correct": llm_judge_correct,
                "llm_judge_reasoning": llm_judge_reasoning,
                "prompt_tokens": agent.total_prompt_tokens,
                "cached_prompt_tokens": agent.total_cached_prompt_tokens,
                "completion_tokens": agent.total_completion_tokens,
                "estimated_cost_usd": estimate_cost_usd(
                    agent.total_prompt_tokens,
                    agent.total_cached_prompt_tokens,
                    agent.total_completion_tokens,
                    pricing,
                ),
            }
        except Exception as e:
            # Don't let one bad question abort the whole comparison run.
            record = {
                "id": qid,
                "sql": "",
                "attempts": 0,
                "latency_seconds": 0.0,
                "error": str(e),
                "unanswerable": False,
                "correct": False,
                "llm_judge_correct": False,
                "llm_judge_reasoning": f"Skipped due to error: {e}",
                "prompt_tokens": 0,
                "cached_prompt_tokens": 0,
                "completion_tokens": 0,
                "estimated_cost_usd": 0.0,
            }

        per_question.append(record)
        mech = "OK  " if record["correct"] else "MISS"
        judged = "OK  " if record["llm_judge_correct"] else "MISS"
        print(
            f"  [{i}/{len(questions)}] {qid}: mechanical={mech} judge={judged}  "
            f"{record['attempts']} attempt(s), {record['latency_seconds']:.2f}s, "
            f"${record['estimated_cost_usd']:.5f}"
        )

    n = len(per_question)
    latencies = sorted(r["latency_seconds"] for r in per_question)
    total_cost = sum(r["estimated_cost_usd"] for r in per_question)

    summary = {
        "accuracy": sum(r["correct"] for r in per_question) / n,
        "llm_judge_accuracy": sum(r["llm_judge_correct"] for r in per_question) / n,
        "avg_latency_seconds": sum(latencies) / n,
        "p50_latency_seconds": latencies[n // 2],
        "max_latency_seconds": latencies[-1],
        "avg_attempts": sum(r["attempts"] for r in per_question) / n,
        "total_cost_usd_for_10_questions": total_cost,
        "avg_cost_usd_per_question": total_cost / n,
    }
    return {"summary": summary, "per_question": per_question}


def main() -> None:
    conn = load_db(DB_PATH)
    questions = json.loads(Path(QUESTIONS_PATH).read_text())
    gold = {q["id"]: q for q in json.loads(Path(GOLD_PATH).read_text())}

    all_results = {}
    for model, pricing in MODELS.items():
        print("=" * 100)
        print(f"MODEL: {model}")
        print("=" * 100)

        all_results[model] = run_one_model(model, pricing, conn, questions, gold)

        s = all_results[model]["summary"]
        print("-" * 100)
        print(
            f"Accuracy (mechanical): {s['accuracy']*100:.0f}%  |  "
            f"Accuracy (LLM judge): {s['llm_judge_accuracy']*100:.0f}%  |  "
            f"Avg latency: {s['avg_latency_seconds']:.2f}s  |  "
            f"P50 latency: {s['p50_latency_seconds']:.2f}s  |  "
            f"Max latency: {s['max_latency_seconds']:.2f}s  |  "
            f"Avg attempts: {s['avg_attempts']:.2f}  |  "
            f"Cost (10 q): ${s['total_cost_usd_for_10_questions']:.4f}"
        )
        print()

    Path(OUTPUT_PATH).write_text(json.dumps(all_results, indent=2, default=str) + "\n")
    print(f"Wrote full comparison to {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
