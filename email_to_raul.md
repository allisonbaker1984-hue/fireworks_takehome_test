**Subject: Text-to-SQL PoC Update**

*Built with the help of Claude Code for implementation and Claude for design discussion and code review throughout.*

Hi Raul,

Here's where things stand on the text-to-SQL PoC.

**What we built:** an interactive CLI on Fireworks that converts natural language into SQL against any database you point it at. It reads your actual table structure and foreign keys instead of guessing at them, returns structured output instead of free text, automatically retries failed queries with the error fed back to the model, enforces read-only access, and caches the schema and instructions across turns to keep latency and cost down.

**Performance:** all 10 dev questions answered correctly, 8 of 10 by exact match and 10 of 10 confirmed by an independent LLM-as-judge pass (the other 2 were correct but formatted differently than the reference answer).

**Model choice:** we compared 4 Fireworks models on accuracy, latency, and cost, and recommend `gpt-oss-120b`.

| Model | LLM-Judged Accuracy | P50 Latency | Cost / Query |
|---|---|---|---|
| kimi-k2p7-code | 10/10 | 5.17s | $0.0015 |
| kimi-k2p7-code-fast | 9/10 | 1.56s | $0.0022 |
| **gpt-oss-120b** | **10/10** | **1.73s** | **$0.0004** |
| deepseek-v4-flash | 10/10 | 5.81s (26s worst case) | $0.0003 |

It is the only model that stayed both fast and accurate across two separate comparison runs: comfortably under your 3-second target and roughly 85% cheaper than our first pick.

**How we validated it:** beyond the dev-question results above, we forced execution failures and write attempts to confirm the retry and safety logic actually work, measured real prompt-cache hit rates rather than assuming caching helps, and ran a controlled experiment to isolate the cause of an early latency problem. Full methodology and data are in the repository for review.

**One limitation worth flagging directly:** our schema approach does not include sample data values, so questions that depend on exact string matches (for example, "United States" versus the database's stored "USA") can fail silently. We would want a governed way to surface representative column values before this goes near a real customer schema.

**What we'd tackle next:** rerun the model comparison at larger scale before treating it as fully settled, close the string-matching gap above, and scope model fine-tuning or distillation once there is real production traffic to learn from.

Happy to walk through the code and design decisions whenever works for you.

Best,
[Your name]
