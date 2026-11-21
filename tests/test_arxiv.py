"""Offline tests for the arXiv resolution path (no network).

Run: ./.venv/bin/python -m unittest tests.test_arxiv -v

Regression guard for the bug where an arXiv preprint (no DOI of its own in the
body) had find_doi() pick a cited reference's DOI, mislabeling the note. See
metadata.find_arxiv_id / fetch_arxiv / get_metadata.
"""
import unittest
from unittest import mock

from src import metadata


# A trimmed arXiv export-API Atom response.
SAMPLE_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom">
  <entry>
    <id>http://arxiv.org/abs/2602.11632v1</id>
    <published>2026-02-15T00:00:00Z</published>
    <title>CL API: Real-Time Closed-Loop Interactions with
  Biological Neural Networks</title>
    <summary>  Biological neural networks are increasingly explored.  </summary>
    <author><name>David Hogan</name></author>
    <author><name>Brett J. Kagan</name></author>
  </entry>
</feed>"""

# Same, but with a published journal DOI attached.
SAMPLE_XML_WITH_DOI = SAMPLE_XML.replace(
    b"</entry>", b"    <arxiv:doi>10.1234/journal.2026.999</arxiv:doi>\n  </entry>"
)

# An arXiv "not found" placeholder entry (no title/published).
SAMPLE_XML_EMPTY = b"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>http://arxiv.org/api/errors#incorrect_id_format</id>
    <summary>incorrect id format</summary>
  </entry>
</feed>"""


class _Resp:
    def __init__(self, content):
        self.content = content

    def raise_for_status(self):
        pass


def _patch_get(content):
    return mock.patch.object(metadata.requests, "get", return_value=_Resp(content))


class FindArxivId(unittest.TestCase):
    def test_text_arxiv_token(self):
        self.assertEqual(
            metadata.find_arxiv_id("see arXiv:2602.11632v1 [q-bio.NC]"), "2602.11632"
        )

    def test_text_abs_url(self):
        self.assertEqual(
            metadata.find_arxiv_id("paper at https://arxiv.org/abs/2602.11632"),
            "2602.11632",
        )

    def test_hint_filename_bare_id(self):
        self.assertEqual(
            metadata.find_arxiv_id("", "F0C1YDPG7N0_2602.11632 2.pdf"), "2602.11632"
        )

    def test_hint_pdf_url(self):
        self.assertEqual(
            metadata.find_arxiv_id("", "https://arxiv.org/pdf/2602.11632v2"),
            "2602.11632",
        )

    def test_old_scheme(self):
        self.assertEqual(
            metadata.find_arxiv_id("arXiv:hep-th/9901001"), "hep-th/9901001"
        )

    def test_negative_bare_number_in_text(self):
        # A dotted number in prose, with no arXiv context, must NOT match.
        self.assertEqual(
            metadata.find_arxiv_id("the value was 2602.11632 in table 3"), ""
        )

    def test_negative_cited_doi(self):
        # A cited-reference DOI must never be read as an arXiv ID.
        self.assertEqual(metadata.find_arxiv_id("10.1016/j.neuron.2023.02.008"), "")

    def test_negative_empty(self):
        self.assertEqual(metadata.find_arxiv_id("", ""), "")


class FetchArxiv(unittest.TestCase):
    def test_parse_fields(self):
        with _patch_get(SAMPLE_XML):
            m = metadata.fetch_arxiv("2602.11632")
        self.assertIsNotNone(m)
        self.assertEqual(
            m.title,
            "CL API: Real-Time Closed-Loop Interactions with Biological Neural Networks",
        )
        self.assertEqual(m.authors, ["Hogan, David", "Kagan, Brett J."])
        self.assertEqual(m.year, "2026")
        self.assertEqual(m.url, "https://arxiv.org/abs/2602.11632")
        self.assertEqual(m.oa_pdf_url, "https://arxiv.org/pdf/2602.11632")
        self.assertEqual(m.doi, "10.48550/arXiv.2602.11632")
        self.assertTrue(m.is_preprint)
        self.assertEqual(m.journal_doi, "")
        self.assertEqual(m.abstract, "Biological neural networks are increasingly explored.")

    def test_journal_doi_captured(self):
        with _patch_get(SAMPLE_XML_WITH_DOI):
            m = metadata.fetch_arxiv("2602.11632")
        self.assertEqual(m.journal_doi, "10.1234/journal.2026.999")

    def test_empty_entry_returns_none(self):
        with _patch_get(SAMPLE_XML_EMPTY):
            self.assertIsNone(metadata.fetch_arxiv("bogus"))

    def test_network_error_returns_none(self):
        with mock.patch.object(metadata.requests, "get", side_effect=OSError("dns")):
            self.assertIsNone(metadata.fetch_arxiv("2602.11632"))


class GetMetadataArxiv(unittest.TestCase):
    def test_arxiv_short_circuits_find_doi(self):
        # Full text of a preprint whose bibliography cites the Kagan DOI. The
        # arXiv branch must resolve the paper and never surface the cited DOI.
        body = "Some preprint body.\nReferences\n[1] 10.1016/j.neuron.2023.02.008\n"
        with _patch_get(SAMPLE_XML):
            m = metadata.get_metadata(
                pdf_text=body, doi_text=body, source_hint="2602.11632.pdf"
            )
        self.assertEqual(m.citekey, "Hogan_2026")
        self.assertEqual(m.url, "https://arxiv.org/abs/2602.11632")
        self.assertNotIn("10.1016/j.neuron", m.doi)
        self.assertTrue(m.is_preprint)

    def test_arxiv_fetch_failure_does_not_fall_back_to_cited_doi(self):
        # If arXiv is unreachable, we must NOT scrape the cited DOI; keep the
        # arXiv abs URL and an empty DOI instead.
        body = "body\n[1] 10.1016/j.neuron.2023.02.008\n"
        with mock.patch.object(metadata.requests, "get", side_effect=OSError("dns")):
            m = metadata.get_metadata(
                pdf_text="CL API paper title line here for fallback",
                doi_text=body,
                source_hint="2602.11632.pdf",
            )
        self.assertEqual(m.url, "https://arxiv.org/abs/2602.11632")
        self.assertEqual(m.doi, "")
        self.assertTrue(m.is_preprint)

    def test_explicit_doi_wins_over_arxiv(self):
        # A user-supplied DOI is deliberate; the arXiv branch must not override it.
        with _patch_get(SAMPLE_XML) as g:
            m = metadata.get_metadata(
                doi="10.9999/explicit", source_hint="2602.11632.pdf"
            )
            # arXiv API should not have been consulted at all.
            called_urls = [c.args[0] for c in g.call_args_list if c.args]
        self.assertTrue(all("arxiv" not in u for u in called_urls))
        self.assertEqual(m.doi, "10.9999/explicit")


if __name__ == "__main__":
    unittest.main()
