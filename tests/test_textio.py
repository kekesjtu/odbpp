"""Text and tokenisation tests.

Two habits matter here.  First, the tokeniser must be quote aware, because a
text record's content is arbitrary ASCII that regularly contains ``#`` and ``;``
(spec p.174).  Second, the structured reader must **preserve key case**: attribute
names such as ``.technology`` are case sensitive, while the fixed-vocabulary files
(``misc/info``, ``stephdr``, matrix blocks) are written in either case by
different exporters and must be looked up case insensitively.
"""

from __future__ import annotations

import pytest

from odbpp.errors import ParseError
from odbpp.textio import (
    StructuredText,
    iter_content_lines,
    parse_value,
    split_sections,
    tokenize,
)


class TestIterContentLines:
    def test_comments_and_blanks_are_dropped_but_numbers_are_kept(self):
        text = "UNITS=MM\n#\n# a comment\n\nF 3\n"
        assert [line for _, line in iter_content_lines(text)] == ["UNITS=MM", "F 3"]

    def test_line_numbers_are_one_based_and_refer_to_the_original_text(self):
        text = "UNITS=MM\n#\nF 3\n"
        assert [(n, l) for n, l in iter_content_lines(text)] == [(1, "UNITS=MM"), (3, "F 3")]

    def test_crlf_and_bare_cr_are_both_accepted(self):
        # Samples come from Windows and Unix exporters alike.
        text = "UNITS=MM\r\nF 3\r\n"
        assert [line for _, line in iter_content_lines(text)] == ["UNITS=MM", "F 3"]
        assert [line for _, line in iter_content_lines("UNITS=MM\rF 3\r")] == ["UNITS=MM", "F 3"]

    def test_trailing_whitespace_is_trimmed(self):
        assert [line for _, line in iter_content_lines("F 3   \n")] == ["F 3"]


class TestTokenize:
    def test_plain_fields(self):
        assert [t.text for t in tokenize("P 5 5 0 P 0 0")] == ["P", "5", "5", "0", "P", "0", "0"]

    def test_quoted_text_keeps_its_hash_and_semicolon(self):
        # A text record's payload is quoted precisely so these are not comments.
        line = "T 0.1 0.1 standard P 0 0.05 0.05 1.0 'ROT#30;SEMI' 0"
        tokens = tokenize(line)
        assert tokens[a_quoted_index(tokens)].text == "ROT#30;SEMI"

    def test_quoted_field_is_marked_as_quoted(self):
        tokens = tokenize("T 1 2 'abc' 3")
        quoted = [t for t in tokens if t.quoted]
        assert len(quoted) == 1
        assert quoted[0].text == "abc"

    def test_unterminated_quote_is_an_error_not_a_silent_truncation(self):
        with pytest.raises(ParseError):
            tokenize("T 1 2 'abc")

    def test_values_keep_their_original_spelling(self):
        tokens = tokenize("P 1.500 -0.25 +3")
        assert [t.text for t in tokens] == ["P", "1.500", "-0.25", "+3"]


def a_quoted_index(tokens) -> int:
    for index, token in enumerate(tokens):
        if token.quoted:
            return index
    raise AssertionError("no quoted token found")


class TestSplitSections:
    def test_splits_on_semicolons(self):
        assert split_sections("P 1 2 P;0=1;ID=7") == ["P 1 2 P", "0=1", "ID=7"]

    def test_semicolons_inside_quotes_are_not_separators(self):
        parts = split_sections("T 1 2 'a;b';0=1")
        assert parts == ["T 1 2 'a;b'", "0=1"]

    def test_empty_sections_are_preserved(self):
        # "S P 0;;ID=138" is legal (spec p.138), so the empty middle must survive.
        assert split_sections("S P 0;;ID=138") == ["S P 0", "", "ID=138"]


class TestStructuredText:
    def test_block_with_brace_on_the_same_line(self):
        doc = StructuredText.parse("LAYER {\n  NAME=F.CU\n  TYPE=SIGNAL\n}\n")
        assert len(doc.blocks_of("LAYER")) == 1
        assert doc.blocks_of("LAYER")[0].str_field("NAME") == "F.CU"

    def test_block_with_brace_on_the_next_line(self):
        # misc/sysattr* uses this shape: "BOOLEAN\n{\n  NAME = ...\n}".
        text = "BOOLEAN\n{\n  NAME = .string_mirrored\n  ENTITY = FEATURE\n}\n"
        doc = StructuredText.parse(text)
        blocks = doc.blocks_of("BOOLEAN")
        assert len(blocks) == 1
        assert blocks[0].str_field("NAME") == ".string_mirrored"

    def test_nested_blocks(self):
        text = "A {\n B {\n  X = 1\n }\n}\n"
        doc = StructuredText.parse(text)
        outer = doc.blocks_of("A")[0]
        assert outer.blocks_of("B")[0].int_field("X") == 1

    def test_lookup_is_case_insensitive(self):
        doc = StructuredText.parse("LAYER {\n  row = 3\n  Name = F.Cu\n}\n")
        block = doc.blocks_of("layer")[0]
        assert block.int_field("ROW") == 3
        assert block.str_field("nAmE") == "F.Cu"
        assert "row" in block

    def test_key_case_is_preserved_so_attribute_names_survive(self):
        # .technology and .TECHNOLOGY are different attributes.
        doc = StructuredText.parse(".technology = probe\n.TECHNOLOGY = other\n")
        assert set(doc.fields) == {".technology", ".TECHNOLOGY"}
        assert doc.fields[".technology"] == "probe"
        assert doc.fields[".TECHNOLOGY"] == "other"

    def test_later_duplicate_field_wins(self):
        doc = StructuredText.parse("A {\n  X = 1\n  X = 2\n}\n")
        assert doc.blocks_of("A")[0].int_field("X") == 2

    def test_empty_value_is_kept_as_empty_string_not_none(self):
        # "OLD_NAME=" is extremely common and means "no value", not "missing".
        doc = StructuredText.parse("LAYER {\n  OLD_NAME=\n}\n")
        assert doc.blocks_of("LAYER")[0].str_field("OLD_NAME") == ""
        assert doc.blocks_of("LAYER")[0].str_field("ABSENT") == ""

    def test_typed_accessors_report_the_offending_field(self):
        doc = StructuredText.parse("X = notanumber\n")
        with pytest.raises(ParseError) as excinfo:
            doc.int_field("X")
        assert "notanumber" in str(excinfo.value)

    def test_unterminated_block_is_an_error(self):
        with pytest.raises(ParseError):
            StructuredText.parse("A {\n  X = 1\n")

    def test_unmatched_closing_brace_is_an_error(self):
        with pytest.raises(ParseError):
            StructuredText.parse("}\n")

    def test_brace_without_a_keyword_is_an_error(self):
        with pytest.raises(ParseError):
            StructuredText.parse("{\n  X = 1\n}\n")


class TestParseValue:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("1", 1),
            ("-2", -2),
            ("1.5", 1.5),
            ("YES", "YES"),
            ("", ""),
        ],
    )
    def test_passthrough_shapes(self, raw, expected):
        assert parse_value(raw) == expected
