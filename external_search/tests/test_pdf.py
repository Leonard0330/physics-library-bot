"""PDF download/validation tests. All HTTP is mocked; nothing touches the network."""
import asyncio
import http.client
import os
import socket
import tempfile
import unittest
import urllib.error
import urllib.request
from unittest import mock

from external_search import (AccessStatus, InvalidPdfError, PdfTooLargeError, PdfUnavailableError,
                             ProviderNetworkError, ProviderTimeout, SearchManager, SearchResult,
                             UnsupportedOperation)
from external_search.providers.arxiv import ArxivProvider
from external_search.utils import pdf as pdfu

HOSTS = {"arxiv.org"}
URL = "https://arxiv.org/pdf/2101.00001v2"
PDF = b"%PDF-1.5\n" + b"x" * 200 + b"\n%%EOF\n"


class FakeResp:
    def __init__(self, body=PDF, status=200, ctype="application/pdf", length="auto",
                 url=URL, read_error=None, chunk=50):
        self.status, self._body, self._url, self._err, self._chunk = status, body, url, read_error, chunk
        self.headers = {}
        if ctype is not None:
            self.headers["Content-Type"] = ctype
        if length == "auto":
            length = len(body)
        if length is not None:
            self.headers["Content-Length"] = str(length)
        self._pos = 0

    def read(self, n=-1):
        if self._err and self._pos > 0:
            raise self._err
        out = self._body[self._pos:self._pos + min(n, self._chunk)]
        self._pos += len(out)
        return out

    def geturl(self):
        return self._url

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def opener_for(resp=None, exc=None):
    calls = []

    def op(url, timeout):
        calls.append((url, timeout))
        if exc:
            raise exc
        return resp
    op.calls = calls
    return op


class NoNetwork(unittest.TestCase):
    def setUp(self):   # any accidental real request fails the test
        p = mock.patch.object(pdfu, "_default_opener", side_effect=AssertionError("real network"))
        p.start(); self.addCleanup(p.stop)
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)

    def dl(self, opener, url=URL, **kw):
        return pdfu.download_pdf(url, self.tmp.name, "p.pdf", allowed_hosts=HOSTS, opener=opener, **kw)

    def assertNoFile(self):
        self.assertEqual(os.listdir(self.tmp.name), [])


class ValidDownload(NoNetwork):
    def test_saves_real_pdf(self):
        op = opener_for(FakeResp())
        path = self.dl(op)
        with open(path, "rb") as f:
            self.assertEqual(f.read(), PDF)
        self.assertEqual(op.calls[0][0], URL)
        self.assertGreater(op.calls[0][1], 0)       # a timeout is always passed

    def test_octet_stream_and_missing_content_type_ok_with_signature(self):
        for ct in ("application/octet-stream", None, "application/pdf; charset=binary"):
            self.assertTrue(os.path.getsize(self.dl(opener_for(FakeResp(ctype=ct)))) > 0)

    def test_no_content_length_ok(self):
        self.assertTrue(self.dl(opener_for(FakeResp(length=None))))


class Rejections(NoNetwork):
    def test_html_content_type(self):
        for ct in ("text/html; charset=utf-8", "application/xhtml+xml", "text/plain"):
            with self.assertRaises(InvalidPdfError):
                self.dl(opener_for(FakeResp(body=b"<html>no</html>", ctype=ct)))
            self.assertNoFile()

    def test_html_body_with_pdf_content_type(self):
        with self.assertRaises(InvalidPdfError):
            self.dl(opener_for(FakeResp(body=b"<!doctype html><html>Error</html>")))
        self.assertNoFile()

    def test_long_html_body_aborts_early(self):
        resp = FakeResp(body=b"<html>" + b"a" * 5000, length=None, chunk=2000)
        with self.assertRaises(InvalidPdfError):
            self.dl(opener_for(resp))
        self.assertLess(resp._pos, 5000)
        self.assertNoFile()

    def test_empty_file(self):
        with self.assertRaises(InvalidPdfError):
            self.dl(opener_for(FakeResp(body=b"")))
        self.assertNoFile()

    def test_non_200_status(self):
        for st in (201, 202, 204, 206):
            with self.assertRaises(PdfUnavailableError):
                self.dl(opener_for(FakeResp(status=st)))
        self.assertNoFile()

    def test_http_errors(self):
        def err(code):
            return urllib.error.HTTPError(URL, code, "x", {}, None)
        for code in (403, 404, 410):
            with self.assertRaises(PdfUnavailableError):
                self.dl(opener_for(exc=err(code)))
        for code in (429, 500, 503):
            with self.assertRaises(ProviderNetworkError):
                self.dl(opener_for(exc=err(code)))

    def test_timeouts(self):
        for e in (socket.timeout("t"), urllib.error.URLError(socket.timeout("t"))):
            with self.assertRaises(ProviderTimeout):
                self.dl(opener_for(exc=e))
        with self.assertRaises(ProviderTimeout):      # stalls while streaming
            self.dl(opener_for(FakeResp(read_error=socket.timeout("t"))))
        self.assertNoFile()

    def test_total_deadline(self):
        ticks = iter(range(0, 1000, 10))
        with self.assertRaises(ProviderTimeout):
            self.dl(opener_for(FakeResp(chunk=10)), total_timeout=25, clock=lambda: next(ticks))
        self.assertNoFile()

    def test_connection_errors(self):
        for e in (urllib.error.URLError(ConnectionRefusedError("no")), ConnectionResetError("rst")):
            with self.assertRaises(ProviderNetworkError):
                self.dl(opener_for(exc=e))
        with self.assertRaises(ProviderNetworkError):
            self.dl(opener_for(FakeResp(read_error=ConnectionResetError("rst"))))
        self.assertNoFile()

    def test_too_large_declared_and_streamed(self):
        with self.assertRaises(PdfTooLargeError):
            self.dl(opener_for(FakeResp(length=10**9)), max_bytes=1000)
        with self.assertRaises(PdfTooLargeError):    # no/lying Content-Length
            self.dl(opener_for(FakeResp(body=PDF + b"y" * 5000, length=None)), max_bytes=1000)
        self.assertNoFile()

    def test_incomplete(self):
        with self.assertRaises(InvalidPdfError):
            self.dl(opener_for(FakeResp(length=len(PDF) + 100)))
        with self.assertRaises(InvalidPdfError):
            self.dl(opener_for(FakeResp(read_error=http.client.IncompleteRead(b"x"))))
        self.assertNoFile()

    def test_disallowed_urls_never_requested(self):
        op = opener_for(FakeResp())
        for u in ("http://arxiv.org/pdf/1", "https://evil.example/x.pdf", "https://arxiv.org.evil.com/p",
                  "ftp://arxiv.org/p", "file:///etc/passwd", "", None, "https://user@evil.com/p"):
            with self.assertRaises(PdfUnavailableError):
                self.dl(op, url=u)
        self.assertEqual(op.calls, [])

    def test_final_url_after_redirect_must_be_allowed(self):
        with self.assertRaises(PdfUnavailableError):
            self.dl(opener_for(FakeResp(url="https://evil.example/f.pdf")))
        self.assertNoFile()

    def test_redirect_handler_checks_target(self):
        h = pdfu._CheckedRedirects(frozenset(HOSTS))
        req = urllib.request.Request(URL)
        with self.assertRaises(PdfUnavailableError):
            h.redirect_request(req, None, 302, "Found", {}, "https://evil.example/x")
        with self.assertRaises(PdfUnavailableError):
            h.redirect_request(req, None, 302, "Found", {}, "http://arxiv.org/pdf/1")
        self.assertIsNotNone(h.redirect_request(req, None, 302, "Found", {}, "https://arxiv.org/pdf/2101.00001v3"))


class Helpers(unittest.TestCase):
    def test_signature_and_filename(self):
        self.assertTrue(pdfu.is_pdf_bytes(b"%PDF-1.7"))
        self.assertFalse(pdfu.is_pdf_bytes(b"<html>"))
        self.assertFalse(pdfu.is_pdf_bytes(b""))
        self.assertEqual(pdfu.safe_filename("2101.00001v2"), "2101.00001v2.pdf")
        self.assertEqual(pdfu.safe_filename("hep-th/9901001"), "hep-th_9901001.pdf")
        self.assertEqual(pdfu.safe_filename("../../etc/passwd"), "etc_passwd.pdf")
        self.assertEqual(pdfu.safe_filename(None), "paper.pdf")


class ManagerDownload(NoNetwork):
    def mgr(self):
        m = SearchManager(); m.register(ArxivProvider(fetch=lambda u: self.fail("search used")))
        return m

    def res(self, **kw):
        d = dict(title="T", source="arxiv", identifier="2101.00001v2", pdf_url=URL,
                 access_status=AccessStatus.OPEN)
        d.update(kw)
        return SearchResult(**d)

    def run_dl(self, r, op):
        return asyncio.run(self.mgr().download_pdf(r, self.tmp.name, opener=op))

    def test_downloads_record_pdf_url_only(self):
        op = opener_for(FakeResp())
        path = self.run_dl(self.res(), op)
        self.assertEqual(os.path.basename(path), "2101.00001v2.pdf")
        self.assertEqual([c[0] for c in op.calls], [URL])      # exactly the record's link

    def test_no_pdf_link_or_closed_access(self):
        op = opener_for(FakeResp())
        for r in (self.res(pdf_url=None), self.res(access_status=AccessStatus.CLOSED)):
            with self.assertRaises(PdfUnavailableError):
                self.run_dl(r, op)
        self.assertEqual(op.calls, [])

    def test_link_must_be_on_provider_host(self):
        op = opener_for(FakeResp())
        with self.assertRaises(PdfUnavailableError):
            self.run_dl(self.res(pdf_url="https://example.org/a.pdf"), op)
        self.assertEqual(op.calls, [])

    def test_unknown_provider_and_unsupported(self):
        with self.assertRaises(KeyError):
            self.run_dl(self.res(source="nope"), opener_for(FakeResp()))
        from external_search import BaseProvider, Capability

        class Plain(BaseProvider):
            name = "plain"; capabilities = Capability.SEARCH
        m = SearchManager(); m.register(Plain())
        with self.assertRaises(UnsupportedOperation):
            asyncio.run(m.download_pdf(self.res(source="plain"), self.tmp.name))

    def test_invalid_pdf_propagates_and_leaves_nothing(self):
        with self.assertRaises(InvalidPdfError):
            self.run_dl(self.res(), opener_for(FakeResp(body=b"<html></html>")))
        self.assertNoFile()


if __name__ == "__main__":
    unittest.main()


class HostSuffixTests(unittest.TestCase):
    def test_suffix_entries(self):
        hosts = {"hal.science", ".hal.science"}
        self.assertTrue(pdfu.host_allowed("hal.science", hosts))
        self.assertTrue(pdfu.host_allowed("theses.hal.science", hosts))
        self.assertTrue(pdfu.host_allowed("A.B.HAL.science", hosts))
        for bad in ("evilhal.science", "hal.science.evil.com", "science", ".hal.science", "", None):
            self.assertFalse(pdfu.host_allowed(bad, hosts), bad)
        self.assertFalse(pdfu.host_allowed("theses.hal.science", {"hal.science"}))     # exact entries stay exact
        self.assertFalse(pdfu.host_allowed("hal.science", {".hal.science"}))           # suffix needs a subdomain
