"""Multi-provider merging in SearchManager: page size, interleaving, de-duplication."""
import asyncio
import unittest

from external_search import (AccessStatus, BaseProvider, Capability, ProviderError, SearchManager,
                             SearchPage, SearchResult)


def res(title, source, doi=None, pdf=True, ident=None):
    return SearchResult(title=title, source=source, identifier=ident or title[:6], doi=doi,
                        pdf_url="https://x/y.pdf" if pdf else None,
                        access_status=AccessStatus.OPEN if pdf else AccessStatus.UNKNOWN)


def make(name, items, total=None, fail=False):
    class P(BaseProvider):
        capabilities = Capability.SEARCH
        calls = []

        def search(self, query, field=None, page=1, page_size=10):
            P.calls.append((page, page_size))
            if fail:
                raise ProviderError("down")
            return SearchPage(items[:page_size], page, page_size, total=total or len(items), has_next=True)
    P.name = name
    return P()


def run(m, **kw): return asyncio.run(m.search("q", **kw))


class MergeTests(unittest.TestCase):
    def test_page_size_is_shared_between_providers(self):
        a, b, c = (make(n, [res(f"{n} paper number {i}", n) for i in range(10)]) for n in "abc")
        m = SearchManager()
        for p in (a, b, c): m.register(p)
        pg = run(m, page=2, page_size=5)
        self.assertEqual([type(p).calls[-1] for p in (a, b, c)], [(2, 2)] * 3)    # ceil(5/3) each
        self.assertEqual((len(pg.results), pg.page_size, pg.page), (6, 6, 2))     # one row of <= 8 buttons
        self.assertEqual([r.source for r in pg.results], ["a", "b", "c", "a", "b", "c"])  # round-robin

    def test_single_provider_behaviour_unchanged(self):
        a = make("a", [res(f"paper number {i} here", "a") for i in range(9)])
        m = SearchManager(); m.register(a)
        pg = run(m, page=1, page_size=5)
        self.assertEqual((len(pg.results), pg.page_size), (5, 5))
        self.assertEqual(type(a).calls[-1], (1, 5))

    def test_page_size_stays_stable_when_a_provider_fails(self):
        a = make("a", [res(f"a paper number {i}", "a") for i in range(9)])
        b = make("b", [], fail=True)
        m = SearchManager(); m.register(a); m.register(b)
        pg = run(m, page=1, page_size=5)
        self.assertEqual((pg.page_size, set(pg.errors)), (6, {"b"}))              # numbering stays consistent

    def test_dedup_by_doi_arxiv_id_and_title(self):
        a = make("arxiv", [res("Dark Matter Halos In Galaxies", "arxiv", doi="10.1/ABC", ident="2101.00001v2"),
                           res("Unrelated first paper title", "arxiv", ident="2101.00002")])
        b = make("zenodo", [res("A totally different title text", "zenodo", doi="https://doi.org/10.1/abc"),
                            res("Unrelated first paper title!", "zenodo")])
        c = make("hal", [res("dark matter halos in galaxies", "hal"), res("Short", "hal"), res("Short", "hal")])
        m = SearchManager()
        for p in (a, b, c): m.register(p)
        titles = [r.title for r in run(m, page_size=9).results]
        self.assertEqual(len(titles), len(set(t.lower().strip("!") for t in titles)) + 1)  # only the two "Short" stay
        self.assertEqual(sum(t == "Dark Matter Halos In Galaxies" or t.lower() == "dark matter halos in galaxies" for t in titles), 1)
        self.assertEqual(sum(t.startswith("Unrelated first") for t in titles), 1)
        self.assertEqual(titles.count("Short"), 2)                                # short titles never merged

    def test_duplicate_with_pdf_replaces_record_without_pdf(self):
        a = make("arxiv", [res("Same paper with a long title", "arxiv", doi="10.1/x", pdf=False)])
        b = make("zenodo", [res("Same paper with a long title", "zenodo", doi="10.1/x", pdf=True)])
        m = SearchManager(); m.register(a); m.register(b)
        (r,) = run(m).results
        self.assertEqual((r.source, bool(r.pdf_url)), ("zenodo", True))
        a = make("arxiv", [res("Same paper with a long title", "arxiv", doi="10.1/x", pdf=True)])
        m = SearchManager(); m.register(a); m.register(b)
        self.assertEqual(run(m).results[0].source, "arxiv")                       # tie -> first provider wins

    def test_arxiv_copy_found_through_any_provider_is_deduplicated(self):
        a = make("arxiv", [SearchResult(title="Short A", source="arxiv", identifier="2101.00001v2",
                                        pdf_url="https://arxiv.org/pdf/2101.00001v2", access_status=AccessStatus.OPEN)])
        b = make("ads", [SearchResult(title="Other title entirely", source="ads", identifier="2021ApJ...910L...1E",
                                      pdf_url="https://arxiv.org/pdf/2101.00001", access_status=AccessStatus.OPEN),
                         SearchResult(title="Different paper", source="ads", identifier="2021ApJ...910L...2E",
                                      pdf_url="https://arxiv.org/pdf/2101.00002", access_status=AccessStatus.OPEN)])
        c = make("hal", [SearchResult(title="Yet another title", source="hal", identifier="hal-01234567",
                                      landing_page_url="https://arxiv.org/abs/2101.00002v1")])
        m = SearchManager()
        for p in (a, b, c): m.register(p)
        got = [(r.source, r.identifier) for r in run(m, page_size=9).results]
        self.assertEqual(got, [("arxiv", "2101.00001v2"), ("ads", "2021ApJ...910L...2E")])
        for url in ("https://example.org/arxiv.org/pdf/2101.00001", "http://arxiv.org/pdf/2101.00001"):
            from external_search.manager import _dup_keys
            self.assertFalse([k for k in _dup_keys(SearchResult(title="x", source="s", pdf_url=url)) if k.startswith("arxiv:")], url)

    def test_totals(self):
        a, b = make("a", [res("a paper title number 1", "a")], total=10), make("b", [res("b paper title number 1", "b")], total=5)
        m = SearchManager(); m.register(a); m.register(b)
        self.assertEqual(run(m).total, 15)
        c = make("c", [res("c paper title number 1", "c")], total=None)
        c.search = lambda *a, **k: SearchPage([res("c paper title number 1", "c")], 1, 5, total=None)
        m.register(c)
        self.assertIsNone(run(m).total)


if __name__ == "__main__":
    unittest.main()
