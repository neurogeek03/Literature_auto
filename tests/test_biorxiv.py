"""Offline tests for the medRxiv/bioRxiv preprint resolution path (no network).

Run: ./.venv/bin/python -m unittest tests.test_biorxiv -v

Regression guard for the bug where a medRxiv preprint (AlphaGenome Atlas) that
merely *cites* an arXiv paper (GELU, arXiv:1606.08415) had find_arxiv_id match the
cited reference and hijack the paper's identity — the note was written as the 2016
GELU arXiv paper instead of the 2026 medRxiv preprint. See metadata.find_arxiv_id
(now header-scoped) and metadata.find_preprint_doi / get_metadata precedence.
"""
import unittest
from unittest import mock

from src import metadata
from src.metadata import PaperMeta


# The medRxiv page running-header stamp, printed on every page. Note the newer
# 10.64898 DOI prefix (not the classic 10.1101) — matching is on the label, so it
# must work regardless of prefix.
MEDRXIV_STAMP = (
    "medRxiv preprint doi: https://doi.org/10.64898/2026.09.16.26363192; "
    "this version posted September 20, 2026."
)
BIORXIV_STAMP = (
    "bioRxiv preprint doi: https://doi.org/10.1101/2024.01.02.573001; "
    "this version posted January 2, 2024."
)


class FindPreprintDoi(unittest.TestCase):
    def test_medrxiv_new_prefix(self):
        doi, server = metadata.find_preprint_doi(MEDRXIV_STAMP)
        self.assertEqual(doi, "10.64898/2026.09.16.26363192")
        self.assertEqual(server, "medrxiv")

    def test_biorxiv_classic_prefix(self):
        doi, server = metadata.find_preprint_doi(BIORXIV_STAMP)
        self.assertEqual(doi, "10.1101/2024.01.02.573001")
        self.assertEqual(server, "biorxiv")

    def test_most_frequent_wins(self):
        # The real page DOI repeats on every page; a one-off stray does not.
        text = (MEDRXIV_STAMP + "\n") * 5 + (
            "medRxiv preprint doi: https://doi.org/10.64898/9999.99.99.99999999\n"
        )
        doi, _ = metadata.find_preprint_doi(text)
        self.assertEqual(doi, "10.64898/2026.09.16.26363192")

    def test_absent(self):
        self.assertEqual(metadata.find_preprint_doi("a normal journal paper"), ("", ""))
        self.assertEqual(metadata.find_preprint_doi(""), ("", ""))


class FindArxivIdHeaderScope(unittest.TestCase):
    def test_cited_arxiv_deep_in_body_ignored(self):
        # A cited arXiv reference far past the page-1 header window must NOT be
        # read as the paper's own arXiv id (the AlphaGenome-cites-GELU bug).
        body = "AlphaGenome Atlas.\n" + ("x" * 4000) + "\n[42] arXiv:1606.08415 GELU\n"
        self.assertEqual(metadata.find_arxiv_id(body), "")

    def test_genuine_stamp_in_header_matches(self):
        # A real arXiv paper's page-1 margin stamp is within the header window.
        body = "arXiv:2602.11632v1 [q-bio.NC] 15 Feb 2026\n\nTitle...\n" + ("x" * 4000)
        self.assertEqual(metadata.find_arxiv_id(body), "2602.11632")

    def test_source_hint_still_trusted(self):
        # The dropped filename names the actual paper, regardless of body position.
        body = "some body text " + ("x" * 4000)
        self.assertEqual(metadata.find_arxiv_id(body, "2602.11632.pdf"), "2602.11632")


class CleanAuthors(unittest.TestCase):
    def test_drops_role_labels(self):
        # medRxiv deposits inject "Lead authors:" as a fake first author, which
        # otherwise corrupts the first-author citekey (Authors_2026 vs Cheng_2026).
        self.assertEqual(
            metadata._clean_authors(
                ["Lead authors:", "Cheng, Jun", "Taylor, Kyle R.", "Senior authors:"]
            ),
            ["Cheng, Jun", "Taylor, Kyle R."],
        )

    def test_keeps_normal_authors_and_drops_empties(self):
        self.assertEqual(
            metadata._clean_authors(["", "Jun Cheng", "   ", "Corresponding author:"]),
            ["Jun Cheng"],
        )


class GetMetadataPreprint(unittest.TestCase):
    # A realistic medRxiv full text: the self-DOI stamp up top, and a cited arXiv
    # reference (GELU) deep in the bibliography — exactly the failure shape.
    BODY = (
        MEDRXIV_STAMP
        + "\nAlphaGenome Atlas: in silico mutagenesis of the human genome.\n"
        + ("body " * 800)
        + "\nReferences\n[1] Hendrycks & Gimpel. GELU. arXiv:1606.08415\n"
    )

    def test_resolves_via_preprint_doi_not_arxiv(self):
        resolved = PaperMeta(
            title="AlphaGenome Atlas",
            authors=["Cheng, Jun"],
            year="2026",
            venue="medRxiv",
            doi="10.64898/2026.09.16.26363192",
            is_preprint=True,
        )
        with mock.patch.object(
            metadata, "_resolve_by_doi", return_value=resolved
        ) as rd, mock.patch.object(
            metadata, "fetch_arxiv", side_effect=AssertionError("arXiv must not run")
        ):
            m = metadata.get_metadata(pdf_text=self.BODY, doi_text=self.BODY)
        rd.assert_called_once_with("10.64898/2026.09.16.26363192")
        self.assertEqual(m.doi, "10.64898/2026.09.16.26363192")
        self.assertEqual(m.citekey, "Cheng_2026")
        self.assertTrue(m.is_preprint)
        self.assertNotIn("arXiv", m.doi)

    def test_unresolvable_preprint_keeps_identity(self):
        # Brand-new prefix no registry knows yet: keep the correct DOI/venue/year
        # rather than fall through to a wrong-paper guess.
        with mock.patch.object(
            metadata, "_resolve_by_doi", return_value=None
        ), mock.patch.object(
            metadata, "fetch_arxiv", side_effect=AssertionError("arXiv must not run")
        ):
            m = metadata.get_metadata(pdf_text=self.BODY, doi_text=self.BODY)
        self.assertEqual(m.doi, "10.64898/2026.09.16.26363192")
        self.assertEqual(m.venue, "medRxiv")
        self.assertEqual(m.year, "2026")
        self.assertTrue(m.is_preprint)

    def test_explicit_doi_still_wins(self):
        with mock.patch.object(
            metadata, "_resolve_by_doi", return_value=None
        ) as rd:
            m = metadata.get_metadata(
                doi="10.9999/explicit", pdf_text=self.BODY, doi_text=self.BODY
            )
        rd.assert_called_once_with("10.9999/explicit")
        self.assertEqual(m.doi, "10.9999/explicit")


if __name__ == "__main__":
    unittest.main()
