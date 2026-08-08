"""Agent logic for text-to-SQL conversion."""

import json
import os
import sqlite3
import time
import uuid
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv
from openai import OpenAI
from pydantic import BaseModel, Field

from src.utils import NotReadOnlyError, get_schema_ddl, is_readonly_sql, query_db

# Load variables from a local .env file (e.g. FIREWORKS_API_KEY) into the
# process environment, if one exists. A no-op if it doesn't -- production
# deployments are expected to set real environment variables instead.
load_dotenv()

# Fireworks exposes an OpenAI-compatible chat completions endpoint, so the
# standard `openai` client works unmodified here -- only the base_url and
# API key differ from calling OpenAI directly.
FIREWORKS_BASE_URL = "https://api.fireworks.ai/inference/v1"

# Overridable via env var so the model can be swapped without a code change
# -- see compare_models.py / model_comparison.json for the actual head-to-
# head this default is based on, not a guess.
#
# `kimi-k2p7-code-fast` (the router-served fast variant of Fireworks' code-
# specialized model) was chosen over the plain `kimi-k2p7-code` after
# measuring both on all 10 dev questions: identical accuracy (8/10 exact
# match, 10/10 substantively correct on manual review -- see
# generate_dev_answers.py's output), but P50 latency of 1.80s vs. 3.32s and
# a much tighter tail (4.02s max vs. 14.12s max). A follow-up controlled
# test (diagnose_latency.py) ruled out response length as the cause of the
# base model's variance (latency vs. completion-token correlation ~0) and
# showed connection reuse only partially explains it -- pointing to serving
# -side variance on the base model's standard tier rather than anything
# fixable in our request handling, which `-fast` sidesteps. Costs roughly
# 2x more per query, which is still negligible at this scale.
DEFAULT_MODEL = os.environ.get(
    "FIREWORKS_MODEL", "accounts/fireworks/routers/kimi-k2p7-code-fast"
)

# Hard ceiling on total generation attempts for a single question (1 initial
# attempt + retries after an execution failure). Each retry is a full extra
# model round trip -- and we've measured this model swinging from ~3s to
# ~28s call to call -- so this bounds the worst case for one question rather
# than letting a persistently-wrong query retry indefinitely.
MAX_ATTEMPTS = 3

# The instructional portion of the system prompt is a module-level constant
# (rather than an f-string built fresh each call) so that, together with the
# schema block, it forms a fixed prefix across every request in a session.
# Fireworks (like most inference providers) can cache repeated prompt
# prefixes, so keeping this prefix byte-for-byte identical across turns and
# across users of the same database directly reduces both latency and cost
# on the ~30 queries/user/day this system needs to sustain in production.
SYSTEM_PROMPT_TEMPLATE = """You are a SQLite expert helping a user query their database using natural language.

You will be given the database schema as SQL CREATE TABLE statements, which include PRIMARY KEY and FOREIGN KEY constraints. Use those constraints to determine how tables relate to each other -- do not guess a JOIN condition that is not implied by the schema.

Database schema:
{schema}

Rules:
- Only reference tables and columns that appear in the schema above. Never invent a table or column name.
- Write a single syntactically valid SQLite query that answers the user's question.
- Only generate SELECT statements. Never generate INSERT, UPDATE, DELETE, DROP, ALTER, or any other statement that modifies data or schema.
- Prefer explicit JOINs over implicit ones, and qualify column names when a query touches more than one table.
- Respond with a JSON object of the form {{"sql": "<query>"}} and nothing else -- no markdown fences, no prose before or after it.
- If the question is ambiguous or cannot be answered with the given schema, set "sql" to an empty string instead of guessing.
"""


# Structured-output schema for a single SQL-generation turn, passed to the
# API as a JSON Schema via `response_format` so Fireworks constrains
# decoding to match it exactly, rather than us parsing free-form text and
# hoping a code fence shows up in a predictable place.
#
# Kept to a single field on purpose. Note that pydantic serializes this
# class's docstring into the schema's "description" -- and that schema is
# sent as part of the request on every single call, plus echoed into the
# fixed prefix that has to stay identical for prompt caching -- so
# deliberately no docstring here; this comment carries the rationale
# instead, without adding to what we ship over the wire.
class SQLGenerationResult(BaseModel):
    sql: str = Field(
        description=(
            "A single syntactically valid SQLite query that answers the "
            "question, or an empty string if the question cannot be "
            "answered from the given schema."
        )
    )


@dataclass
class QueryResult:
    """
    Outcome of TextToSQLAgent.ask(): one full question -> SQL -> execution
    cycle, including any retries that happened along the way.

    Attributes:
        question: The original natural-language question.
        sql: The final SQL query attempted (empty string if the model
            judged the question unanswerable from the schema).
        rows: Query results as a list of row dicts, or None if the query
            never executed successfully (unanswerable or exhausted retries).
        error: The last SQLite error message, or None if the final attempt
            succeeded (or the question was judged unanswerable).
        attempts: How many model calls this question took (1 = succeeded or
            was judged unanswerable on the first try).
        unanswerable: True if the model judged the question unanswerable
            from the given schema, rather than the SQL simply failing.
        latency_seconds: Wall-clock time for the whole call, including any
            retries -- what the user actually experienced.
    """

    question: str
    sql: str
    rows: Optional[List[Dict[str, Any]]]
    error: Optional[str]
    attempts: int
    unanswerable: bool
    latency_seconds: float


def build_system_prompt(conn: sqlite3.Connection) -> str:
    """
    Construct the system prompt for the text-to-SQL agent.

    The schema is introspected fresh from the live connection (rather than
    hardcoded) so this works against any SQLite database the CLI is pointed
    at, not just Chinook -- matching the "no setup beyond a connection
    string" requirement from the customer.

    Callers should invoke this once per database connection/session and
    reuse the result for every turn (including follow-up questions), rather
    than rebuilding it per-query: the schema does not change mid-session, and
    keeping this string identical across turns is what allows prompt-prefix
    caching to kick in.

    Args:
        conn: Active SQLite database connection

    Returns:
        str: The full system prompt, with the live schema embedded.
    """
    schema_ddl = get_schema_ddl(conn)
    return SYSTEM_PROMPT_TEMPLATE.format(schema=schema_ddl)


class TextToSQLAgent:
    """
    Stateful text-to-SQL agent for one interactive CLI session against one
    database connection.

    One instance corresponds to one conversation: it owns the running chat
    history so follow-up questions (e.g. "now break that down by month") can
    refer back to earlier turns, and it owns a single Fireworks client plus a
    fixed session identity so repeated requests in this session actually
    benefit from prompt-prefix caching instead of each landing on a
    potentially cold backend replica (see SYSTEM_PROMPT_TEMPLATE and
    generate_sql docstrings for why that requires everything upstream of the
    new content to stay byte-identical turn over turn).
    """

    def __init__(
        self,
        conn: sqlite3.Connection,
        model: str = DEFAULT_MODEL,
        api_key: Optional[str] = None,
    ) -> None:
        """
        Args:
            conn: Active SQLite database connection to introspect and query.
            model: Fireworks model identifier, e.g.
                "accounts/fireworks/models/llama-v3p1-70b-instruct".
            api_key: Fireworks API key. Defaults to the FIREWORKS_API_KEY
                environment variable.

        Raises:
            RuntimeError: If no API key is supplied and FIREWORKS_API_KEY is
                not set in the environment.
        """
        self.conn = conn
        self.model = model

        resolved_key = api_key or os.environ.get("FIREWORKS_API_KEY")
        if not resolved_key:
            raise RuntimeError(
                "No Fireworks API key found. Set the FIREWORKS_API_KEY "
                "environment variable or pass api_key explicitly."
            )
        self.client = OpenAI(api_key=resolved_key, base_url=FIREWORKS_BASE_URL)

        # Generated ONCE per session -- i.e. once per TextToSQLAgent
        # instance, which lives for the lifetime of one CLI session -- and
        # reused on every subsequent request. This must NOT be regenerated
        # per-turn: Fireworks uses it to route repeated requests from this
        # session to the backend replica that already has our fixed
        # system-prompt/schema prefix cached, which only works if the
        # identifier stays constant across the whole session (see
        # https://docs.fireworks.ai/guides/prompt-caching).
        self.session_id = str(uuid.uuid4())

        # Built once here (not per-turn) and used to seed the running
        # message history that every subsequent question/answer appends to.
        self.system_prompt = build_system_prompt(conn)
        self.messages: List[Dict[str, str]] = [
            {"role": "system", "content": self.system_prompt}
        ]

        # Running token totals across every call this agent makes (SQL
        # generation, retries, and summarize()) -- for cost estimation when
        # comparing models/designs, not used anywhere in the generation
        # logic itself. cached_prompt_tokens is pulled from the provider's
        # usage response when present, so a model/cost comparison can credit
        # actual cache hits rather than assuming a flat discount rate.
        self.total_prompt_tokens = 0
        self.total_completion_tokens = 0
        self.total_cached_prompt_tokens = 0

    def _record_usage(self, response: Any) -> None:
        """Accumulate token usage from one API response, if present."""
        usage = getattr(response, "usage", None)
        if usage is None:
            return
        self.total_prompt_tokens += getattr(usage, "prompt_tokens", 0) or 0
        self.total_completion_tokens += getattr(usage, "completion_tokens", 0) or 0
        prompt_details = getattr(usage, "prompt_tokens_details", None)
        cached = getattr(prompt_details, "cached_tokens", 0) or 0
        self.total_cached_prompt_tokens += cached

    def _call_model(
        self, messages: List[Dict[str, str]]
    ) -> tuple[SQLGenerationResult, str]:
        """
        Make one structured-output chat completion call and parse it.

        Deliberately does not touch self.messages -- callers decide what to
        commit to persisted session history and when. `generate_sql` commits
        every turn; `ask` only commits the final outcome of a (possibly
        multi-attempt) turn, discarding intermediate failed retries (see
        `ask` docstring).

        Args:
            messages: Full message list to send, including the system
                prompt as the first entry.

        Returns:
            Tuple of (parsed result, raw JSON response text).

        Raises:
            RuntimeError: If Fireworks returns an empty completion, or
                returns content that doesn't parse as the expected
                {"sql": ...} JSON shape despite the schema constraint.
        """
        response = self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            temperature=0,  # deterministic SQL generation, not creative writing
            # Structured output: constrains decoding so the response is
            # guaranteed valid JSON matching SQLGenerationResult, instead of
            # us parsing free text and hoping the SQL is where we expect it.
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": "SQLGenerationResult",
                    "schema": SQLGenerationResult.model_json_schema(),
                },
            },
            # Session/routing affinity: pins every request in this session to
            # the same backend replica so the fixed system-prompt + schema
            # prefix built in __init__ actually gets reused turn over turn
            # instead of only helping when we happen to land on the replica
            # that cached it. `user` and `x-session-affinity` are two
            # documented mechanisms for the same purpose; sending both is
            # free and covers whichever path Fireworks' router prioritizes.
            user=self.session_id,
            extra_headers={"x-session-affinity": self.session_id},
        )
        self._record_usage(response)

        raw_content = response.choices[0].message.content
        if raw_content is None:
            raise RuntimeError("Fireworks returned an empty completion.")

        try:
            result = SQLGenerationResult.model_validate_json(raw_content)
        except ValueError as e:
            raise RuntimeError(
                f"Model response did not match the expected schema: {e}\n"
                f"Raw response: {raw_content!r}"
            ) from e

        return result, raw_content

    def generate_sql(self, question: str) -> str:
        """
        Convert a natural-language question into a SQL query, single-shot.

        The full conversation so far (system prompt + prior turns) is sent
        along with the new question so the model can resolve follow-ups
        ("now sort that by country") using earlier context. Does not execute
        the query or retry on failure -- use `ask` for that; this method
        exists for callers that just want the raw generation step.

        Args:
            question: The user's natural-language question.

        Returns:
            str: The generated SQL query (empty string if the model judged
            the question unanswerable from the schema).
        """
        self.messages.append({"role": "user", "content": question})
        result, raw_content = self._call_model(self.messages)

        # Keep the model's exact raw JSON output in history (so a follow-up
        # turn's context matches exactly what the model said, rather than a
        # reconstructed/reformatted version of it).
        self.messages.append({"role": "assistant", "content": raw_content})

        return result.sql.strip()

    def ask(self, question: str) -> QueryResult:
        """
        Convert a question to SQL, execute it, and self-correct on failure.

        If the generated SQL fails to execute, the SQLite error is fed back
        to the model as an extra turn asking it to fix the query, up to
        MAX_ATTEMPTS total attempts. Those retry attempts are negotiated
        against a local copy of the conversation and are NOT persisted to
        self.messages -- only the original question and the final attempt's
        answer are committed to session history. Otherwise every follow-up
        turn for the rest of the session would keep re-sending a growing
        trail of the model's own failed attempts, which both degrades
        context quality on later turns and makes the (supposedly fixed)
        prompt prefix grow unpredictably, undermining prompt-prefix caching.

        A model response with an empty "sql" (its signal that the question
        is unanswerable from the schema) is never retried -- retrying can't
        fix an unanswerable question.

        Args:
            question: The user's natural-language question.

        Returns:
            QueryResult: see its docstring for field meanings.
        """
        start = time.monotonic()

        working_messages = list(self.messages) + [
            {"role": "user", "content": question}
        ]

        final_result: Optional[SQLGenerationResult] = None
        final_raw_content = ""
        rows: Optional[List[Dict[str, Any]]] = None
        error: Optional[str] = None
        attempt = 0

        for attempt in range(1, MAX_ATTEMPTS + 1):
            final_result, final_raw_content = self._call_model(working_messages)
            working_messages.append(
                {"role": "assistant", "content": final_raw_content}
            )

            if not final_result.sql.strip():
                # Model judged the question unanswerable -- nothing to
                # execute, and retrying won't change that judgment.
                error = None
                rows = None
                break

            # Read-only guard: validated BEFORE the SQL ever touches the
            # database connection, on the same footing as an execution
            # error -- caught here and fed back for a retry rather than
            # ever being handed to query_db. See is_readonly_sql's
            # docstring in utils.py for exactly what this does and doesn't
            # catch.
            try:
                is_readonly_sql(final_result.sql)
            except NotReadOnlyError as e:
                error = str(e)
                rows = None
                if attempt < MAX_ATTEMPTS:
                    working_messages.append(
                        {
                            "role": "user",
                            "content": (
                                "You generated a non-SELECT statement, "
                                "which is not allowed. Rewrite this as a "
                                "read-only SELECT query."
                            ),
                        }
                    )
                continue  # skip execution entirely for this attempt

            try:
                rows = query_db(self.conn, final_result.sql, return_as_df=False)
                error = None
                break  # execution succeeded
            except sqlite3.Error as e:
                error = str(e)
                rows = None
                if attempt < MAX_ATTEMPTS:
                    working_messages.append(
                        {
                            "role": "user",
                            "content": (
                                "That query failed to execute against the "
                                f"database with this error:\n{error}\n\n"
                                "Fix the query and respond again with the "
                                "same JSON schema."
                            ),
                        }
                    )

        # Commit only the original question + the final attempt's answer to
        # persisted session history -- see docstring for why intermediate
        # failed retries are deliberately left out.
        self.messages.append({"role": "user", "content": question})
        self.messages.append({"role": "assistant", "content": final_raw_content})

        return QueryResult(
            question=question,
            sql=final_result.sql.strip() if final_result else "",
            rows=rows,
            error=error,
            attempts=attempt,
            unanswerable=bool(final_result and not final_result.sql.strip()),
            latency_seconds=time.monotonic() - start,
        )

    def summarize(self, question: str, rows: List[Dict[str, Any]]) -> str:
        """
        Produce a short, human-readable natural-language summary of already
        -executed query results that answers the original question -- e.g.
        "Rock ($826.65), Latin ($382.14), ..." rather than a raw row dump.
        Intended for things like a dev-answers file or a friendlier CLI
        display alongside the results table.

        This is a separate, lightweight completion call: it deliberately
        does NOT read from or append to self.messages. Narrating an
        already-executed result isn't part of the SQL-generation
        conversation, and there's nothing about it a follow-up SQL question
        should need to see.

        Args:
            question: The original natural-language question.
            rows: The executed query's result rows (as returned in
                QueryResult.rows).

        Returns:
            str: A concise natural-language answer. Empty results are
            handled locally without a model call, since there's nothing to
            summarize.
        """
        if not rows:
            return "No matching results were found."

        response = self.client.chat.completions.create(
            model=self.model,
            temperature=0,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Given a question and the SQL query results that "
                        "answer it, write one concise, natural-language "
                        "sentence stating the answer directly. Use natural "
                        'formatting the data implies (e.g. "$826.65", '
                        '"3,034 tracks"). Do not mention SQL, queries, or '
                        "rows -- just state the answer."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"Question: {question}\n"
                        f"Results: {json.dumps(rows, default=str)}"
                    ),
                },
            ],
            user=self.session_id,
            extra_headers={"x-session-affinity": self.session_id},
        )
        self._record_usage(response)
        content = response.choices[0].message.content
        return content.strip() if content else ""
