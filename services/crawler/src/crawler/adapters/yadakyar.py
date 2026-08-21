from crawler.adapters.base import HtmlAdapter


class YadakYarAdapter(HtmlAdapter):
    key = "yadakyar"
    discovery_urls = (
        "http://yadakyar.com/product/2373398-تسمه-تایم-پراید-پاورگریپ-بارمان-کیمیا/",
        "http://yadakyar.com/product/1923145-سنسور-اکسیژن-بالا-تیبا-سایپا-یدک/",
    )
    listing_selector = "html"
    external_key_selector = 'meta[name="product_id"]'
    external_key_attribute = "content"
    url_selector = None
    title_selector = 'meta[name="product_name"]'
    title_attribute = "content"
    price_selector = 'meta[name="product_price"]'
    price_attribute = "content"
    stock_selector = 'meta[name="availability"]'
    stock_attribute = "content"
    image_selector = 'meta[property="og:image"]'
    image_attribute = "content"
    fragment_selector = "head"
