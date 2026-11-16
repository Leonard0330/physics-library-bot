"""NASA ADS provider tests. Fixtures follow ADS's documented Solr-style response; no network."""
import asyncio
import json
import os
import tempfile
import unittest
import urllib.parse
import urllib.request
from unittest import mock

from external_search import (AccessStatus, InvalidQueryError, ProviderResponseError, SearchField,
                             SearchManager, build_manager, config)
from external_search.providers import _http, ads as a

TOKEN = "SECRET-TOKEN-123"
BIB = "2021ApJ...910L...1E"


def doc(bib=BIB, title="Dark matter halos in <SUB>galaxies</SUB>", **kw):
    d = {"bibcode": bib, "title": [title], "author": ["Einstein, Albert", "Curie, Marie"],
         "abstract": "We study <i>dark</i> matter &amp; halos.", "pubdate": "2021-03-00", "year": "2021",
         "doi": ["10.3847/2041-8213/abe0b3"], "pub": "The Astrophysical Journal Letters",
         "identifier": ["2021ApJ...910L...1E", "arXiv:2101.00001", "10.3847/2041-8213/abe0b3"],
         "property": ["ARTICLE", "REFEREED", "OPENACCESS", "EPRINT_OPENACCESS"]}
    d.update(kw)
    return d


def payload(*docs, found=None):
    return json.dumps({"responseHeader": {"status": 0},
                       "response": {"numFound": len(docs) if found is None else found, "start": 0,
                                    "docs": list(docs)}}).encode()


class Fetch:
    def __init__(self, body): self.body, self.urls = body, []

    def __call__(self, url):
        self.urls.append(url)
        if isinstance(self.body, Exception):
            raise self.body
        return self.body


def provider(body):
    f = Fetch(body)
    return a.AdsProvider(fetch=f, token=TOKEN, min_interval=0), f


class QueryTests(unittest.TestCase):
    def test_build_query(self):
        self.assertEqual(a.build_query('dark "matter"', SearchField.TITLE), 'title:"dark matter"')
        self.assertEqual(a.build_query("Albert Einstein", SearchField.AUTHOR), "author:(Albert AND Einstein)")
        self.assertEqual(a.build_query("dark matter", SearchField.KEYWORD), "abs:(dark AND matter)")

    def test_hostile_input(self):
        self.assertEqual(a.build_query("^x OR y) ; author:z \\", SearchField.KEYWORD), "abs:(x AND y AND author AND z)")
        for f in (SearchField.AUTHOR, SearchField.KEYWORD):
            for bad in ("", "  ", "^()[]:", "AND NOT"):
                with self.assertRaises(InvalidQueryError, msg=(f, bad)):
                    a.build_query(bad, f)
        with self.assertRaises(InvalidQueryError):
            a.build_query(' " ', SearchField.TITLE)

    def test_lookup_queries(self):
        self.assertEqual(a._lookup_query(BIB), f'bibcode:"{BIB}"')
        self.assertEqual(a._lookup_query("arXiv:2101.00001"), 'identifier:"arXiv:2101.00001"')
        self.assertEqual(a._lookup_query("https://doi.org/10.1103/PhysRevLett.1.1"), 'doi:"10.1103/physrevlett.1.1"')
        for bad in ("", "nope", 'x" OR bibcode:*'):
            with self.assertRaises(InvalidQueryError):
                a._lookup_query(bad)


class ParseTests(unittest.TestCase):
    def test_search_request_and_mapping(self):
        p, f = provider(payload(doc(), found=40))
        pg = p.search("dark matter", SearchField.KEYWORD, page=2, page_size=5)
        u = urllib.parse.urlsplit(f.urls[0])
        q = urllib.parse.parse_qs(u.query)
        self.assertEqual(f"{u.scheme}://{u.netloc}{u.path}", "https://api.adsabs.harvard.edu/v1/search/query")
        self.assertEqual((q["q"], q["rows"], q["start"]), (["abs:(dark AND matter)"], ["5"], ["5"]))
        self.assertNotIn(TOKEN, f.urls[0])                                  # the token never goes in the URL
        r = pg.results[0]
        self.assertEqual((r.source, r.identifier, r.doi, r.publication_year, r.published),
                         ("ads", BIB, "10.3847/2041-8213/abe0b3", 2021, None))
        self.assertEqual(r.title, "Dark matter halos in galaxies")           # tags stripped
        self.assertEqual(r.abstract, "We study dark matter & halos.")
        self.assertEqual(r.authors, ["Albert Einstein", "Marie Curie"])
        self.assertEqual(r.journal, "The Astrophysical Journal Letters")
        self.assertEqual(r.landing_page_url, f"https://ui.adsabs.harvard.edu/abs/{BIB}/abstract")
        self.assertEqual((r.pdf_url, r.access_status), ("https://arxiv.org/pdf/2101.00001", AccessStatus.OPEN))
        self.assertEqual((pg.total, pg.has_next), (40, True))

    def test_pdf_only_when_arxiv_eprint_exists(self):
        r = a._parse_doc(doc(identifier=["2021ApJ...910L...1E"], property=["ARTICLE"]))
        self.assertEqual((r.pdf_url, r.access_status), (None, AccessStatus.UNKNOWN))
        r = a._parse_doc(doc(identifier=["2021ApJ...910L...1E"], property=["OPENACCESS", "PUB_OPENACCESS"]))
        self.assertEqual((r.pdf_url, r.access_status), (None, AccessStatus.OPEN))     # open, but we only fetch arXiv
        r = a._parse_doc(doc(identifier=["arXiv:hep-th/9901001", "arXiv:evil/../x"]))
        self.assertEqual(r.pdf_url, "https://arxiv.org/pdf/hep-th/9901001")
        r = a._parse_doc(doc(identifier=["arXiv:../../etc/passwd"]))
        self.assertIsNone(r.pdf_url)

    def test_odd_documents(self):
        r = a._parse_doc(doc(title="Plain", author=None, abstract=None, doi=None, pub=None, year=None, pubdate="1999-01-00"))
        self.assertEqual((r.authors, r.abstract, r.doi, r.journal, r.publication_year), ([], None, None, None, 1999))
        self.assertIsNone(a._parse_doc(doc(title=[])))
        self.assertEqual(len(a.parse_response(payload(doc(title=[]), doc()), 1, 5).results), 1)

    def test_malformed(self):
        for body in (b"nope", b"[]", b"{}", b'{"response": []}', b'{"response": {"docs": []}}',
                     json.dumps({"response": {"numFound": 1, "docs": [{"title": ["x"]}]}}).encode(),
                     json.dumps({"response": {"numFound": 1, "docs": [{"bibcode": "bad", "title": ["x"]}]}}).encode(),
                     json.dumps({"response": {"numFound": 1, "docs": ["str"]}}).encode()):
            with self.assertRaises(ProviderResponseError, msg=body):
                a.parse_response(body, 1, 5)

    def test_metadata_and_paging(self):
        p, f = provider(payload(doc()))
        self.assertEqual(p.get_metadata(BIB).identifier, BIB)
        self.assertIn("bibcode", urllib.parse.unquote(f.urls[0]))
        self.assertEqual(p.get_open_access_pdf("arXiv:2101.00001"), "https://arxiv.org/pdf/2101.00001")
        p, _ = provider(payload())
        self.assertIsNone(p.get_metadata(BIB))
        for bad in ((0, 5), (1, 0), ("1", 5)):
            with self.assertRaises(InvalidQueryError):
                p.search("x", page=bad[0], page_size=bad[1])


class TokenTests(unittest.TestCase):
    def test_default_fetch_sends_bearer_header_only(self):
        with mock.patch.object(a, "http_get", return_value=payload()) as g:
            a.AdsProvider(token=TOKEN, min_interval=0).search("x")
        url, = g.call_args.args[:1]
        self.assertEqual(g.call_args.kwargs["headers"], {"Authorization": f"Bearer {TOKEN}"})
        self.assertNotIn(TOKEN, url)

    def test_missing_token_means_not_configured_and_never_calls_out(self):
        with mock.patch.object(config, "ADS_API_TOKEN", ""):
            self.assertFalse(a.AdsProvider.configured())
            self.assertNotIn("ads", build_manager().available())
            self.assertNotIn("ads", build_manager(["arxiv", "ads"]).available())
            with mock.patch.object(a, "http_get") as g, self.assertRaises(ProviderResponseError):
                a.AdsProvider(min_interval=0).search("x")
            g.assert_not_called()
        with mock.patch.object(config, "ADS_API_TOKEN", TOKEN):
            self.assertTrue(a.AdsProvider.configured())
            self.assertIn("ads", build_manager().available())

    def test_http_get_never_follows_redirects_when_authenticated(self):
        op = _http._build_opener(True)
        h = next(x for x in op.handlers if isinstance(x, _http._NoRedirect))
        self.assertIsNone(h.redirect_request(None, None, 302, "Found", {}, "https://evil.example/"))
        self.assertFalse(any(type(x) is urllib.request.HTTPRedirectHandler for x in op.handlers))
        plain = _http._build_opener(False)
        self.assertTrue(any(isinstance(x, urllib.request.HTTPRedirectHandler) and not isinstance(x, _http._NoRedirect)
                            for x in plain.handlers))

    def test_errors_do_not_leak_the_token(self):
        import urllib.error
        err = urllib.error.HTTPError("https://api.adsabs.harvard.edu/x", 401, "Unauthorized", {}, None)
        with mock.patch.object(urllib.request.OpenerDirector, "open", side_effect=err):
            with self.assertRaises(Exception) as c:
                _http.http_get("https://api.adsabs.harvard.edu/x", 5, 1000, "ADS", headers={"Authorization": f"Bearer {TOKEN}"})
        self.assertIn("HTTP 401", str(c.exception))
        self.assertNotIn(TOKEN, str(c.exception))


class DownloadTests(unittest.TestCase):
    def test_pdf_only_from_arxiv_hosts(self):
        from external_search.tests.test_pdf import FakeResp, opener_for
        m = SearchManager(); m.register(a.AdsProvider(fetch=lambda u: payload(), token=TOKEN, min_interval=0))
        r = a._parse_doc(doc())
        with tempfile.TemporaryDirectory() as d:
            op = opener_for(FakeResp(url=r.pdf_url))
            path = asyncio.run(m.download_pdf(r, d, opener=op))
            self.assertEqual(op.calls[0][0], "https://arxiv.org/pdf/2101.00001")
            self.assertTrue(os.path.basename(path).endswith(".pdf"))
            r.pdf_url = "https://ui.adsabs.harvard.edu/link_gateway/x/PUB_PDF"
            with self.assertRaises(Exception) as c:
                asyncio.run(m.download_pdf(r, d, opener=op))
            self.assertEqual(type(c.exception).__name__, "PdfUnavailableError")


if __name__ == "__main__":
    unittest.main()
