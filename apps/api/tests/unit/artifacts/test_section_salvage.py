"""A report section whose prose swallowed the JSON that followed it.

Taken from a signed, approved document: a paragraph that ended

    ...based on the following data:","table":{"headers":[...],"rows":[[...]]}

and printed exactly that into Word. Constrained decoding cannot prevent it —
a string field accepts any characters, so the grammar is satisfied and the
leak is only visible afterwards. The model had the table; it wrote it inside
the prose instead of into the fields beside it.
"""

from __future__ import annotations

from workbench.tools.artifacts import ReportSection, salvage_section

LEAKED = (
    "The thickness decreased from 12.50 mm in 2023 to 9.20 mm in 2029, a loss of "
    '3.30 mm over six years [1]. The calculation is based on the following data:","table":'
    '{"headers":["CML","Year","Thickness (mm)"],"rows":[["CML-04",2023,12.50],'
    '["CML-04",2029,9.20]]}'
)


class TestLeakedJson:
    def test_the_json_never_reaches_the_page(self) -> None:
        salvaged = salvage_section(
            ReportSection(heading="Corrosion Rate", sources=[1], body=LEAKED)
        )
        assert '"headers"' not in salvaged.body
        assert '"rows"' not in salvaged.body
        assert "{" not in salvaged.body

    def test_the_prose_before_the_leak_survives(self) -> None:
        salvaged = salvage_section(
            ReportSection(heading="Corrosion Rate", sources=[1], body=LEAKED)
        )
        assert salvaged.body.startswith("The thickness decreased from 12.50 mm")
        assert "3.30 mm over six years [1]." in salvaged.body

    def test_the_table_is_recovered_rather_than_discarded(self) -> None:
        """The readings are the part of the report anyone actually wants."""
        salvaged = salvage_section(
            ReportSection(heading="Corrosion Rate", sources=[1], body=LEAKED)
        )
        assert salvaged.table_headers == ["CML", "Year", "Thickness (mm)"]
        assert salvaged.table_rows == [
            ["CML-04", "2023", "12.50"],
            ["CML-04", "2029", "9.20"],
        ]

    def test_a_table_the_section_already_had_is_not_overwritten(self) -> None:
        salvaged = salvage_section(
            ReportSection(
                heading="Corrosion Rate",
                sources=[1],
                body=LEAKED,
                table_headers=["Year", "mm"],
                table_rows=[["2029", "9.20"]],
            )
        )
        assert salvaged.table_headers == ["Year", "mm"]
        assert salvaged.table_rows == [["2029", "9.20"]]

    def test_an_escaped_slash_does_not_reach_the_page(self) -> None:
        section = salvage_section(
            ReportSection(
                heading="Rate",
                sources=[1],
                body='A rate of 0.55 mm/year (3.30 mm \\/ 6 years) [1].","table":{"headers":["a"]}',
            )
        )
        assert "\\/" not in section.body
        assert "3.30 mm / 6 years" in section.body


class TestOrdinarySections:
    def test_a_clean_section_is_returned_unchanged(self) -> None:
        section = ReportSection(
            heading="Findings",
            sources=[2],
            body="CML-04 shows the highest metal loss [2].",
            table_headers=["CML", "mm"],
            table_rows=[["CML-04", "9.20"]],
        )
        assert salvage_section(section) == section

    def test_a_colon_in_prose_is_not_a_leak(self) -> None:
        section = ReportSection(
            heading="Scope", sources=[1], body='The vessel is tagged "V-1201": the crude feed drum.'
        )
        assert salvage_section(section).body == section.body
