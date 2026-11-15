import asyncio
import unittest

from external_search import (BaseProvider, Capability, ProviderError, SearchFailedError,
                             SearchManager, SearchPage, SearchResult, UnsupportedOperation,
                             build_manager)
from external_search.utils.doi import extract_doi
from external_search.utils.pdf import is_pdf_bytes


def run(c): return asyncio.run(c)


class Fake(BaseProvider):
    name = "fake"
    capabilities = Capability.SEARCH

    def search(self, query, field=None, page=1, page_size=10):
        return SearchPage([SearchResult(title=query, source="")], page, page_size, total=25, has_next=True)


class Broken(BaseProvider):
    name = "broken"
    capabilities = Capability.SEARCH

    def search(self, *a, **k): raise ProviderError("down")


class Crash(Broken):
    name = "crash"

    def search(self, *a, **k): raise RuntimeError("boom")


class MetaOnly(BaseProvider):
    name = "meta"
    capabilities = Capability.METADATA


class ManagerTests(unittest.TestCase):
    def test_register_available(self):
        m = SearchManager(); m.register(Fake()); m.register(MetaOnly())
        self.assertEqual(m.available(), ["fake", "meta"])
        self.assertEqual(m.available(Capability.SEARCH), ["fake"])
        with self.assertRaises(ValueError):
            m.register(Fake())

    def test_search_merges_and_normalizes(self):
        m = SearchManager(); m.register(Fake())
        pg = run(m.search("x", page=2, page_size=5))
        self.assertEqual((pg.results[0].source, pg.page, pg.total, pg.has_next), ("fake", 2, 25, True))

    def test_partial_failure_reported(self):
        m = SearchManager()
        for p in (Fake(), Broken(), Crash()): m.register(p)
        pg = run(m.search("x"))
        self.assertEqual(len(pg.results), 1)
        self.assertEqual(set(pg.errors), {"broken", "crash"})

    def test_all_failed_raises(self):
        m = SearchManager(); m.register(Broken()); m.register(Crash())
        with self.assertRaises(SearchFailedError) as c:
            run(m.search("x"))
        self.assertEqual(set(c.exception.errors), {"broken", "crash"})

    def test_unsupported_unknown(self):
        m = SearchManager(); m.register(Fake())
        with self.assertRaises(UnsupportedOperation):
            run(m.get_metadata("2101.00001", "fake"))
        with self.assertRaises(KeyError):
            run(m.search("x", providers=["nope"]))
        with self.assertRaises(ProviderError):
            run(SearchManager().search("x"))

    def test_factory(self):
        self.assertEqual(build_manager().available(), ["arxiv"])
        with self.assertRaises(KeyError):
            build_manager(["nope"])


class UtilTests(unittest.TestCase):
    def test_doi(self):
        self.assertEqual(extract_doi("https://doi.org/10.1103/PhysRevLett.1.1."), "10.1103/physrevlett.1.1")
        self.assertIsNone(extract_doi("no doi"))

    def test_pdf(self):
        self.assertTrue(is_pdf_bytes(b"%PDF-1.7 ..."))
        self.assertFalse(is_pdf_bytes(b"<html>"))


if __name__ == "__main__":
    unittest.main()
