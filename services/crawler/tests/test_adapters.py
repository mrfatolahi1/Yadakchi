import hashlib

from crawler.adapters import get_adapter, registered_adapter_keys
from tests.helpers import fixture_bytes

YADAKYAR_URL = (
    "http://yadakyar.com/product/2373398-%D8%AA%D8%B3%D9%85%D9%87-%D8%AA%D8%A7%DB%8C%D9%85-"
    "%D9%BE%D8%B1%D8%A7%DB%8C%D8%AF-%D9%BE%D8%A7%D9%88%D8%B1%DA%AF%D8%B1%DB%8C%D9%BE-"
    "%D8%A8%D8%A7%D8%B1%D9%85%D8%A7%D9%86-%DA%A9%DB%8C%D9%85%DB%8C%D8%A7/"
)
SARAYYADAK_URL = "https://sarayyadak.com/product/27/x/"
ISACO_URL = "https://isacostore.com/shop/peugeot-car/peugeot-206"


def test_three_adapters_are_registered() -> None:
    assert registered_adapter_keys() == ("isacostore", "sarayyadak", "yadakyar")


def test_yadakyar_saved_real_page_extracts_expected_stub() -> None:
    listings = get_adapter("yadakyar").extract_listings(fixture_bytes("yadakyar"), YADAKYAR_URL)

    assert len(listings) == 1
    listing = listings[0]
    assert listing.external_key == "67487"
    assert listing.url == YADAKYAR_URL
    assert listing.raw_title == "تسمه تایم پراید پاورگریپ بارمان کیمیا"
    assert listing.raw_price_text == "0"
    assert listing.raw_stock_text == "outofstock"
    assert hashlib.sha256(listing.raw_fragment.encode()).hexdigest() == (
        "3bc8e339955a21412d77912e010e0cd66d8d5654ede1db3d37632a04bf998480"
    )


def test_isacostore_saved_real_page_extracts_expected_stubs() -> None:
    listings = get_adapter("isacostore").extract_listings(fixture_bytes("isacostore"), ISACO_URL)

    assert len(listings) == 12
    listing = listings[0]
    assert listing.external_key == "peugeot-206-gearbox-breather-cap"
    assert listing.raw_title == "هواکش پوسته گیربکس پژو 206 ایساکو"
    assert listing.raw_price_text == "۲۳,۰۰۰  تومان"
    assert listing.raw_stock_text is None
    assert hashlib.sha256(listing.raw_fragment.encode()).hexdigest() == (
        "2551645c3601ad13be0c2dea0b001ce839dab8d8583a9521b5e86e1f165fe305"
    )


def test_sarayyadak_saved_real_page_extracts_expected_stub() -> None:
    listings = get_adapter("sarayyadak").extract_listings(
        fixture_bytes("sarayyadak"), SARAYYADAK_URL
    )

    assert len(listings) == 1
    listing = listings[0]
    assert listing.external_key == "27"
    assert listing.raw_title == ("مجموعه بلبرینگ تایم ثابت و متحرک  سمند EF7 برند هرینگتون اصلی")
    assert listing.raw_price_text == "2956000"
    assert listing.raw_stock_text == "instock"
    assert hashlib.sha256(listing.raw_fragment.encode()).hexdigest() == (
        "f1ae321ffc647423c9690f75f391c71da9d8dd00e8d02721c997a73021bf356c"
    )
