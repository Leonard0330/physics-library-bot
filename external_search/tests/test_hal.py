"""HAL provider tests. Fixtures are modeled on HAL's documented Solr fields; no network."""
import asyncio
import json
import os
import tempfile
import unittest
import urllib.parse
from datetime import date

from external_search import AccessStatus, InvalidQueryError, ProviderResponseError, SearchField, SearchManager
from external_search.providers import hal as h


def doc(hal_id="hal-01234567", title="Dark matter halos", oa=True, **kw):
    d = {"halId_s": hal_id, "title_s": [title], "authFullName_s": ["Albert Einstein", "Marie Curie"],
         "abstract_s": ["We study dark matter halos."], "producedDate_s": "2019-05-06",
         "publicationDateY_i": 2019, "doiId_s": "10.1000/XYZ", "uri_s": f"https://hal.science/{hal_id}",
         "fileMain_s": f"https://hal.science/{hal_id}/document", "openAccess_bool": oa,
         "journalTitle_s": "Phys. Rev. D"}
    d.update(kw)
    return d


def payload(*docs, found=None):
    return json.dumps({"response": {"numFound": len(docs) if found is None else found, "start": 0,
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
    return h.HalProvider(fetch=f, min_interval=0), f


class QueryTests(unittest.TestCase):
    def test_build_query(self):
        self.assertEqual(h.build_query('dark "matter"', SearchField.TITLE), 'title_t:"dark matter"')
        self.assertEqual(h.build_query("Albert Einstein", SearchField.AUTHOR), "authFullName_t:(Albert AND Einstein)")
        self.assertEqual(h.build_query("dark matter", SearchField.KEYWORD), "dark AND matter")

    def test_hostile_input(self):
        self.assertEqual(h.build_query("a OR b) -fq:* \\ NOT", SearchField.KEYWORD), "a AND b AND fq")
        for f in (SearchField.AUTHOR, SearchField.KEYWORD):
            for bad in ("", "  ", "():*", "AND OR"):
                with self.assertRaises(InvalidQueryError):
                    h.build_query(bad, f)
        with self.assertRaises(InvalidQueryError):
            h.build_query('""', SearchField.TITLE)

    def test_ids(self):
        self.assertEqual(h.normalize_hal_id("hal-01234567"), "hal-01234567")
        self.assertEqual(h.normalize_hal_id("https://hal.science/tel-02345678v2/document"), "tel-02345678v2")
        for t in ("", "hal-12", "x y", "../x", None):
            self.assertIsNone(h.normalize_hal_id(t))


class ParseTests(unittest.TestCase):
    def test_search_request_and_mapping(self):
        p, f = provider(payload(doc(), found=12))
        pg = p.search("dark matter", SearchField.KEYWORD, page=3, page_size=5)
        u = urllib.parse.urlsplit(f.urls[0])
        q = urllib.parse.parse_qs(u.query)
        self.assertEqual(f"{u.scheme}://{u.netloc}{u.path}", "https://api.archives-ouvertes.fr/search/")
        self.assertEqual((q["q"], q["rows"], q["start"], q["wt"]), (["dark AND matter"], ["5"], ["10"], ["json"]))
        self.assertEqual(q["fq"], ["openAccess_bool:true", "fileMain_s:[* TO *]"])
        r = pg.results[0]
        self.assertEqual((r.source, r.identifier, r.title, r.doi, r.journal),
                         ("hal", "hal-01234567", "Dark matter halos", "10.1000/XYZ", "Phys. Rev. D"))
        self.assertEqual(r.authors, ["Albert Einstein", "Marie Curie"])
        self.assertEqual((r.published, r.publication_year), (date(2019, 5, 6), 2019))
        self.assertEqual(r.landing_page_url, "https://hal.science/hal-01234567")
        self.assertEqual(r.pdf_url, "https://hal.science/hal-01234567/document")
        self.assertEqual(r.access_status, AccessStatus.OPEN)
        self.assertEqual((pg.total, pg.has_next), (12, True))      # items 10..10 of 12: more to come

    def test_pdf_host_rules(self):
        ok = ("https://hal.science/hal-1/document", "http://hal.archives-ouvertes.fr/hal-1/document",
              "https://theses.hal.science/tel-02345678/document", "https://hal-cea.archives-ouvertes.fr/x.pdf")
        for u in ok:
            r = h._parse_doc(doc(fileMain_s=u))
            self.assertTrue(r.pdf_url.startswith("https://"), u)
        bad = ("https://evil.example/x.pdf", "https://hal.science.evil.com/x.pdf", "https://evilhal.science/x.pdf",
               "ftp://hal.science/x.pdf", "javascript:alert(1)", None, 5)
        for u in bad:
            r = h._parse_doc(doc(fileMain_s=u))
            self.assertEqual((r.pdf_url, r.access_status), (None, AccessStatus.UNKNOWN), u)

    def test_access_and_fallbacks(self):
        closed = h._parse_doc(doc(oa=False))
        self.assertEqual((closed.pdf_url, closed.access_status), (None, AccessStatus.CLOSED))
        r = h._parse_doc(doc(title_s="Plain string title", authFullName_s=None, abstract_s=None, producedDate_s="2018",
                             publicationDateY_i=2018, uri_s="https://evil.example/x", doiId_s=None, journalTitle_s=None))
        self.assertEqual((r.title, r.authors, r.abstract, r.published, r.publication_year, r.doi),
                         ("Plain string title", [], None, None, 2018, None))
        self.assertEqual(r.landing_page_url, "https://hal.science/hal-01234567")       # untrusted uri ignored
        self.assertIsNone(h._parse_doc(doc(title_s=[""])))                                # untitled skipped

    def test_has_next(self):
        pg = h.parse_response(payload(doc(), found=12), 3, 5)       # items 10..: 10 + 1 < 12
        self.assertTrue(pg.has_next)
        self.assertFalse(h.parse_response(payload(doc(), found=11), 3, 5).has_next)

    def test_malformed(self):
        for body in (b"nope", b"[]", b"{}", b'{"response": []}', b'{"response": {"docs": []}}',
                     json.dumps({"response": {"numFound": 1, "docs": [{"title_s": ["x"]}]}}).encode(),
                     json.dumps({"response": {"numFound": 1, "docs": [{"halId_s": "../../x", "title_s": ["x"]}]}}).encode(),
                     json.dumps({"response": {"numFound": 1, "docs": ["str"]}}).encode()):
            with self.assertRaises(ProviderResponseError, msg=body):
                h.parse_response(body, 1, 5)

    def test_bad_paging_and_metadata(self):
        p, _ = provider(payload())
        for bad in ((0, 5), (1, 0), ("1", 5)):
            with self.assertRaises(InvalidQueryError):
                p.search("x", page=bad[0], page_size=bad[1])
        p, f = provider(payload(doc()))
        self.assertEqual(p.get_metadata("https://hal.science/hal-01234567").title, "Dark matter halos")
        self.assertIn("halId_s%3Ahal-01234567", f.urls[0])
        self.assertTrue(p.get_open_access_pdf("hal-01234567").endswith("/document"))
        with self.assertRaises(InvalidQueryError):
            p.get_metadata("nope")
        p, _ = provider(payload())
        self.assertIsNone(p.get_metadata("hal-01234567"))


class DownloadTests(unittest.TestCase):
    def test_manager_downloads_from_hal_hosts_only(self):
        from external_search.tests.test_pdf import FakeResp, opener_for
        m = SearchManager(); m.register(h.HalProvider(fetch=lambda u: payload(), min_interval=0))
        r = h._parse_doc(doc())
        with tempfile.TemporaryDirectory() as d:
            op = opener_for(FakeResp(url="https://hal.science/hal-01234567/file/paper.pdf"))   # same-host redirect
            path = asyncio.run(m.download_pdf(r, d, opener=op))
            self.assertEqual(os.path.basename(path), "hal-01234567.pdf")
            op = opener_for(FakeResp(url="https://evil.example/paper.pdf"))                    # redirect off-site
            with self.assertRaises(Exception) as c:
                asyncio.run(m.download_pdf(r, d, opener=op))
            self.assertEqual(type(c.exception).__name__, "PdfUnavailableError")
            self.assertEqual(os.listdir(d), [])


if __name__ == "__main__":
    unittest.main()
