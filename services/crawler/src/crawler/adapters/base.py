import csv
import io
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import ClassVar, Protocol
from urllib.parse import urljoin, urlparse
from xml.etree import ElementTree

from selectolax.parser import HTMLParser, Node

from crawler.models import Source


@dataclass(frozen=True, slots=True)
class ListingStub:
    external_key: str
    url: str
    raw_title: str
    raw_price_text: str | None
    raw_stock_text: str | None
    image_url: str | None
    raw_fragment: str


class Adapter(Protocol):
    key: str

    def discover(self, source: Source) -> Iterator[str]: ...

    def extract_listings(self, raw: bytes, url: str) -> list[ListingStub]: ...


def node_value(node: Node | None, attribute: str | None = None) -> str | None:
    if node is None:
        return None
    value = node.attributes.get(attribute) if attribute else node.text(separator=" ", strip=True)
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


class HtmlAdapter:
    key: ClassVar[str]
    discovery_urls: ClassVar[tuple[str, ...]] = ()
    listing_selector: ClassVar[str]
    external_key_selector: ClassVar[str | None] = None
    external_key_attribute: ClassVar[str | None] = None
    url_selector: ClassVar[str | None]
    url_attribute: ClassVar[str] = "href"
    title_selector: ClassVar[str]
    title_attribute: ClassVar[str | None] = None
    price_selector: ClassVar[str | None] = None
    price_attribute: ClassVar[str | None] = None
    stock_selector: ClassVar[str | None] = None
    stock_attribute: ClassVar[str | None] = None
    image_selector: ClassVar[str | None] = None
    image_attribute: ClassVar[str] = "src"
    fragment_selector: ClassVar[str | None] = None

    def discover(self, source: Source) -> Iterator[str]:
        del source
        yield from self.discovery_urls

    def external_key(self, listing: Node, listing_url: str) -> str:
        if self.external_key_selector:
            value = node_value(
                listing.css_first(self.external_key_selector), self.external_key_attribute
            )
            if value:
                return value
        path = PurePosixPath(urlparse(listing_url).path.rstrip("/"))
        return path.name or listing_url

    def extract_listings(self, raw: bytes, url: str) -> list[ListingStub]:
        tree = HTMLParser(raw)
        stubs: list[ListingStub] = []
        for listing in tree.css(self.listing_selector):
            url_value = (
                node_value(listing.css_first(self.url_selector), self.url_attribute)
                if self.url_selector
                else url
            )
            title = node_value(listing.css_first(self.title_selector), self.title_attribute)
            if not url_value or not title:
                continue
            listing_url = urljoin(url, url_value)
            image = None
            if self.image_selector:
                image_value = node_value(
                    listing.css_first(self.image_selector), self.image_attribute
                )
                image = urljoin(url, image_value) if image_value else None
            fragment_node = (
                listing.css_first(self.fragment_selector) if self.fragment_selector else listing
            )
            fragment = fragment_node.html if fragment_node is not None else listing.html
            if not fragment:
                continue
            stubs.append(
                ListingStub(
                    external_key=self.external_key(listing, listing_url),
                    url=listing_url,
                    raw_title=title,
                    raw_price_text=(
                        node_value(listing.css_first(self.price_selector), self.price_attribute)
                        if self.price_selector
                        else None
                    ),
                    raw_stock_text=(
                        node_value(listing.css_first(self.stock_selector), self.stock_attribute)
                        if self.stock_selector
                        else None
                    ),
                    image_url=image,
                    raw_fragment=fragment,
                )
            )
        return stubs


class FeedAdapter:
    key: ClassVar[str]
    discovery_urls: ClassVar[tuple[str, ...]] = ()

    def discover(self, source: Source) -> Iterator[str]:
        del source
        yield from self.discovery_urls

    def extract_listings(self, raw: bytes, url: str) -> list[ListingStub]:
        stripped = raw.lstrip()
        if stripped.startswith(b"<"):
            return self._extract_xml(raw, url)
        return self._extract_csv(raw, url)

    def _extract_xml(self, raw: bytes, url: str) -> list[ListingStub]:
        root = ElementTree.fromstring(raw)
        listings: list[ListingStub] = []
        for item in root.findall(".//item"):
            values = {child.tag.rsplit("}", 1)[-1]: child.text for child in item}
            external_key = values.get("id")
            title = values.get("title")
            link = values.get("link")
            if not external_key or not title or not link:
                continue
            listings.append(
                ListingStub(
                    external_key=external_key,
                    url=urljoin(url, link),
                    raw_title=title,
                    raw_price_text=values.get("price"),
                    raw_stock_text=values.get("availability"),
                    image_url=(
                        urljoin(url, values["image_link"]) if values.get("image_link") else None
                    ),
                    raw_fragment=ElementTree.tostring(item, encoding="unicode"),
                )
            )
        return listings

    def _extract_csv(self, raw: bytes, url: str) -> list[ListingStub]:
        text = raw.decode("utf-8-sig")
        listings: list[ListingStub] = []
        for row in csv.DictReader(io.StringIO(text)):
            external_key = row.get("id")
            title = row.get("title")
            link = row.get("link")
            if not external_key or not title or not link:
                continue
            fragment = ",".join(f"{key}={value}" for key, value in row.items())
            image_link = row.get("image_link")
            listings.append(
                ListingStub(
                    external_key=external_key,
                    url=urljoin(url, link),
                    raw_title=title,
                    raw_price_text=row.get("price"),
                    raw_stock_text=row.get("availability"),
                    image_url=urljoin(url, image_link) if image_link else None,
                    raw_fragment=fragment,
                )
            )
        return listings
