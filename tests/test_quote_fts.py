"""Unit tests for quote_fts — FTS5 query escaping."""

from __future__ import annotations

import sqlite3

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from rekal.adapters.sqlite_adapter import quote_fts

# ── Output structure ──────────────────────────────────────────────────────────


def test_bare_word() -> None:
    assert quote_fts("hello") == '"hello"'


def test_two_bare_words() -> None:
    assert quote_fts("hello world") == '"hello" "world"'


def test_double_quotes_are_word_separators() -> None:
    # `"` is treated as whitespace — phrase intent is not preserved.
    assert quote_fts('"hello world"') == '"hello" "world"'
    assert quote_fts('he said "hello world"') == '"he" "said" "hello" "world"'
    assert quote_fts('foo"bar') == '"foo" "bar"'


def test_quotes_only_produce_empty() -> None:
    assert quote_fts('"') == ""
    assert quote_fts('""') == ""
    assert quote_fts('"""') == ""


# ── Edge: empty / whitespace ──────────────────────────────────────────────────


def test_empty_string() -> None:
    assert quote_fts("") == ""


def test_whitespace_only() -> None:
    assert quote_fts("   ") == ""


def test_tabs_and_newlines() -> None:
    assert quote_fts("foo\tbar\nbaz") == '"foo" "bar" "baz"'


def test_null_byte_stripped() -> None:
    # FTS5 rejects null bytes even inside phrase quotes — strip them.
    assert quote_fts("\x00") == ""
    assert quote_fts("foo\x00bar") == '"foobar"'


def test_multiple_spaces_collapsed() -> None:
    assert quote_fts("foo   bar") == '"foo" "bar"'


# ── FTS5 boolean operators neutralised ───────────────────────────────────────


def test_and_operator_neutralised() -> None:
    assert quote_fts("AND") == '"AND"'


def test_or_operator_neutralised() -> None:
    assert quote_fts("OR") == '"OR"'


def test_not_operator_neutralised() -> None:
    assert quote_fts("NOT") == '"NOT"'


def test_boolean_expression_neutralised() -> None:
    result = quote_fts("foo AND bar OR NOT baz")
    assert result == '"foo" "AND" "bar" "OR" "NOT" "baz"'


# ── FTS5 special syntax chars ────────────────────────────────────────────────


def test_caret_neutralised() -> None:
    assert quote_fts("^foo") == '"^foo"'


def test_star_neutralised() -> None:
    assert quote_fts("foo*") == '"foo*"'


def test_dot_neutralised() -> None:
    assert quote_fts("foo.bar") == '"foo.bar"'


def test_dash_neutralised() -> None:
    assert quote_fts("foo-bar") == '"foo-bar"'


def test_colon_neutralised() -> None:
    assert quote_fts("foo:bar") == '"foo:bar"'


def test_parens_neutralised() -> None:
    assert quote_fts("(foo bar)") == '"(foo" "bar)"'


# ── Unicode ───────────────────────────────────────────────────────────────────


def test_unicode_letters() -> None:
    assert quote_fts("héllo wörld") == '"héllo" "wörld"'


def test_emoji() -> None:
    result = quote_fts("hello 🔥 world")
    assert result == '"hello" "🔥" "world"'


def test_cjk() -> None:
    result = quote_fts("日本語 テスト")
    assert result == '"日本語" "テスト"'


def test_rtl_arabic() -> None:
    result = quote_fts("مرحبا بالعالم")
    assert result == '"مرحبا" "بالعالم"'


# ── Injection / adversarial ───────────────────────────────────────────────────


def test_sql_comment_neutralised() -> None:
    result = quote_fts("'; DROP TABLE memories; --")
    assert '"' in result  # all tokens phrase-wrapped


def test_fts5_column_filter_syntax_neutralised() -> None:
    # content:foo is FTS5 column filter syntax — should be neutralised.
    assert quote_fts("content:foo") == '"content:foo"'


def test_near_syntax_neutralised() -> None:
    assert quote_fts("NEAR(foo bar)") == '"NEAR(foo" "bar)"'


def test_only_special_chars() -> None:
    result = quote_fts("* ^ .")
    assert result == '"*" "^" "."'


def test_very_long_query() -> None:
    long = " ".join(["word"] * 1000)
    result = quote_fts(long)
    assert result.count('"word"') == 1000


# ── Output is always valid FTS5 ───────────────────────────────────────────────


def _fts5_accepts(query: str) -> bool:
    """Return True if SQLite FTS5 accepts the query without a syntax error."""
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE VIRTUAL TABLE t USING fts5(content)")
    try:
        conn.execute("SELECT * FROM t WHERE t MATCH ?", (query,)).fetchall()
        return True
    except sqlite3.OperationalError:
        return False
    finally:
        conn.close()


@pytest.mark.parametrize(
    "raw",
    [
        "hello",
        "hello world",
        '"hello world"',
        'he said "hello"',
        '"',
        '"""',
        "",
        "   ",
        "AND OR NOT",
        "foo.bar foo-bar foo:bar",
        "foo*",
        "^start",
        "NEAR(foo bar)",
        'foo"bar',
        "日本語",
        "'; DROP TABLE memories; --",
        "foo\tbar\nbaz",
    ],
)
def test_output_valid_fts5(raw: str) -> None:
    result = quote_fts(raw)
    if result:
        assert _fts5_accepts(result), f"FTS5 rejected output {result!r} for input {raw!r}"


# ── Hypothesis ────────────────────────────────────────────────────────────────


@given(st.text())
@settings(max_examples=500)
def test_quote_fts_never_crashes(query: str) -> None:
    quote_fts(query)  # must not raise


@given(st.text())
@settings(max_examples=500)
def test_quote_fts_output_always_valid_fts5(query: str) -> None:
    result = quote_fts(query)
    if result:
        assert _fts5_accepts(result), f"FTS5 rejected {result!r} (input: {query!r})"


@given(st.text(alphabet=st.characters(whitelist_categories=("L", "N", "P", "S"))))
@settings(max_examples=300)
def test_quote_fts_with_printable_chars(query: str) -> None:
    result = quote_fts(query)
    if result:
        assert _fts5_accepts(result)


@given(
    st.lists(
        st.text(min_size=1, alphabet=st.sampled_from(list("abcdefghijklmnopqrstuvwxyz\"' "))),
        min_size=1,
        max_size=20,
    )
)
@settings(max_examples=300)
def test_quote_fts_word_list(words: list[str]) -> None:
    query = " ".join(words)
    result = quote_fts(query)
    if result:
        assert _fts5_accepts(result)
