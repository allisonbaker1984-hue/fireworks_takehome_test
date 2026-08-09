**Subject: Text-to-SQL PoC Update**

*Built with the help of Claude Code for implementation and Claude for design discussion and code review throughout.*

Hi Raul,

Thanks for sharing the sample database, the test questions, and such a clear picture of where the prototype was falling short, it made this a lot easier to build against. Good to share an update on the PoC, we made real progress and learned some useful things along the way.

Attached is a zip with everything you need to try it yourself: the working CLI and source, a README with exact setup steps, and `dev_answers.json` showing our outputs on all 10 dev questions so you can see exactly what we tested. Setup's just a few minutes with your own Fireworks API key, the README walks through it.

You called out three specific problems with the prototype, here's what we did about each:

- **Quality:** you were seeing hallucinated tables and wrong joins. We now give the model your actual schema, including foreign keys, pulled live from whatever database it's pointed at, instead of nothing. We also force structured output instead of free text, and auto-retry failed queries with the real database error fed back in. Result: all 10 dev questions answered correctly, confirmed two independent ways, an exact-match check and a separate LLM-as-judge pass.
- **Latency:** you needed under 3 seconds P50, the prototype was at 7. We designed the whole system around prompt caching from the start, then compared 4 Fireworks models before picking one. Result: 1.73 seconds P50 on the model we're recommending.
- **Cost:** GPT-5.4 pricing didn't work at 30,000 queries a day. Moving to an open-source model on Fireworks got us to roughly $340 a month at that volume.

The CLI is also read-only by design (nothing it generates can modify your data) and supports follow-up questions in the same session, both things you'd asked for.

**Model comparison:** here's the head-to-head across all 4 models we tested.

| Model | LLM-Judged Accuracy | P50 Latency | Cost / Query |
|---|---|---|---|
| kimi-k2p7-code | 10/10 | 5.17s | $0.0015 |
| kimi-k2p7-code-fast | 9/10 | 1.56s | $0.0022 |
| **gpt-oss-120b** | **10/10** | **1.73s** | **$0.0004** |
| deepseek-v4-flash | 10/10 | 5.81s (26s worst case) | $0.0003 |

`gpt-oss-120b` is the one we're recommending. Worth noting the LLM-judge pass wasn't a rubber stamp, it caught a real bug in `kimi-k2p7-code-fast` (a missing join that silently dropped some results), which is part of why we trust these numbers.

**How we validated it:** beyond the dev-question results above, we forced execution failures and write attempts to confirm the retry and safety logic actually work, measured real prompt-cache hit rates rather than assuming caching helps, and ran a controlled experiment to isolate the cause of an early latency spike. We kept those scripts out of the attached zip to keep things focused, but happy to share and walk through them if useful: the model comparison harness, the latency diagnostic, and the LLM-judge evaluation.

**One limitation worth flagging directly:** our schema approach doesn't include sample data values, so questions that depend on exact string matches (for example, "United States" versus the database's stored "USA") can fail silently. We'd want a governed way to surface representative column values before this goes near a real customer schema.

**What we'd tackle next:** rerun the model comparison at larger scale before treating it as fully settled, close the string-matching gap above, and scope model fine-tuning or distillation once there's real production traffic to learn from.

This was a fun one to dig into, would love to walk through the code and decisions together whenever works for you.

Best,
Allison
