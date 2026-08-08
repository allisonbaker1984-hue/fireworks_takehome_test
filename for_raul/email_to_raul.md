**Subject: Text-to-SQL CLI — PoC Update and Early Results**

Hi Raul,

Quick update on the text-to-SQL PoC ahead of [final delivery] — here's what we've built, what we've validated so far, and where we're headed next.

## What we built

Starting from your baseline prompt (`Convert this question to SQL: {question}`), we made four changes aimed directly at the three problems you flagged (accuracy, latency, cost):

- **Schema-aware prompting.** The baseline prompt gives the model zero information about your database, which is the direct cause of the hallucinated tables/columns and wrong JOINs you saw. We introspect the live database's own `CREATE TABLE` statements — including primary and foreign key constraints — and inject them into the system prompt, so the model is told exactly how your tables relate instead of guessing. This is derived automatically from whatever database is connected, so it works the same way for any customer schema, not just the sample data — matching the "point it at a connection string" experience you described.
- **Structured outputs, not free-text parsing.** Rather than asking for prose and hoping to extract a SQL block out of it, we constrain the model's response to a strict JSON schema (`{"sql": "..."}`) using Fireworks' schema-constrained decoding. This removes an entire failure mode: "the SQL was actually fine, but our parser choked on how it was wrapped."
- **Self-correcting execution loop.** Generated SQL is executed against the real database. If it errors, we feed the exact database error back to the model and let it retry (capped at 3 attempts total) before surfacing a clear failure to the user, rather than either silently failing or retrying forever.
- **Prompt-prefix caching, architected in from the start.** Fireworks supports prompt caching, so we built around it deliberately: the schema and system instructions are constructed once per session and kept byte-identical across every turn, with the user's question appended at the end rather than mixed in. This lets repeated turns in a session reuse the cached prefix instead of reprocessing the full schema every time — directly targeting both latency (fewer tokens to actually process per turn) and cost (cached input tokens are billed at a steep discount on Fireworks' serverless tier — 80% off list price for the model we're testing with, not just the ~50% default). To fully realize this, we also pass a consistent session identifier on each request so repeat calls route to the same backend replica; without it, the cache can miss silently even when the prompt itself is unchanged. We've since measured this directly (see below) rather than just trusting the design — at GitLab's projected scale (~30K queries/day) this is a meaningful, now-verified lever on both your latency and unit-economics concerns.

The CLI also preserves conversation context, so follow-up questions ("now sort that by country") work without the user repeating themselves.

## What we've validated so far

We ran all 10 dev questions end-to-end (`generate_dev_answers.json` → `dev_answers.json`) and checked the generated SQL's *actual executed results* against the gold answers, not just whether it looked plausible:

- **10/10 substantively correct.** An automated results-comparison (matching on values, tolerant of extra/differently-named columns) shows 8/10 as an exact match; the other 2 (which employee has the most customers; top 5 customers by spend) are correct in substance on manual review — the only difference is our SQL returns first/last name as separate columns where the gold query concatenates them into one string. Same underlying data, different shape, not a wrong answer. We're calling this out explicitly rather than rounding up to "10/10" silently, since it's a real limitation of naive automated SQL-result grading worth remembering as this scales.
- **Retry/self-correction is working, not just theoretical.** We forced both an execution failure and an attempted write statement in testing and confirmed the agent catches each, feeds the error back to the model, and gets a corrected query on the next attempt — capped at 3 attempts total so a persistently-wrong query can't spiral.
- **Prompt caching is measurably working, not just designed to.** On a freshly started session (first call, cold by definition) we saw 1,405 of 1,419 prompt tokens (~99%) served from cache rather than reprocessed — direct evidence the fixed-prefix system prompt design is paying off, not just a theoretical benefit.
- **Read-only enforcement**: generated SQL is validated as a single read-only SELECT statement before it's ever handed to the database — rejecting statement-stacking tricks (`SELECT 1; DROP TABLE ...`) and write statements smuggled behind a CTE, on top of the database connection itself only ever being queried, never given write access.

## Model comparison, and the latency problem we found (and chased down)

We ran all 10 dev questions against two candidates: `kimi-k2p7-code` (Fireworks' code-specialized model, our initial default) and its `-fast` router variant.

| Model | Accuracy | Avg latency | **P50 latency** | Max latency | Cost / 10 questions |
|---|---|---|---|---|---|
| `kimi-k2p7-code` | 8/10 (10/10 substantively — see above) | 4.74s | **3.32s** | 14.12s | $0.0148 |
| `kimi-k2p7-code-fast` | 8/10 (10/10 substantively) | 1.86s | **1.80s** | 4.02s | $0.0305 |

Accuracy is identical between the two — same questions, same 2 substantively-correct-but-differently-shaped results either way. Latency is not: the base model's P50 sits right at your <3s target with a rough tail (14s max), while `-fast` comes in well under it with a much tighter spread.

We didn't want to just report that gap — we wanted to know whether it was something in *our* design or genuinely a model/infrastructure difference, so we ran a controlled test: the same question, fired repeatedly, once creating a fresh connection each time (matching how our eval scripts call the agent today) and once reusing a single warm connection. Two findings:

1. **Response length isn't the cause.** Latency showed essentially zero correlation with completion token count (r ≈ 0.01–0.15) — the model isn't "thinking longer" on the slow calls.
2. **Connection reuse helps, but doesn't close the gap.** A warm, reused connection cut latency variance roughly in half (stdev 2.03s → 0.79s) but still left a 2x+ spread (1.85s–4.14s) on functionally identical requests. That points to serving-side variance on the base model's standard tier, not something fixable in our request handling.

**Recommendation: ship with `kimi-k2p7-code-fast`.** Same accuracy, comfortably under your P50 target with a much more predictable tail, at roughly double the (already negligible) per-query cost — at 30K queries/day that's the difference between ~$45/day and ~$90/day, both trivial next to the GPT-5.4 baseline that made the unit economics not work in the first place.

## A known limitation worth naming explicitly

Our schema-injection approach uses the database's own `CREATE TABLE` statements (structure and types) but deliberately doesn't include sample data values. That's a reasonable scope call for a time-boxed PoC, but it has a real failure mode: questions that hinge on exact string matching can fail silently if the phrasing doesn't match how the data is actually stored. Concretely, in this dataset: a question phrased with "United States" would generate `WHERE Country = 'United States'` and return **zero rows**, because the database actually stores `'USA'`. The model has no way to know that from the schema alone — it's guessing.

For production, we'd want a governed way to expose representative values per column — ideally sourced from curated metadata rather than live-sampling a customer's tables at query time (both for correctness — someone should own what "representative" means for a column — and because live-sampling raw customer data into prompts raises its own governance questions). This is the same class of problem Databricks' Unity Catalog / Genie solves with column comments and curated sample values rather than direct table access, and we think that's the right model to follow here rather than reinventing it.

## What's next

1. **Ship `kimi-k2p7-code-fast`** as the default per the comparison above, and keep the CLI's `FIREWORKS_MODEL` override in place so this is a one-line change if your own validation says otherwise.
2. **Build a governed column-value metadata layer** (see above) to close the exact-string-matching gap before this goes near real customer schemas.
3. **Worth scoping as a phase 2, once there's real usage:** Fireworks supports model fine-tuning/distillation. At your projected volume (~30K queries/day), you'd accumulate a large set of (question, schema, generated SQL, execution outcome) examples quickly. Distilling a frontier model's SQL quality — or a curated set of confirmed-correct queries — into a small model fine-tuned specifically on your schema could push cost and latency meaningfully lower than prompt engineering alone can, without giving back the accuracy work. Not something to build in this exercise's time box, but a strong next lever once there's production traffic to learn from.

Happy to walk through the code and design decisions live whenever's useful.

Best,
[Your name]
