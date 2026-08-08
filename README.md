# Fireworks AI Field Engineering: Text-to-SQL

This take-home is meant to mirror part of the AI Field Engineer role: supporting customers in their journey to build GenAI applications on Fireworks.

In this exercise, you should approach the problem like a Fireworks engineer supporting a customer who needs a text-to-SQL agent that can run as an interactive CLI.

## What We're Looking For

1. **Customer-oriented problem solving**: translate the customer's pain points into a working system that demonstrates a working proof of concept.
2. **CLI and agent design**: build an interactive terminal agent that converts natural language to SQL and returns results.
3. **Self-validation**: show that you checked your work against the provided test set and know where the system still fails.
4. **Code quality**: write clean, readable Python that another engineer can understand and extend.
5. **Communication**: draft a clear response email to the customer explaining what you built, how you validated it, and what comes next.

## Customer Scenario

**From:** Raul Jimenez <raul.j@gitlab.com>
**To:** Solutions Team <solutions@fireworks.ai>
**Subject:** Help Needed: Agentic BI CLI Product for GitLab Customers

Hi Fireworks team,

Following up on our conversation about a new product we're building. We want to ship an agentic business intelligence CLI as part of the GitLab platform — a tool that lets any developer or data practitioner query their database in natural language directly from the terminal. The idea is that customers plug in their own database and start asking questions immediately, no setup beyond a connection string.

At our current scale, we're projecting roughly 1,000 active users in the first cohort, running about 30 queries per day each.

**Our current state:**

We built a quick prototype using a simple prompt:

```
Convert this question to SQL:
{question}
```

We tested it with GPT-5.4 and the results were mixed. Sometimes it works, sometimes it hallucinates table names, produces invalid SQL, or returns wrong results. We don't have a systematic way to measure whether it's "good enough" to ship to customers.

**Where we're stuck:**

1. **Quality**: The accuracy is not where we need it. We're seeing hallucinated column names, incorrect JOINs, and SQL that doesn't execute. We can't ship a product to customers where the SQL is wrong — trust is everything for a customer-facing feature.

2. **Latency**: Our current prototype takes about 7 seconds end-to-end (from the user hitting enter to seeing results). For an interactive CLI experience, we need this under 3 seconds P50 end-to-end. Developers won't adopt a tool that feels sluggish.

3. **Cost**: With ~1,000 users at ~30 queries/day, we're looking at roughly 30,000 queries daily — and that's just the first cohort. At GPT-5.4 pricing, the unit economics don't work for a platform feature. We need to explore open-source alternatives that can deliver comparable quality at a fraction of the cost so this is sustainable as we scale.

**What we're envisioning:**

An interactive CLI that a user can launch from their terminal, point at any database, and ask questions in natural language. They get back the SQL query plus results, and can ask follow-up questions to refine or explore further.

**What we need from you:**

1. A working proof-of-concept CLI that demonstrates this is viable with open-source models on Fireworks
2. Evidence that you've validated the PoC against real questions
3. A clear read on what works, what doesn't, and what you'd tackle next

We're providing a sample database and a set of test questions so you can build and validate against real data.

Looking forward to your recommendations.

Best,
Raul J.
Director of Data Platform, GitLab

---

## Project Structure

```
.
├── README.md
├── setup.sh
├── pyproject.toml
├── uv.lock
├── src/
│   ├── cli.py
│   ├── agent.py
│   └── utils.py
└── data/
    ├── Chinook.db
    ├── dev_questions.json
    ├── dev_questions_with_answers.json
    └── dev_answers_example.json
```

## Data Overview

The sample database is the Chinook database — a SQLite database modeling a digital music store with 11 tables:

| Table | Description |
|-------|-------------|
| `Artist` | Music artists |
| `Album` | Albums linked to artists |
| `Track` | Individual tracks (linked to album, genre, media type) |
| `Genre` | Music genre classification |
| `MediaType` | Media format classification |
| `Customer` | Customer information with billing address |
| `Employee` | Support representatives |
| `Invoice` | Sales invoices with billing details |
| `InvoiceLine` | Line items — track, unit price, quantity |
| `Playlist` | Curated track collections |
| `PlaylistTrack` | Links playlists to tracks |

Use the provided utility functions to explore the schema:

```python
from src.utils import load_db, query_db, print_table_schema

conn = load_db()
print_table_schema(conn)
results = query_db(conn, "SELECT * FROM Artist LIMIT 5")
```

## Your Task

Build an interactive CLI agent that converts natural language questions to SQL and executes them against the database. Your goal is to demonstrably improve on the customer's baseline prompt (`Convert this question to SQL: {question}`) in quality and reliability.

Part of the challenge is deciding *how* to build this: how the agent understands the database schema, what happens when generated SQL fails, whether tools or structured outputs help, and how the conversation context should be used. These are design decisions we want to see you reason through.

### CLI Requirements

The CLI should be runnable via:

```bash
uv run cli
```

Or, if you prefer to activate the environment manually:

```bash
python -m src.cli
```

This should launch an interactive terminal session where the user can:
- Type a natural language question
- See the generated SQL query
- See the query results
- Ask follow-up questions or refine queries
- Type `exit` or `quit` to end the session

### Development Questions

`data/dev_questions.json` contains 10 development questions. `data/dev_questions_with_answers.json` includes the gold-standard SQL and expected results so you can evaluate your system locally.

Run your agent against each of the 10 questions and record the outputs in `dev_answers.json`. The dev answer key is public so you can build and iterate against it.

## Things to Consider

- What happens when the generated SQL doesn't execute?
- How does the agent know about the database it's querying?
- What are the trade-offs between different agent architectures for this problem?
- What are the trade-offs between open-source and proprietary models for this use case?
- How should the CLI handle follow-up questions and conversation context?

## Required Deliverables

1. **ZIP file** shared via Google Drive containing your implementation
2. **README** in your submission with exact run instructions, required environment variables, and setup steps
3. **Working CLI** runnable via `uv run cli` (or `python -m src.cli`)
4. **`dev_answers.json`** with your system's outputs for the 10 development questions
5. **Email response** to Raul — a short, professional email with:
   - What you built and key findings
   - How you performed against the 10 dev questions
   - How you validated your PoC
   - What you'd tackle next and the scope to move this forward

## `dev_answers.json` Format

Copy `data/dev_answers_example.json` and fill in your answers:

```json
{
  "q_001": {
    "sql": "SELECT g.Name, SUM(il.UnitPrice * il.Quantity) ...",
    "answer": "Rock ($826.65), Latin ($382.14), ..."
  },
  "q_002": {
    "sql": "...",
    "answer": "..."
  }
}
```

Include both the generated SQL and a human-readable summary of the results.

## Submission Guidelines

- Submit your ZIP via Google Drive within the deadline provided by your recruiter.
- Please spend no more than ~**3 hours** on this assessment.
- You may use any Fireworks model and any additional framework, library, or tool.
- You may use the internet, documentation, third-party packages, and AI coding tools.
- If you use AI assistance, mention how in your email response.
- If you have questions during the assessment, reach out to your recruiter.

**Note on scope:** We're more interested in your approach, thought process, and ability to make progress in a time-boxed manner than achieving perfect accuracy. A strong submission has a working agent, evidence of self-validation, and a clear email — not necessarily all three polished to perfection. Focus on demonstrating sound engineering judgment, clean code, and clear communication.

## Getting Started

Run the setup script to download the Chinook database:

```bash
./setup.sh
```

Then install dependencies and run the CLI:

```bash
uv sync
export FIREWORKS_API_KEY=<your-key>
uv run cli
```

Explore the database and dev questions, then start building.

## Resources

1. [Fireworks AI Model Library](https://fireworks.ai/models)
2. [Fireworks AI Docs](https://docs.fireworks.ai)
3. [Fireworks OpenAI SDK Compatibility](https://docs.fireworks.ai/getting-started/quickstart)

## How We Will Review

We will review your submission using:
- Your system's accuracy on the public dev questions
- The quality of the interactive CLI experience
- Whether you validated your work and can articulate where the system still fails
- The clarity and professionalism of your email to Raul
- Code quality and readability

**Note:** You will be invited to a follow-up live session where we will extend this project together. We'll discuss what it takes to ship to production and work through evaluation, testing, and performance metrics as a team. Come ready to walk through your code and extend it live.
