**Subject: Text-to-SQL PoC Update**

Hi Raul,

Here is a full update on the text-to-SQL proof of concept: what we built, how it performed against your test questions, and what we would tackle next.

## What We Built

Starting from your baseline prompt ("Convert this question to SQL: {question}"), we made several changes aimed directly at the three problems you flagged: accuracy, latency, and cost.

- **Schema-aware prompting.** The baseline prompt gives the model no information about the database, which is why it was hallucinating tables and getting joins wrong. We now pull the database's own CREATE TABLE statements, including foreign keys, and inject them into the prompt so the model knows how your tables actually relate rather than guessing. This is introspected live from whatever database is connected, so it works the same way for any customer schema, not just our sample data.
- **Structured outputs instead of free text.** Rather than asking the model to write prose and hoping to extract a SQL block from it, we constrain its response to a strict JSON schema using Fireworks' schema-constrained decoding. This eliminates an entire class of failure where the SQL was correct but our own parsing broke.
- **A self-correcting execution loop.** Generated SQL is actually executed against the database. If it fails, we feed the exact error back to the model and let it retry, up to three attempts, before surfacing a clear failure to the user rather than failing silently or retrying indefinitely.
- **Prompt caching, built in from the start.** Fireworks caches repeated prompt prefixes, so we designed for it deliberately: the schema and instructions are built once per session and stay identical across every turn, with the user's question appended at the end. This lets repeat turns reuse the cached prefix instead of reprocessing the full schema each time, and cached tokens are billed at a steep discount (roughly 80% off list price on the model we tested). We also pass a consistent session identifier on each request so repeat calls route to the same backend replica; without that, the cache can miss even when the prompt itself is unchanged. We measured this directly rather than assuming it (more below), and at your projected volume this is a real lever on both latency and cost.
- **Follow-up questions.** Conversation history carries forward within a session, so a question like "now sort that by country" works without the user repeating context.

## Performance Against the 10 Dev Questions

We ran all 10 dev questions and validated the generated SQL's actual executed results against your gold answers, not just whether the SQL looked reasonable.

All 10 are correct in substance. An automated comparison (matching on values, tolerant of extra or differently named columns) shows 8 of 10 as exact matches. The remaining 2 are correct on manual review as well; the difference is that our SQL returns first and last name as separate columns where the gold query concatenates them into a single string. Same underlying data, different shape, not an incorrect answer. We are calling this out explicitly rather than rounding up silently to 10 of 10, since it reflects a real limitation in automated SQL-result grading worth keeping in mind as this scales.

A few points we validated directly rather than assumed:

- **Retry and self-correction.** We deliberately forced an execution failure and an attempted write statement in testing, and confirmed the agent catches both, feeds the error back to the model, and produces a corrected query on the next attempt.
- **Prompt caching.** On a freshly started session's first call, 1,405 of 1,419 prompt tokens were served from cache, direct evidence the caching design is working as intended rather than being a theoretical benefit.
- **Read-only enforcement.** Generated SQL is validated as a single read-only SELECT statement before it ever reaches the database, blocking statement-stacking tricks and writes disguised behind a CTE, on top of the database connection itself never being granted write access.

## Model Comparison and the Latency Issue

Our first comparison only tested `kimi-k2p7-code` against its own "fast" serving tier: the same underlying model on a different serving tier, not two distinct models. That told us how to serve Kimi well, but not whether Kimi was the right choice to begin with. We broadened the comparison to four models, including two genuinely different model families, before finalizing a recommendation.

| Model | Accuracy | Avg Latency | P50 Latency | Max Latency | Cost / 10 Questions | Cost / Query |
|---|---|---|---|---|---|---|
| kimi-k2p7-code | 8/10 (10/10 in substance) | 3.74s | 3.69s | 7.26s | $0.0163 | $0.0016 |
| kimi-k2p7-code-fast | 8/10 (10/10 in substance) | 1.82s | 1.93s | 3.30s | $0.0248 | $0.0025 |
| gpt-oss-120b | 8/10 (10/10 in substance) | 1.75s | 1.32s | 3.75s | $0.0037 | $0.0004 |
| deepseek-v4-flash | 8/10 (10/10 in substance) | 4.28s | 4.39s | 6.73s | $0.0027 | $0.0003 |

Accuracy was identical across all four models, with the same 8 of 10 exact matches and the same 2 substantively correct mismatches every time, which confirms this is a grading artifact rather than a real quality gap between models. `gpt-oss-120b` outperformed `kimi-k2p7-code-fast` on every dimension that matters here: a better P50 (1.32s versus 1.93s), equivalent accuracy, and roughly 85% lower cost (an estimated $333 per month versus $2,232 per month at your projected volume of 30,000 queries per day). One caveat worth noting honestly: its max latency (3.75s) was marginally higher than `kimi-k2p7-code-fast`'s (3.30s) in this run. That is a single data point on each side, so we are not treating it as settled, and would want to confirm it holds with a larger sample. `deepseek-v4-flash` was the cheapest per query but had the worst latency of the four (P50 4.39s) and would not have met your sub-3-second target in this test.

Before selecting a serving tier, we also wanted to determine whether the original latency variance was something in our own design or a genuine model or infrastructure difference. We ran a controlled test, firing the same question repeatedly: once with a fresh connection each time, and once reusing a single warm connection. Two findings came out of that. Response length was not the cause; latency showed essentially no correlation with completion token count. And connection reuse reduced but did not eliminate the variance, still leaving a greater than 2x spread on functionally identical requests against the base Kimi model. That points to serving-side variance on its standard tier rather than anything fixable in our own request handling.

**Recommendation:** ship with `gpt-oss-120b`. It matches the accuracy of every other model tested, delivers the best P50 latency of the four, and costs substantially less. It is a stronger result on every dimension you asked us to optimize for than our initial recommendation, which is why we went back and broadened the comparison rather than stopping at an acceptable answer.

## A Limitation Worth Flagging Directly

Our schema-injection approach uses the database's own table definitions but deliberately excludes sample data values, a reasonable scope decision for this phase. It does introduce a real failure mode, however: questions that depend on exact string matching can fail silently if the phrasing does not match how the data is actually stored. In this dataset specifically, a question phrased around "United States" would generate a filter for that exact string and return zero rows, because the database stores "USA." The model has no way to know that from the schema alone; it is guessing.

For production, we would want a governed way to expose representative values per column, ideally sourced from curated metadata rather than sampling a customer's live tables at query time. This matters both for correctness (someone should own what "representative" means for a given column) and for governance (raw customer data should not be flowing into prompts by default). This is the same problem Databricks' Unity Catalog and Genie solve with column comments and curated sample values rather than direct table access, and we think that is the right pattern to follow here rather than reinventing it.

## What We'd Tackle Next

1. **Ship `gpt-oss-120b`** as the default, and keep the `FIREWORKS_MODEL` override in place so switching is a one-line change if your own validation says otherwise. We would also want to rerun this comparison against a larger question set before treating it as fully settled; 10 questions is enough to catch a significant problem, which is how we caught the original latency issue, but it is a thin basis for a confident production decision at 30,000 queries per day.
2. **Build a governed column-value metadata layer** to close the exact-string-matching gap before this goes near a real customer schema.
3. **Scope model fine-tuning and distillation as a phase two**, once there is real usage to learn from. At your projected volume, you would accumulate a large set of question, schema, generated SQL, and execution-outcome examples quickly. Distilling a frontier model's SQL quality, or a curated set of confirmed-correct queries, into a small model fine-tuned specifically for your schema could reduce cost and latency further without sacrificing accuracy. Not something to build within this exercise's scope, but a strong next step once there is production traffic to learn from.

Happy to walk through the code and design decisions whenever works for you.

Best,
[Your name]
