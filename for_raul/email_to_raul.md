**Subject: Text-to-SQL PoC update**

Hey Raul,

Wanted to give you a real update on the text-to-SQL PoC, not just "it's going well." Here's what we built, how it did against your test questions, and what's still rough.

## What we built

Starting from your baseline prompt (just "Convert this question to SQL: {question}"), we made a handful of changes aimed straight at the three problems you called out: accuracy, latency, and cost.

Schema-aware prompting. Your baseline gives the model zero information about the database, which is exactly why it was hallucinating tables and getting joins wrong. We pull the database's own CREATE TABLE statements, foreign keys included, and put them right in the prompt, so the model knows how your tables actually relate instead of guessing. This is pulled live from whatever database is connected, so it works the same way on any customer's schema, not just our sample data.

Structured outputs instead of free text. Rather than asking the model to write prose and hoping we can extract a SQL block out of it, we constrain its response to a strict JSON shape using Fireworks' schema-constrained decoding. That kills a whole category of bug where the SQL was actually fine but our own parsing choked on it.

A self-correcting execution loop. We actually run the generated SQL. If it errors, we hand the exact error back to the model and let it try again, up to 3 attempts, before giving up and telling the user clearly. No silent failures, no infinite retries either.

Prompt caching, designed in from day one. Fireworks caches repeated prompt prefixes, so we built around that on purpose: the schema and instructions get constructed once per session and stay identical turn to turn, with the actual question tacked on at the end. That means repeat turns reuse the cached prefix instead of reprocessing the whole schema every time, and cached tokens are billed a lot cheaper (80% off list on the model we tested, not just the usual 50% default). We also pass a consistent session ID on every request so repeat calls land on the same backend replica, otherwise the cache can miss silently even when nothing in the prompt changed. We measured this directly instead of just trusting the theory (more on that below), and at your projected volume it's a real lever on both latency and cost.

Follow-up questions work too. Conversation history carries over within a session, so something like "now sort that by country" just works without repeating yourself.

## How it did on the 10 questions

We ran all 10 dev questions and checked the actual query results against your gold answers, not just whether the SQL looked plausible.

All 10 are correct in substance. An automated check, comparing values and tolerant of extra or renamed columns, shows 8 of 10 as exact matches. The other 2 are right too on manual review; the model just returns first and last name as separate columns where the gold query concatenates them into one string. Same underlying data, different shape, not a wrong answer. We're pointing that out on purpose instead of quietly rounding up to 10/10, since it's a real limitation in how we're auto-grading this and worth remembering as it scales.

The retry logic actually works, we checked rather than assumed. We forced a bad query and an attempted write statement in testing and confirmed the agent catches both, sends the error back to the model, and gets a corrected query on the next try, capped at 3 attempts so nothing spirals.

Caching is measurably working, not just theoretically. On a fresh session's very first call we saw 1,405 of 1,419 prompt tokens served from cache. That's direct proof the design is paying off, not just a nice idea on paper.

Everything is read-only, enforced in code. Generated SQL gets checked to make sure it's a single SELECT statement before it ever touches the database. This blocks statement-stacking tricks and writes smuggled behind a CTE, on top of the database connection itself never being given write access at all.

## Model comparison, and the latency issue we chased down

Our first pass only compared kimi-k2p7-code against its own "fast" serving tier, same underlying weights, just a different serving tier, not actually two different models. That told us how to serve Kimi well, but nothing about whether Kimi was the right pick in the first place. So we widened it to 4 models, including two genuinely different families, before calling anything final.

| Model | Accuracy | Avg latency | P50 latency | Max latency | Cost / 10 q | Cost / query |
|---|---|---|---|---|---|---|
| kimi-k2p7-code | 8/10 (10/10 in substance) | 3.74s | 3.69s | 7.26s | $0.0163 | $0.0016 |
| kimi-k2p7-code-fast | 8/10 (10/10 in substance) | 1.82s | 1.93s | 3.30s | $0.0248 | $0.0025 |
| gpt-oss-120b | 8/10 (10/10 in substance) | 1.75s | 1.32s | 3.75s | $0.0037 | $0.0004 |
| deepseek-v4-flash | 8/10 (10/10 in substance) | 4.28s | 4.39s | 6.73s | $0.0027 | $0.0003 |

A few things stood out. Accuracy was identical across all 4, same 8 of 10, same 2 questions "missed" every single time, which is a good sign that's a quirk in our grading rather than a real quality gap between models. gpt-oss-120b beat kimi-fast on every axis that actually matters here: better P50 (1.32s vs 1.93s), same accuracy, and roughly 85% cheaper, about $333/month vs $2,232/month at your projected 30K queries a day. We're not going to pretend it was a clean sweep though: its max latency (3.75s) was a touch worse than kimi-fast's (3.30s) in this run. That's one data point out of ten on each side, so we're not reading much into it yet, but it's worth another look with more data before we call the tail fully settled. And deepseek-v4-flash was the cheapest per query of the four but had the worst latency (P50 4.39s), so despite the name it wouldn't have hit your sub-3-second target in this test.

Before landing on a serving tier, we also wanted to know whether the original latency swing was something in our own design or a genuine model/infrastructure difference. We ran a controlled test: the same question, fired repeatedly, once with a fresh connection each time and once reusing a single warm one. Two things came out of that. Response length wasn't the cause, latency barely correlated with how much the model actually generated. And connection reuse helped some but didn't close the gap; we still saw a 2x+ spread on identical requests against the base Kimi model, which points to serving-side variance on its standard tier rather than anything in our request handling.

Recommendation: ship with gpt-oss-120b. Same accuracy as everything else we tried, the best P50 of the four, and a lot cheaper. It's a better result on every dimension you asked us to optimize for than our first pick, which is exactly why we went back and widened the comparison instead of just calling it done.

## One limitation worth flagging directly

Our schema approach uses the database's own table definitions but deliberately skips sample data values, to keep this pass simple. That has a real failure mode though: questions that hinge on exact string matches can fail silently if the wording doesn't match how the data is actually stored. In this dataset specifically, a question phrased around "United States" would generate a filter for that exact string and come back with zero rows, because the database stores "USA." The model has no way to know that just from the table structure, it's guessing.

For production we'd want a governed way to surface representative values per column, ideally sourced from curated metadata rather than sampling a customer's live tables at query time. That's both a correctness thing (someone should own what "representative" means for a given column) and a governance thing (you don't want to be piping raw customer data into prompts by default). It's basically the same problem Unity Catalog and Genie solve with column comments and curated sample values instead of direct table access, and we think that's the right pattern to follow here rather than reinventing it.

## What's next

Ship gpt-oss-120b as the default, and keep the FIREWORKS_MODEL override in place so switching is a one-line change if your own testing says otherwise. Worth rerunning this comparison against a bigger question set before treating it as fully settled; 10 questions is enough to catch a big problem, which is exactly how we caught the original latency issue, but it's thin for a confident production call at 30K queries a day.

Build a governed column-value metadata layer to close the exact-string-matching gap before this goes near a real customer's schema.

Worth scoping as a phase 2 once there's real usage: Fireworks supports fine-tuning and distillation. At your volume you'd build up a large set of question, schema, SQL, and outcome examples fast. Distilling a frontier model's SQL quality, or a curated set of confirmed-correct queries, into a small model tuned specifically for your schema could push cost and latency even lower without giving up the accuracy work. Not something to build in this pass, but a strong next step once there's real traffic to learn from.

Happy to walk through the code live whenever's good for you.

Best,
[Your name]
