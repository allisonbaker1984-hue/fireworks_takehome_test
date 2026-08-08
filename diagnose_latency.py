"""
Diagnose the latency variance observed on kimi-k2p7-code (roughly 2s-28s
call to call in earlier testing). Two hypotheses:

  (a) It's correlated with response length -- the model "thinks"/generates
      more on some calls than others.
  (b) It's connection/infra-level variance unrelated to what's generated --
      e.g. paying fresh TCP/TLS setup cost each time a new OpenAI client is
      created (which is what generate_dev_answers.py / compare_models.py
      currently do, one fresh TextToSQLAgent per question), or Fireworks-
      side queueing that has nothing to do with our request.

Runs the SAME simple question repeatedly:
  - once with a fresh client per call (matches current eval script behavior)
  - once reusing a single client/connection, with message history reset to
    an identical starting state before each call so every trial sends the
    same prompt content over an already-warm connection

...and reports latency alongside token usage for each trial, plus a
latency-vs-completion-tokens correlation if there's any variance to check.

Usage:
    uv run python diagnose_latency.py
"""

import statistics
import time

from src.agent import DEFAULT_MODEL, TextToSQLAgent
from src.utils import load_db

QUESTION = "What is the most popular media type based on number of tracks?"
TRIALS = 8


def run_trial(agent: TextToSQLAgent) -> dict:
    start = time.monotonic()
    sql = agent.generate_sql(QUESTION)
    elapsed = time.monotonic() - start
    return {
        "latency": elapsed,
        "prompt_tokens": agent.total_prompt_tokens,
        "completion_tokens": agent.total_completion_tokens,
        "cached_tokens": agent.total_cached_prompt_tokens,
        "sql_len": len(sql),
    }


def summarize(label: str, trials: list[dict]) -> None:
    latencies = [t["latency"] for t in trials]
    completions = [t["completion_tokens"] for t in trials]

    print(f"\n--- {label} ---")
    for i, t in enumerate(trials, 1):
        print(
            f"  trial {i}: latency={t['latency']:5.2f}s  "
            f"completion_tokens={t['completion_tokens']:4d}  "
            f"prompt_tokens={t['prompt_tokens']:5d} (cached={t['cached_tokens']:5d})"
        )
    print(
        f"  latency: min={min(latencies):.2f}s max={max(latencies):.2f}s "
        f"mean={statistics.mean(latencies):.2f}s stdev={statistics.pstdev(latencies):.2f}s"
    )
    if len(set(completions)) > 1 and len(latencies) > 1:
        corr = statistics.correlation(latencies, completions)
        print(f"  correlation(latency, completion_tokens) = {corr:.2f}")
    else:
        print("  completion_tokens were constant across trials -- output length isn't the variable here")


def main() -> None:
    conn = load_db("data/Chinook.db")
    print(f"Question: {QUESTION!r}")
    print(f"Model: {DEFAULT_MODEL}")

    # Test A: fresh TextToSQLAgent (fresh OpenAI client, fresh connection)
    # per call -- matches how generate_dev_answers.py / compare_models.py
    # actually invoke this today.
    fresh_trials = []
    for _ in range(TRIALS):
        agent = TextToSQLAgent(conn)
        fresh_trials.append(run_trial(agent))
    summarize("Fresh client per call", fresh_trials)

    # Test B: one shared client/connection across all calls, with message
    # history reset to an identical starting state each trial -- isolates
    # per-call connection setup from whatever's happening once a connection
    # is already warm.
    shared_agent = TextToSQLAgent(conn)
    reset_messages = list(shared_agent.messages)
    shared_trials = []
    for _ in range(TRIALS):
        shared_agent.messages = list(reset_messages)
        shared_agent.total_prompt_tokens = 0
        shared_agent.total_completion_tokens = 0
        shared_agent.total_cached_prompt_tokens = 0
        shared_trials.append(run_trial(shared_agent))
    summarize("Shared client, repeated calls", shared_trials)


if __name__ == "__main__":
    main()
