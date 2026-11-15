import socket
import unittest
import urllib.error
import urllib.parse
from datetime import date
from unittest import mock

from external_search import (AccessStatus, InvalidQueryError, ProviderNetworkError,
                             ProviderResponseError, ProviderTimeout, SearchField)
from external_search.providers import arxiv as ax
from external_search.providers.arxiv import ArxivProvider, normalize_arxiv_id


def feed(total, entries=""):
    return ('<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom" '
            'xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/" '
            'xmlns:arxiv="http://arxiv.org/schemas/atom">'
            f'<opensearch:totalResults>{total}</opensearch:totalResults>{entries}</feed>').encode()


def entry(i="2101.00001v2", title="Quantum\n  Gravity"):
    return f"""<entry><id>http://arxiv.org/abs/{i}</id>
    <published>2021-01-01T10:00:00Z</published><title>{title}</title>
    <summary>  An abstract
    over lines. </summary>
    <author><name>Ada Lovelace</name></author><author><name>Alan Turing</name></author>
    <arxiv:doi>10.1234/abc</arxiv:doi><arxiv:journal_ref>Phys. Rev. D 1</arxiv:journal_ref>
    <link title="pdf" href="http://arxiv.org/pdf/{i}" rel="related" type="application/pdf"/>
    </entry>"""


class Recorder:
    def __init__(self, body): self.body, self.urls = body, []
    def __call__(self, url): self.urls.append(url); return self.body
    def params(self): return urllib.parse.parse_qs(urllib.parse.urlparse(self.urls[-1]).query)


def provider(body):
    rec = Recorder(body)
    return ArxivProvider(fetch=rec, min_interval=0), rec


class ParsingTests(unittest.TestCase):
    def test_entry_normalized(self):
        p, _ = provider(feed(1, entry()))
        r = p.search("gravity").results[0]
        self.assertEqual(r.title, "Quantum Gravity")
        self.assertEqual(r.authors, ["Ada Lovelace", "Alan Turing"])
        self.assertEqual(r.abstract, "An abstract over lines.")
        self.assertEqual(r.published, date(2021, 1, 1))
        self.assertEqual(r.publication_year, 2021)
        self.assertEqual(r.identifier, "2101.00001v2")
        self.assertEqual(r.source, "arxiv")
        self.assertEqual(r.landing_page_url, "https://arxiv.org/abs/2101.00001v2")
        self.assertEqual(r.pdf_url, "https://arxiv.org/pdf/2101.00001v2")
        self.assertEqual((r.doi, r.journal), ("10.1234/abc", "Phys. Rev. D 1"))
        self.assertEqual(r.access_status, AccessStatus.OPEN)

    def test_old_style_id_and_pdf_fallback(self):
        e = '<entry><id>http://arxiv.org/abs/hep-th/9901001v1</id><title>T</title></entry>'
        p, _ = provider(feed(1, e))
        r = p.search("x").results[0]
        self.assertEqual(r.identifier, "hep-th/9901001v1")
        self.assertEqual(r.pdf_url, "https://arxiv.org/pdf/hep-th/9901001v1")
        self.assertIsNone(r.published)

    def test_normalize_id(self):
        for s in ("2101.00001", "arXiv:2101.00001v3", "https://arxiv.org/abs/2101.00001v3",
                  "https://arxiv.org/pdf/2101.00001v3.pdf"):
            self.assertIsNotNone(normalize_arxiv_id(s), s)
        self.assertEqual(normalize_arxiv_id("arxiv.org/abs/hep-th/9901001"), "hep-th/9901001")
        for s in ("", "hello", "../../etc/passwd", "2101.1"):
            self.assertIsNone(normalize_arxiv_id(s), s)


class QueryTests(unittest.TestCase):
    def test_fields(self):
        self.assertEqual(ax.build_search_query("dark matter", SearchField.TITLE), 'ti:"dark matter"')
        self.assertEqual(ax.build_search_query("Ada Lovelace", SearchField.AUTHOR), 'au:"Ada Lovelace"')
        self.assertEqual(ax.build_search_query("dark matter", SearchField.KEYWORD),
                         "all:dark AND all:matter")

    def test_injection_stripped_and_encoded(self):
        self.assertEqual(ax.build_search_query('a" OR all:b', SearchField.TITLE), 'ti:"a OR all:b"')
        self.assertEqual(ax.build_search_query("x) OR (y:z", SearchField.KEYWORD),
                         "all:x AND all:OR AND all:y AND all:z")
        p, rec = provider(feed(0))
        p.search("a&b=c d", SearchField.TITLE)
        self.assertEqual(rec.params()["search_query"], ['ti:"a&b=c d"'])
        self.assertEqual(len(rec.params()), 5)  # '&' did not inject a new parameter

    def test_empty_query(self):
        for f in SearchField:
            with self.assertRaises(InvalidQueryError):
                ax.build_search_query('  "" ', f)


class PaginationTests(unittest.TestCase):
    def test_first_page(self):
        p, rec = provider(feed(5, entry("2101.00001") + entry("2101.00002")))
        pg = p.search("q", page=1, page_size=2)
        self.assertEqual((rec.params()["start"], rec.params()["max_results"]), (["0"], ["2"]))
        self.assertEqual((pg.total, pg.has_next, pg.next_page, pg.next_start), (5, True, 2, 2))

    def test_middle_and_last_page(self):
        p, rec = provider(feed(5, entry("2101.00003") + entry("2101.00004")))
        pg = p.search("q", page=2, page_size=2)
        self.assertEqual(rec.params()["start"], ["2"])
        self.assertTrue(pg.has_next)
        p, rec = provider(feed(5, entry("2101.00005")))
        pg = p.search("q", page=3, page_size=2)
        self.assertEqual(rec.params()["start"], ["4"])
        self.assertFalse(pg.has_next)
        self.assertIsNone(pg.next_start)

    def test_page_size_clamped_and_validated(self):
        p, rec = provider(feed(0))
        self.assertEqual(p.search("q", page_size=9999).page_size, 50)
        for bad in ({"page": 0}, {"page_size": 0}, {"page": "1"}):
            with self.assertRaises(InvalidQueryError):
                p.search("q", **bad)

    def test_no_results(self):
        p, _ = provider(feed(0))
        pg = p.search("zzzz")
        self.assertTrue(pg.is_empty)
        self.assertEqual((pg.total, pg.has_next), (0, False))


class ErrorTests(unittest.TestCase):
    def test_invalid_xml(self):
        with self.assertRaises(ProviderResponseError):
            provider(b"<html>nope")[0].search("q")

    def test_wrong_root_and_missing_total(self):
        with self.assertRaises(ProviderResponseError):
            provider(b"<rss/>")[0].search("q")
        body = b'<feed xmlns="http://www.w3.org/2005/Atom"></feed>'
        with self.assertRaises(ProviderResponseError):
            provider(body)[0].search("q")

    def test_arxiv_error_entry(self):
        e = ('<entry><id>http://arxiv.org/api/errors#bad</id><title>Error</title>'
             '<summary>bad query</summary></entry>')
        with self.assertRaises(ProviderResponseError) as c:
            provider(feed(1, e))[0].search("q")
        self.assertIn("bad query", str(c.exception))

    def test_entry_missing_id(self):
        with self.assertRaises(ProviderResponseError):
            provider(feed(1, "<entry><title>T</title></entry>"))[0].search("q")

    def test_fetch_errors_propagate(self):
        def boom(url): raise ProviderTimeout("t")
        with self.assertRaises(ProviderTimeout):
            ArxivProvider(fetch=boom, min_interval=0).search("q")

    def _get(self, exc):
        with mock.patch("urllib.request.urlopen", side_effect=exc):
            return ax._http_get("https://export.arxiv.org/api/query", 1)

    def test_http_mapping(self):
        with self.assertRaises(ProviderTimeout):
            self._get(socket.timeout())
        with self.assertRaises(ProviderTimeout):
            self._get(urllib.error.URLError(socket.timeout()))
        with self.assertRaises(ProviderNetworkError):
            self._get(urllib.error.URLError("dns failure"))
        with self.assertRaises(ProviderNetworkError) as c:
            self._get(urllib.error.HTTPError("u", 429, "Too Many", {}, None))
        self.assertIn("429", str(c.exception))
        with self.assertRaises(ProviderNetworkError):
            self._get(ConnectionResetError())


class MetadataAndThrottleTests(unittest.TestCase):
    def test_metadata(self):
        p, rec = provider(feed(1, entry()))
        r = p.get_metadata("https://arxiv.org/abs/2101.00001v2")
        self.assertEqual(r.identifier, "2101.00001v2")
        self.assertEqual(rec.params()["id_list"], ["2101.00001v2"])
        self.assertIsNone(provider(feed(0))[0].get_metadata("2101.00001"))
        with self.assertRaises(InvalidQueryError):
            p.get_metadata("junk")

    def test_pdf_url(self):
        p, _ = provider(b"")
        self.assertEqual(p.get_open_access_pdf("arXiv:2101.00001"), "https://arxiv.org/pdf/2101.00001")
        with self.assertRaises(InvalidQueryError):
            p.get_open_access_pdf("x")

    def test_throttle(self):
        t, slept = [0.0], []
        def sleep(s): slept.append(s); t[0] += s
        p = ArxivProvider(fetch=lambda u: feed(0), min_interval=3.0, clock=lambda: t[0], sleep=sleep)
        p.search("q"); t[0] += 1.0; p.search("q")
        self.assertEqual(slept, [2.0])


if __name__ == "__main__":
    unittest.main()
