# Adding an adapter

Adapters only identify listing boundaries and copy source fields verbatim. They must not parse
money, normalize Persian text, infer brands, classify authenticity, or infer vehicle fitment.

1. Confirm the source permits the intended URLs in `robots.txt` and prefer a feed when offered.
2. Save one permitted real response as `tests/fixtures/<source>.html.gz`, plus its URL and fetch
   date in `tests/fixtures/README.md`.
3. Subclass `HtmlAdapter` and declare `listing_selector`, link/title/price/stock/image selectors,
   and `discovery_urls`. Product-detail pages can use metadata selectors; listing pages should use
   the smallest repeated product-card element as `listing_selector`.
4. Add the instance to `crawler.adapters._ADAPTERS` and add a fixture test that asserts the exact
   raw strings and fragment hash.
5. Seed the `Source` as inactive first. Run a crawl, inspect `AdapterHealth`, then activate it with a
   conservative delay. A source without a healthy baseline must never be promoted silently.

The existing adapters are intentionally small. If a new adapter needs content interpretation, that
logic belongs downstream rather than here.
