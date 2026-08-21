# Saved seller pages

These are complete response bodies fetched on 2026-08-21 with
`YadakchiCrawler/1.0 (+https://yadakchi.ir/crawler)`. Each URL was allowed by the source's
`robots.txt` at fetch time. Files are gzip-compressed with a deterministic header; the hashes below
are SHA-256 of the decompressed bytes.

| Fixture | Source URL | Raw bytes | Raw SHA-256 |
|---|---|---:|---|
| `isacostore.html.gz` | `https://isacostore.com/shop/peugeot-car/peugeot-206` | 72,244 | `4c8d8a9907100ecf1abaf942b4387971ea3d552087480544778870936a028f14` |
| `sarayyadak.html.gz` | `https://sarayyadak.com/product/27/...` | 368,339 | `95dc2979e38d8574274e2b77e67fded498f1d2d974f9a343f5b2f745a110b0de` |
| `yadakyar.html.gz` | `http://yadakyar.com/product/2373398-...` | 477,226 | `b520d4dcec21efc5a465cfa5cd566f6effdc8ad8b33489910f6bc449621d468f` |

The abbreviated Persian slugs are documentation only; adapter tests pass the original fetched URL,
and each response contains its canonical/product identifiers. Fixtures must only be refreshed after
checking `robots.txt` again and reviewing selector changes.
