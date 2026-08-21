from crawler.adapters.base import HtmlAdapter


class SarayYadakAdapter(HtmlAdapter):
    key = "sarayyadak"
    discovery_urls = (
        "https://sarayyadak.com/product/8/تسمه-تایم-پژو-405-پاورگریپ-gates-اصلی/",
        "https://sarayyadak.com/product/10/لنت-ترمز-جلو-پژو-206-تیپ-5-ایساکو/",
        "https://sarayyadak.com/product/27/مجموعه-بلبرینگ-تایم-سمند-ef7-هرینگتون/",
        "https://sarayyadak.com/product/34/مغزی-پمپ-بنزین-پژو-405-پارس/",
    )
    listing_selector = "html"
    external_key_selector = 'meta[name="product_id"]'
    external_key_attribute = "content"
    url_selector = 'link[rel="canonical"]'
    title_selector = 'meta[name="product_name"]'
    title_attribute = "content"
    price_selector = 'meta[name="product_price"]'
    price_attribute = "content"
    stock_selector = 'meta[name="availability"]'
    stock_attribute = "content"
    image_selector = 'meta[property="og:image"]'
    image_attribute = "content"
    fragment_selector = "head"
