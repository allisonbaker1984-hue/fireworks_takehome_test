import re
import sqlite3
from typing import List, Dict, Any, Optional, Tuple
from pathlib import Path
import pandas as pd


def load_db(db_path: str = "data/Chinook.db") -> sqlite3.Connection:
    """
    Load the SQLite database and return a connection.

    Args:
        db_path: Path to the SQLite database file. Defaults to "data/Chinook.db"

    Returns:
        sqlite3.Connection: Active database connection

    Raises:
        FileNotFoundError: If the database file doesn't exist
        sqlite3.Error: If there's an error connecting to the database
    """
    db_file = Path(db_path)
    if not db_file.exists():
        raise FileNotFoundError(
            f"Database file not found: {db_path}\n"
            "Please run setup.sh first to create the database."
        )

    try:
        conn = sqlite3.connect(db_path)
        conn.execute("PRAGMA foreign_keys = ON")
        conn.row_factory = sqlite3.Row
        return conn
    except sqlite3.Error as e:
        raise sqlite3.Error(f"Error connecting to database: {e}")


def query_db(
    conn: sqlite3.Connection,
    query: str,
    params: Optional[tuple] = None,
    return_as_df: bool = True,
) -> List[Dict[str, Any]] | pd.DataFrame:
    """
    Execute a SQL query and return results as a pandas DataFrame or list of dictionaries.

    Args:
        conn: Active SQLite database connection
        query: SQL query string to execute
        params: Optional tuple of parameters for parameterized queries
        return_as_df: If True, return pandas DataFrame; if False, return list of dicts

    Returns:
        pd.DataFrame or List[Dict[str, Any]]: Query results

    Raises:
        sqlite3.Error: If there's an error executing the query
    """
    try:
        if return_as_df:
            return pd.read_sql_query(query, conn, params=params)
        else:
            cursor = conn.cursor()
            if params:
                cursor.execute(query, params)
            else:
                cursor.execute(query)

            columns = [description[0] for description in cursor.description]
            results = []
            for row in cursor.fetchall():
                results.append(dict(zip(columns, row)))
            return results
    except sqlite3.Error as e:
        raise sqlite3.Error(f"Error executing query: {e}")


def get_schema(conn: sqlite3.Connection) -> Dict[str, List[Dict[str, str]]]:
    """
    Get the database schema including all tables and their columns.

    Args:
        conn: Active SQLite database connection

    Returns:
        Dict[str, List[Dict[str, str]]]: Dictionary mapping table names to their column info
    """
    schema = {}

    tables = query_db(
        conn,
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name",
        return_as_df=False,
    )

    for table in tables:
        table_name = table["name"]
        columns = query_db(conn, f"PRAGMA table_info({table_name})", return_as_df=False)
        schema[table_name] = columns

    return schema


def get_schema_ddl(conn: sqlite3.Connection) -> str:
    """
    Build a schema representation suitable for injecting into an LLM system prompt.

    Design rationale (see also src/agent.py):
      - We pull the original `CREATE TABLE` statements straight from
        `sqlite_master` instead of reconstructing DDL from `PRAGMA table_info`
        (as `get_schema` does). The raw DDL already encodes PRIMARY KEY and
        FOREIGN KEY constraints, so the model is told exactly how tables relate
        instead of having to guess JOIN conditions -- a major source of the
        "incorrect JOINs" failures the customer reported.
      - Sticking to plain SQL syntax (rather than a custom text format) also
        plays to what LLMs have seen most during training, since CREATE TABLE
        statements are a very common representation in SQL training corpora.
      - This works for *any* SQLite database (not just Chinook), which matters
        because the product plugs into a customer's own database with no
        manual setup -- the schema must be derived automatically, never
        hand-curated.

    Args:
        conn: Active SQLite database connection

    Returns:
        str: All CREATE TABLE statements, one per table, separated by blank
        lines and each terminated with a semicolon -- ready to drop directly
        into a prompt.
    """
    # `sql` here is the exact CREATE TABLE statement SQLite used to build the
    # table, including column types, PRIMARY KEY, and FOREIGN KEY clauses.
    # Views/indexes are excluded via `type='table'`; internal sqlite_ tables
    # are excluded automatically since sqlite_master doesn't list itself.
    tables = query_db(
        conn,
        "SELECT sql FROM sqlite_master WHERE type='table' ORDER BY name",
        return_as_df=False,
    )

    # A table can theoretically have a NULL `sql` column (e.g. implicit
    # tables created internally by SQLite for certain virtual table modules);
    # skip those defensively rather than injecting "None" into the prompt.
    statements = [row["sql"].strip() for row in tables if row["sql"]]

    # Normalize to one statement per line, each ending in ';', so the block
    # reads as valid, copy-pasteable SQL rather than a prose description.
    return "\n\n".join(f"{statement};" for statement in statements)


def print_table_schema(
    conn: sqlite3.Connection, table_name: Optional[str] = None
) -> None:
    """
    Print a formatted view of the database schema.

    Args:
        conn: Active SQLite database connection
        table_name: Optional specific table name to display. If None, shows all tables.
    """
    schema = get_schema(conn)

    if table_name:
        if table_name not in schema:
            print(f"Error: Table '{table_name}' not found in database.")
            print(f"Available tables: {', '.join(schema.keys())}")
            return
        tables_to_print = {table_name: schema[table_name]}
    else:
        tables_to_print = schema

    print("\n" + "=" * 100)
    print(f"DATABASE SCHEMA - {len(schema)} tables")
    print("=" * 100)

    if not table_name:
        print("\nTables:")
        for i, tbl in enumerate(schema.keys(), 1):
            print(f"  {i}. {tbl}")
        print()

    for tbl_name, columns in tables_to_print.items():
        print("\n" + "-" * 100)
        print(f"Table: {tbl_name} ({len(columns)} columns)")
        print("-" * 100)
        print(f"{'Column':<30} {'Type':<20} {'Nullable':<12} {'PK':<5} {'Default':<15}")
        print("-" * 100)

        for col in columns:
            nullable = "NULL" if col["notnull"] == 0 else "NOT NULL"
            pk = "Y" if col["pk"] > 0 else ""
            default = str(col["dflt_value"]) if col["dflt_value"] is not None else ""
            print(
                f"{col['name']:<30} {col['type']:<20} {nullable:<12} {pk:<5} {default:<15}"
            )

    print("=" * 100 + "\n")


# --------------------------------------------------------------------------
# Read-only SQL validation
#
# Generated SQL should never be trusted to only read data just because the
# prompt asked for that -- this validates it structurally before it ever
# reaches a database connection. Deliberately NOT implemented as
# `sql.strip().upper().startswith("SELECT")`: that's defeated by SQL
# comments before the SELECT, and says nothing about a second statement
# smuggled in after a semicolon (e.g. "SELECT 1; DROP TABLE Artist;"), or a
# `WITH ... AS (...)` clause whose final command is DELETE/UPDATE/INSERT
# rather than SELECT (SQLite's grammar allows a WITH clause in front of any
# of those, not just SELECT).
#
# The functions below do a lightweight, quote- and comment-aware scan of the
# SQL text -- not a full SQL parser -- to (a) split on genuine top-level
# statement-separating semicolons, ignoring any that appear inside a string
# literal or comment, and (b) find the actual command keyword a statement
# will execute, skipping past any leading CTE definitions to do so. See
# `is_readonly_sql`'s docstring for what this does and does not catch.
# --------------------------------------------------------------------------


class NotReadOnlyError(ValueError):
    """Raised when generated SQL is not a single read-only SELECT statement."""


_IDENTIFIER_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def _skip_string_or_comment(sql: str, i: int) -> Optional[int]:
    """
    If `sql[i]` starts a line comment, block comment, single-quoted string
    literal, double-quoted identifier, or bracketed [identifier], return the
    index just past its end. Otherwise return None.

    Understands SQLite's escaping convention for quoted literals -- '' or ""
    inside a quote of the same kind is an escaped quote, not the end of the
    literal -- so a value like 'it''s fine' isn't split in the middle.
    """
    n = len(sql)
    ch = sql[i]

    if sql[i : i + 2] == "--":
        end = sql.find("\n", i)
        return n if end == -1 else end + 1
    if sql[i : i + 2] == "/*":
        end = sql.find("*/", i + 2)
        return n if end == -1 else end + 2
    if ch in ("'", '"'):
        j = i + 1
        while j < n:
            if sql[j] == ch:
                if sql[j : j + 2] == ch * 2:  # escaped quote (e.g. '' or "")
                    j += 2
                    continue
                return j + 1
            j += 1
        return n  # unterminated literal -- treat the rest as consumed
    if ch == "[":
        j = sql.find("]", i + 1)
        return n if j == -1 else j + 1

    return None


def split_sql_statements(sql: str) -> List[str]:
    """
    Split `sql` into individual top-level statements on semicolons.

    Unlike `sql.split(';')`, this ignores semicolons that appear inside a
    string/identifier literal or a comment. Paren-depth tracking is not
    needed here: SQLite's grammar never permits a bare, unquoted semicolon
    inside a parenthesized subquery, so any semicolon found outside a
    literal/comment is always a genuine statement boundary. A single
    trailing semicolon (with or without trailing whitespace) does not
    produce an extra empty statement.

    Args:
        sql: Raw SQL text, potentially containing more than one statement.

    Returns:
        List[str]: Each statement, whitespace-trimmed, in source order.
    """
    statements = []
    start = 0
    i = 0
    n = len(sql)
    while i < n:
        skip_to = _skip_string_or_comment(sql, i)
        if skip_to is not None:
            i = skip_to
            continue
        if sql[i] == ";":
            stmt = sql[start:i].strip()
            if stmt:
                statements.append(stmt)
            i += 1
            start = i
            continue
        i += 1

    tail = sql[start:].strip()
    if tail:
        statements.append(tail)
    return statements


def _skip_ws_comments_and_literals(sql: str, i: int) -> int:
    """
    Advance `i` past any run of whitespace, comments, and quoted/bracketed
    literals, stopping at the index of the next character that is
    meaningful syntax (the start of a keyword/identifier, punctuation, a
    digit, etc.) -- or at len(sql) if nothing meaningful remains.
    """
    n = len(sql)
    while i < n:
        skip_to = _skip_string_or_comment(sql, i)
        if skip_to is not None:
            i = skip_to
            continue
        if sql[i].isspace():
            i += 1
            continue
        break
    return i


def _next_token(sql: str, i: int) -> Tuple[Optional[str], int]:
    """
    Starting at index `i`, skip whitespace/comments/literals and return the
    next identifier-shaped token, uppercased, plus the index just past it.
    Returns (None, i') if the next meaningful character isn't the start of
    an identifier (e.g. it's '(' or ',', or the string has ended).
    """
    i = _skip_ws_comments_and_literals(sql, i)
    match = _IDENTIFIER_RE.match(sql, i)
    if match:
        return match.group(0).upper(), match.end()
    return None, i


def _peek_char(sql: str, i: int) -> Tuple[Optional[str], int]:
    """Like `_next_token`, but returns the next meaningful raw character
    (e.g. '(' or ',') instead of an identifier token."""
    i = _skip_ws_comments_and_literals(sql, i)
    if i >= len(sql):
        return None, i
    return sql[i], i


def _skip_balanced_parens(sql: str, i: int) -> int:
    """
    `sql[i]` must be '('. Return the index just past its matching ')',
    correctly skipping over nested parens and any string/comment content in
    between (so a paren inside a string literal, e.g. 'note (see below)',
    doesn't throw off the depth count).
    """
    depth = 0
    n = len(sql)
    while i < n:
        skip_to = _skip_string_or_comment(sql, i)
        if skip_to is not None:
            i = skip_to
            continue
        if sql[i] == "(":
            depth += 1
        elif sql[i] == ")":
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return n  # unterminated -- treat as extending to end of string


def get_statement_command(statement: str) -> Optional[str]:
    """
    Return the top-level SQL command keyword a single statement will
    execute (e.g. "SELECT", "DELETE", "INSERT").

    Correctly looks past a leading `WITH [RECURSIVE] name AS (...), ...`
    common-table-expression clause to find the command it introduces,
    since SQLite allows a WITH clause in front of INSERT/UPDATE/DELETE just
    as validly as SELECT (e.g. "WITH cte AS (SELECT ...) DELETE FROM t ...").

    Args:
        statement: A single SQL statement (see `split_sql_statements`).

    Returns:
        The uppercased command keyword, or None if the statement doesn't
        start with a recognizable identifier token, or a CTE definition is
        malformed enough that the real command can't be located -- both
        treated as "not verifiably read-only" by `is_readonly_sql`.
    """
    token, i = _next_token(statement, 0)
    if token is None:
        return None
    if token != "WITH":
        return token

    # Optional RECURSIVE keyword.
    maybe_recursive, i2 = _next_token(statement, i)
    if maybe_recursive == "RECURSIVE":
        i = i2

    # Walk each comma-separated `name [(col, ...)] AS ( body )` CTE
    # definition, skipping over its parenthesized body wholesale, until no
    # comma follows -- at which point the next token is the real command.
    while True:
        name_token, i = _next_token(statement, i)
        if name_token is None:
            return None  # malformed: expected a CTE name here

        ch, j = _peek_char(statement, i)
        if ch == "(":
            i = _skip_balanced_parens(statement, j)  # optional column list

        as_token, i = _next_token(statement, i)
        if as_token != "AS":
            return None  # malformed CTE definition

        ch, j = _peek_char(statement, i)
        if ch != "(":
            return None  # malformed: CTE body must be parenthesized
        i = _skip_balanced_parens(statement, j)

        ch, j = _peek_char(statement, i)
        if ch == ",":
            i = j + 1
            continue  # another CTE definition follows
        break

    command_token, _ = _next_token(statement, i)
    return command_token


def is_readonly_sql(sql: str) -> None:
    """
    Validate that `sql` is exactly one read-only SELECT statement.

    What this catches:
      - Leading whitespace/comments before the real statement (e.g. a
        "-- explanation\\nSELECT ..." response still validates correctly).
      - Multiple statements stacked with ';' (e.g. "SELECT 1; DROP TABLE
        Artist;") -- rejected regardless of what the first statement is.
      - A `WITH ... AS (...)` clause whose actual command is not SELECT
        (e.g. "WITH cte AS (SELECT 1) DELETE FROM Artist").
      - Any statement whose command keyword isn't exactly SELECT (INSERT,
        UPDATE, DELETE, DROP, ALTER, CREATE, PRAGMA, ATTACH, etc.)

    What this deliberately does NOT do, and why that's an acceptable
    tradeoff here rather than a gap to silently ignore:
      - It is not a full SQL parser/AST, so it cannot catch every
        conceivable malformed-but-technically-parses-as-SELECT edge case a
        real SQLite grammar would. It's a text scan good enough to defeat
        the realistic failure modes above (comments, whitespace, statement
        stacking, WITH-prefixed writes), not a guarantee of syntactic
        validity -- `query_db` executing the statement is still what
        ultimately proves it's valid SQL.
      - It does not prevent a read-only SELECT from being *expensive*
        (e.g. an unbounded scan/huge result set) -- that's a resource
        concern, not a read/write one, and out of scope for this check.
      - It trusts SQLite's own single-statement execution behavior as a
        second line of defense: `sqlite3.Cursor.execute()` refuses to run
        more than one statement even if this check were somehow bypassed,
        so statement-stacking has defense in depth beyond this function.

    Args:
        sql: The generated SQL to validate.

    Raises:
        NotReadOnlyError: If `sql` is empty/comment-only, contains more
            than one statement, or its command is anything other than
            SELECT.
    """
    statements = split_sql_statements(sql)

    if len(statements) == 0:
        raise NotReadOnlyError("No SQL statement found (empty or comment-only).")
    if len(statements) > 1:
        raise NotReadOnlyError(
            f"Expected exactly one SQL statement, found {len(statements)}. "
            "Multiple statements separated by ';' are not allowed."
        )

    command = get_statement_command(statements[0])
    if command != "SELECT":
        raise NotReadOnlyError(
            f"Statement's command is {command or 'unrecognized'}, not SELECT."
        )


def results_match(
    expected_rows: List[Dict[str, Any]], actual_rows: List[Dict[str, Any]]
) -> bool:
    """
    Compare two query result sets by value containment rather than exact
    row/column equality: every row in `expected_rows` must match some row
    in `actual_rows` whose values are a superset of it (each actual row
    consumed at most once; order-independent). This tolerates generated SQL
    selecting extra or differently-named columns than a reference query --
    e.g. the generated query returning `AlbumId, Title, ArtistName` where
    the reference only selected `Title` -- without treating that as wrong.

    Known limitation: this does not reconcile a reference row that
    concatenates multiple columns into one value (e.g. "Jane Peacock" as a
    single string) against a generated row returning the same information
    as separate columns (FirstName="Jane", LastName="Peacock") -- those are
    flagged as a mismatch even though the underlying data is identical,
    since value containment can't see through a string concatenation.
    Treat a False from this function as "needs a human look", not an
    automatic wrong answer, for that specific case.

    Args:
        expected_rows: Reference/gold result rows.
        actual_rows: Rows actually returned by the query under test.

    Returns:
        bool: True if every expected row's values are contained in some
        (distinct) actual row, and both sets have the same row count.
    """
    if len(expected_rows) != len(actual_rows):
        return False

    def row_values(row: Dict[str, Any]) -> frozenset:
        return frozenset(
            round(v, 2) if isinstance(v, float) else v for v in row.values()
        )

    actual_value_sets = [row_values(r) for r in actual_rows]
    consumed = [False] * len(actual_value_sets)

    for expected_row in expected_rows:
        expected_values = row_values(expected_row)
        match_found = False
        for i, actual_values in enumerate(actual_value_sets):
            if not consumed[i] and expected_values.issubset(actual_values):
                consumed[i] = True
                match_found = True
                break
        if not match_found:
            return False

    return True
