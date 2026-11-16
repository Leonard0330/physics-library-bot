"""Zenodo provider tests. Fixtures are modeled on Zenodo's documented record format;
nothing here touches the network."""
import asyncio
import json
import os
import tempfile
import unittest
import urllib.parse
from datetime import date

from external_search import (AccessStatus, InvalidQueryError, ProviderResponseError, SearchField,
                             SearchManager)
from external_search.providers import zenodo as z
from external_search.providers._http import NotFoundError


def rec(rid=1234567, title="Dark matter halos", right="open", files=None, **meta):
    m = {"title": title, "creators": [{"name": "Einstein, Albert"}, {"name": "Marie Curie"}],
         "description": "<p>We study <b>dark</b> matter &amp; halos.</p>", "publication_date": "2021-03-04",
         "access_right": right, "resource_type": {"type": "publication", "subtype": "article"}}
    m.update(meta)
    return {"id": rid, "doi": f"10.5281/zenodo.{rid}", "metadata": m,
            "files": [{"key": "paper.pdf", "size": 1000}] if files is None else files}


def payload(*recs, total=None):
    return json.dumps({"hits": {"hits": list(recs), "total": len(recs) if total is None else total}}).encode()


class Fetch:
    def __init__(self, body): self.body, self.urls = body, []

    def __call__(self, url):
        self.urls.append(url)
        if isinstance(self.body, Exception):
            raise self.body
        return self.body


def provider(body):
    f = Fetch(body)
    return z.ZenodoProvider(fetch=f, min_interval=0), f


class QueryTests(unittest.TestCase):
    def test_build_query(self):
        self.assertEqual(z.build_query('dark  matter "halo"', SearchField.TITLE), 'title:"dark matter halo"')
        self.assertEqual(z.build_query("Albert Einstein", SearchField.AUTHOR), "creators.name:(Albert AND Einstein)")
        self.assertEqual(z.build_query("dark matter", SearchField.KEYWORD), "dark AND matter")

    def test_hostile_input_is_neutralised(self):
        q = z.build_query("a OR b) AND (x:y -z \\ NOT", SearchField.KEYWORD)
        self.assertEqual(q, "a AND b AND x AND y AND z")
        for f in (SearchField.AUTHOR, SearchField.KEYWORD):
            for bad in ("", "   ", "()[]:", "AND OR NOT"):
                with self.assertRaises(InvalidQueryError, msg=(f, bad)):
                    z.build_query(bad, f)
        with self.assertRaises(InvalidQueryError):
            z.build_query('  "  ', SearchField.TITLE)

    def test_ids(self):
        for t in ("1234567", "zenodo.1234567", "10.5281/zenodo.1234567", "https://zenodo.org/records/1234567",
                  "https://zenodo.org/api/records/1234567/files/x.pdf/content"):
            self.assertEqual(z.normalize_zenodo_id(t), "1234567", t)
        for t in ("", "abc", "10.1000/xyz", None):
            self.assertIsNone(z.normalize_zenodo_id(t))


class ParseTests(unittest.TestCase):
    def test_search_and_mapping(self):
        p, f = provider(payload(rec(), total=30))
        pg = p.search("dark matter", SearchField.TITLE, page=2, page_size=10)
        q = urllib.parse.parse_qs(urllib.parse.urlsplit(f.urls[0]).query)
        self.assertEqual((q["q"], q["size"], q["page"], q["type"], q["file_type"]),
                         (['title:"dark matter"'], ["10"], ["2"], ["publication"], ["pdf"]))
        self.assertTrue(f.urls[0].startswith("https://zenodo.org/api/records?"))
        r = pg.results[0]
        self.assertEqual((r.source, r.identifier, r.title, r.doi), ("zenodo", "zenodo.1234567", "Dark matter halos",
                                                                    "10.5281/zenodo.1234567"))
        self.assertEqual(r.authors, ["Albert Einstein", "Marie Curie"])
        self.assertEqual(r.abstract, "We study dark matter & halos.")
        self.assertEqual((r.published, r.publication_year), (date(2021, 3, 4), 2021))
        self.assertEqual(r.landing_page_url, "https://zenodo.org/records/1234567")
        self.assertEqual(r.pdf_url, "https://zenodo.org/api/records/1234567/files/paper.pdf/content")
        self.assertEqual(r.access_status, AccessStatus.OPEN)
        self.assertEqual((pg.total, pg.has_next, pg.page_size), (30, True, 10))

    def test_page_size_is_capped_at_anonymous_maximum(self):
        p, f = provider(payload())
        p.search("x", page_size=50)
        self.assertIn("size=25", f.urls[0])

    def test_pdf_selection_and_access(self):
        files = [{"key": "data.csv", "size": 5}, {"key": "Big.PDF", "size": 10 ** 10}, {"key": "my paper (v2).PDF", "size": 9}]
        r = z._parse_record(rec(files=files))
        self.assertEqual(r.pdf_url, "https://zenodo.org/api/records/1234567/files/my%20paper%20%28v2%29.PDF/content")
        self.assertIsNone(z._parse_record(rec(files=[{"key": "data.csv"}])).pdf_url)       # no pdf file
        self.assertIsNone(z._parse_record(rec(files=[{"key": "x.pdf", "size": 10 ** 10}])).pdf_url)  # too large
        self.assertIsNone(z._parse_record(rec(files=[])).pdf_url)
        for right, st in (("closed", AccessStatus.CLOSED), ("restricted", AccessStatus.CLOSED),
                          ("embargoed", AccessStatus.CLOSED), ("weird", AccessStatus.UNKNOWN)):
            r = z._parse_record(rec(right=right))
            self.assertEqual((r.access_status, r.pdf_url), (st, None), right)

    def test_odd_but_valid_records(self):
        r = z._parse_record(rec(publication_date="2019", creators=None, description=None))
        self.assertEqual((r.published, r.publication_year, r.authors, r.abstract), (None, 2019, [], None))
        self.assertIsNone(z._parse_record(rec(title="  ")))             # untitled records are skipped
        pg = z.parse_hits(payload(rec(title=""), rec(2, "ok")), 1, 5)
        self.assertEqual([r.title for r in pg.results], ["ok"])
        pg = z.parse_hits(json.dumps({"hits": {"hits": [rec()], "total": {"value": 3}}}).encode(), 1, 1)
        self.assertEqual((pg.total, pg.has_next), (3, True))

    def test_malformed_responses(self):
        for body in (b"not json", b"[]", b"{}", b'{"hits": []}', b'{"hits": {"hits": []}}',
                     json.dumps({"hits": {"hits": [{"id": "x", "metadata": {}}], "total": 1}}).encode(),
                     json.dumps({"hits": {"hits": ["str"], "total": 1}}).encode()):
            with self.assertRaises(ProviderResponseError, msg=body):
                z.parse_hits(body, 1, 5)

    def test_bad_paging_and_errors_propagate(self):
        p, _ = provider(payload())
        for bad in ((0, 5), (1, 0), ("1", 5), (1, None)):
            with self.assertRaises(InvalidQueryError):
                p.search("x", page=bad[0], page_size=bad[1])
        p, _ = provider(RuntimeError("boom"))
        with self.assertRaises(RuntimeError):
            p.search("x")

    def test_metadata_and_pdf_lookup(self):
        p, f = provider(json.dumps(rec()).encode())
        self.assertEqual(p.get_metadata("zenodo.1234567").title, "Dark matter halos")
        self.assertEqual(f.urls[0], "https://zenodo.org/api/records/1234567")
        self.assertTrue(p.get_open_access_pdf("1234567").endswith("/files/paper.pdf/content"))
        with self.assertRaises(InvalidQueryError):
            p.get_metadata("nope")
        p, _ = provider(NotFoundError("HTTP 404"))
        self.assertIsNone(p.get_metadata("1"))

    def test_throttle_between_calls(self):
        sleeps, now = [], [100.0]
        p = z.ZenodoProvider(fetch=lambda u: payload(), min_interval=2.0,
                             clock=lambda: now[0], sleep=sleeps.append)
        p.search("a"); p.search("b")
        self.assertEqual(sleeps, [2.0])


class DownloadTests(unittest.TestCase):
    def test_manager_downloads_only_from_zenodo_host(self):
        from external_search.tests.test_pdf import FakeResp, opener_for
        m = SearchManager(); m.register(z.ZenodoProvider(fetch=lambda u: payload(), min_interval=0))
        r = z._parse_record(rec())
        with tempfile.TemporaryDirectory() as d:
            op = opener_for(FakeResp(url=r.pdf_url))
            path = asyncio.run(m.download_pdf(r, d, opener=op))
            self.assertEqual(os.path.basename(path), "zenodo.1234567.pdf")
            self.assertEqual(op.calls[0][0], r.pdf_url)
            r.pdf_url = "https://evil.example/paper.pdf"
            with self.assertRaises(Exception) as c:
                asyncio.run(m.download_pdf(r, d, opener=op))
            self.assertEqual(type(c.exception).__name__, "PdfUnavailableError")


if __name__ == "__main__":
    unittest.main()
