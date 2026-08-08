**Subject: Text-to-SQL PoC Update**

*This PoC was built with the help of Claude Code for implementation and Claude for design discussion and code review throughout.*

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

All 10 are correct in substance, and we checked this two different ways rather than taking our own word for it. A mechanical comparison (matching query results by value, tolerant of extra or differently named columns) shows 8 of 10 as exact matches; the remaining 2 differ only in that our SQL returns first and last name as separate columns where the gold query concatenates them into a single string. Same underlying data, different shape. Rather than rely on our own read of "these are basically the same," we ran those 2 (and the other 8, as a check) through a second, independent evaluation: an LLM-as-judge pass that compares each generated answer against the reference answer for substantive correctness rather than exact value matching. That pass returned 10 of 10 correct, including both cases the mechanical check flagged. We are keeping both numbers in view rather than reporting only the more favorable one: 8 of 10 is what naive value-matching finds, 10 of 10 is what a semantic-equivalence check finds, and the gap between them is itself a useful reminder of how brittle simple SQL-result grading can be at scale.

A few other points we validated directly rather than assumed:

- **Retry and self-correction.** We deliberately forced an execution failure and an attempted write statement in testing, and confirmed the agent catches both, feeds the error back to the model, and produces a corrected query on the next attempt.
- **Prompt caching.** On a freshly started session's first call, 1,405 of 1,419 prompt tokens were served from cache, direct evidence the caching design is working as intended rather than being a theoretical benefit.
- **Read-only enforcement.** Generated SQL is validated as a single read-only SELECT statement before it ever reaches the database, blocking statement-stacking tricks and writes disguised behind a CTE, on top of the database connection itself never being granted write access.

## Model Comparison and the Latency Issue

Our first comparison only tested `kimi-k2p7-code` against its own "fast" serving tier: the same underlying model on a different serving tier, not two distinct models. That told us how to serve Kimi well, but not whether Kimi was the right choice to begin with. We broadened the comparison to four models, including two genuinely different model families, before finalizing a recommendation.

| Model | Mechanical Accuracy | LLM-Judge Accuracy | Avg Latency | P50 Latency | Max Latency | Cost / 10 Questions | Cost / Query |
|---|---|---|---|---|---|---|---|
| kimi-k2p7-code | 8/10 | 10/10 | 4.68s | 5.17s | 8.48s | $0.0149 | $0.0015 |
| kimi-k2p7-code-fast | 7/10 | 9/10 | 1.51s | 1.56s | 2.43s | $0.0217 | $0.0022 |
| gpt-oss-120b | 8/10 | 10/10 | 1.60s | 1.73s | 2.72s | $0.0037 | $0.0004 |
| deepseek-v4-flash | 8/10 | 10/10 | 6.55s | 5.81s | 26.42s | $0.0026 | $0.0003 |

The LLM-judge column is a second, independent evaluation pass: rather than the mechanical results_match check comparing query results by value, we have a model judge whether each generated answer is substantively correct against the reference answer. It is not a rubber stamp. In this run it caught a real bug, not just a formatting quirk: `kimi-k2p7-code-fast` used an inner join between Playlist and PlaylistTrack on the "tracks per playlist" question, which silently drops any playlist with zero tracks from the results. Both the mechanical check and the judge flagged it correctly, which is exactly the kind of failure a customer-facing tool cannot afford to miss. That single miss is also why `kimi-k2p7-code-fast`'s judged accuracy (9/10) is lower than the other three models (10/10 each) in this run.

`gpt-oss-120b` is the strongest overall result: judged accuracy tied for the best of the four, latency comfortably under your 3-second target, and by far the lowest cost among the models that actually meet that latency bar. `deepseek-v4-flash` is worth flagging as unreliable despite being cheapest per query: it hit a 26.42-second outlier on a single question in this run, more than eight times its own average. We ran this comparison twice, and the latency rankings shifted meaningfully between runs for every model except `gpt-oss-120b`, which stayed consistently fast and consistently accurate both times. That is a useful data point in `gpt-oss-120b`'s favor, and also a reminder that 10 questions, run once or twice, is not enough data to fully trust any single latency number at your production volume.

Separately from the model comparison, we also wanted to understand whether the latency variance we first saw was something in our own design or a genuine model or infrastructure difference. We ran a controlled test, firing the same question repeatedly: once with a fresh connection each time, and once reusing a single warm connection. Two findings came out of that. Response length was not the cause; latency showed essentially no correlation with completion token count. And connection reuse reduced but did not eliminate the variance, still leaving a greater than 2x spread on functionally identical requests against the base Kimi model. That points to serving-side variance rather than anything fixable in our own request handling.

**Recommendation:** ship with `gpt-oss-120b`. It tied for the best judged accuracy, stayed comfortably under your latency target in both comparison runs while every other model's ranking shifted meaningfully between runs, and costs substantially less than either Kimi variant. It is a stronger, more consistent result than our initial recommendation, which is why we went back and broadened the comparison rather than stopping at an acceptable answer.

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
