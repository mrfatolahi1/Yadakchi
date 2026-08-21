from crawler.adapters.base import HtmlAdapter


class IsacoStoreAdapter(HtmlAdapter):
    key = "isacostore"
    discovery_urls = (
        "https://isacostore.com/shop/peugeot-car/peugeot-206",
        "https://isacostore.com/shop/peugeot-car/peugeot-405",
        "https://isacostore.com/shop/peugeot-car/peugeot-pars",
        "https://isacostore.com/shop/samand-car",
    )
    listing_selector = ".maxshop-products"
    url_selector = "a.product-link"
    title_selector = ".product-box-title h3"
    price_selector = ".product-box-price"
    image_selector = "img.product-image"
