"""Unit tests for the CV rendering text rules."""

from __future__ import annotations

import unittest

from core.render.format import AcronymExpander, build_filename, format_date_range


class TestFormatDateRange(unittest.TestCase):
    def test_year_month_range(self) -> None:
        self.assertEqual(format_date_range("2019-01", "2022-12"), "01/2019 – 12/2022")

    def test_missing_end_is_present(self) -> None:
        self.assertEqual(format_date_range("2019-01", None), "01/2019 – Present")

    def test_month_name_and_present_word_are_normalised(self) -> None:
        self.assertEqual(
            format_date_range("March 2024", "Present"), "03/2024 – Present"
        )

    def test_year_only_is_kept(self) -> None:
        self.assertEqual(format_date_range("2015", "2018"), "2015 – 2018")

    def test_unrecognised_text_is_kept_verbatim_and_logged(self) -> None:
        with self.assertLogs("core.render.format", level="WARNING"):
            result = format_date_range("Spring 2020", None)
        self.assertEqual(result, "Spring 2020 – Present")

    def test_invalid_month_is_kept_verbatim(self) -> None:
        with self.assertLogs("core.render.format", level="WARNING"):
            result = format_date_range("2019-13", "2020-01")
        self.assertEqual(result, "2019-13 – 01/2020")

    def test_no_dates_is_empty(self) -> None:
        self.assertEqual(format_date_range(None, None), "")

    def test_missing_start_shows_only_the_end(self) -> None:
        self.assertEqual(format_date_range(None, "2018-12"), "12/2018")


class TestNotOpenEndedRange(unittest.TestCase):
    def test_a_missing_end_is_not_present_when_the_range_is_not_open_ended(
        self,
    ) -> None:
        self.assertEqual(format_date_range("2011", None, open_ended=False), "2011")

    def test_a_closed_range_is_unchanged_when_not_open_ended(self) -> None:
        self.assertEqual(
            format_date_range("2011", "2014", open_ended=False), "2011 – 2014"
        )


class TestAcronymExpander(unittest.TestCase):
    def test_expands_the_first_use_only(self) -> None:
        expander = AcronymExpander()
        self.assertEqual(
            expander.expand("Built ELT pipelines and more ELT"),
            "Built ELT (Extract, Load, Transform) pipelines and more ELT",
        )

    def test_later_texts_are_left_alone(self) -> None:
        expander = AcronymExpander()
        expander.expand("ELT one")
        self.assertEqual(expander.expand("ELT two"), "ELT two")

    def test_an_author_given_expansion_is_kept_and_counts_as_first_use(self) -> None:
        expander = AcronymExpander()
        self.assertEqual(
            expander.expand("GCP (Google Cloud Platform) migration"),
            "GCP (Google Cloud Platform) migration",
        )
        self.assertEqual(expander.expand("More GCP"), "More GCP")

    def test_an_unrelated_bracket_does_not_count_as_an_expansion(self) -> None:
        expander = AcronymExpander()
        self.assertEqual(
            expander.expand("ELT (batch) jobs"),
            "ELT (Extract, Load, Transform) (batch) jobs",
        )

    def test_acronyms_inside_longer_tokens_are_not_touched(self) -> None:
        expander = AcronymExpander()
        for text in ("dbt/ELT models", "ELTA", "REST APIs"):
            self.assertEqual(expander.expand(text), text)

    def test_slash_acronym(self) -> None:
        expander = AcronymExpander()
        self.assertEqual(
            expander.expand("Set up CI/CD for dbt"),
            "Set up CI/CD (Continuous Integration/Continuous Delivery) for dbt",
        )

    def test_two_acronyms_in_one_text(self) -> None:
        expander = AcronymExpander()
        self.assertEqual(
            expander.expand("GCP and AWS"),
            "GCP (Google Cloud Platform) and AWS (Amazon Web Services)",
        )


class TestBuildFilename(unittest.TestCase):
    def test_surname_title_company(self) -> None:
        self.assertEqual(
            build_filename("Zz Fixture", "Lead Data Engineer", "Acme Bank", "docx"),
            "Fixture_Lead_Data_Engineer_Acme_Bank.docx",
        )

    def test_path_characters_and_symbols_are_removed(self) -> None:
        self.assertEqual(
            build_filename(
                "Zz Fixture", "Lead Data Engineer / Platform", "Acme & Sons Ltd.", "txt"
            ),
            "Fixture_Lead_Data_Engineer_Platform_Acme_Sons_Ltd.txt",
        )

    def test_accents_are_folded(self) -> None:
        self.assertEqual(
            build_filename("Zoë Müller", "Ingénieur Données", "Société", "docx"),
            "Muller_Ingenieur_Donnees_Societe.docx",
        )

    def test_missing_company_is_omitted(self) -> None:
        self.assertEqual(
            build_filename("Zz Fixture", "Lead Data Engineer", None, "docx"),
            "Fixture_Lead_Data_Engineer.docx",
        )

    def test_a_name_with_no_latin_letters_falls_back_to_cv(self) -> None:
        self.assertEqual(
            build_filename("山田 太郎", "データ", "会社", "docx"), "CV.docx"
        )

    def test_a_very_long_title_gives_a_filename_within_filesystem_limits(
        self,
    ) -> None:
        name = build_filename("Zz Fixture", "Engineer " * 60, "Acme", "docx")
        self.assertLessEqual(len(name.encode()), 200)
        self.assertTrue(name.endswith(".docx"))
        self.assertTrue(name.startswith("Fixture_Engineer"))


if __name__ == "__main__":
    unittest.main()
