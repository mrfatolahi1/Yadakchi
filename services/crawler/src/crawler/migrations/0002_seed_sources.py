from django.db import migrations


SOURCES = (
    {
        "key": "isacostore",
        "name": "ISACO Store",
        "base_url": "https://isacostore.com",
        "kind": "html",
        "adapter_key": "isacostore",
        "priority": 90,
        "politeness_delay_ms": 2500,
        "is_active": True,
    },
    {
        "key": "yadakyar",
        "name": "YadakYar",
        "base_url": "http://yadakyar.com",
        "kind": "html",
        "adapter_key": "yadakyar",
        "priority": 80,
        "politeness_delay_ms": 3000,
        "is_active": True,
    },
    {
        "key": "sarayyadak",
        "name": "Saray Yadak",
        "base_url": "https://sarayyadak.com",
        "kind": "html",
        "adapter_key": "sarayyadak",
        "priority": 75,
        "politeness_delay_ms": 3000,
        "is_active": True,
    },
    {
        "key": "partsmall",
        "name": "PartsMall",
        "base_url": "https://partsmall.ir",
        "kind": "html",
        "adapter_key": "pending",
        "priority": 50,
        "politeness_delay_ms": 3000,
        "is_active": False,
    },
    {
        "key": "mryadaki",
        "name": "Mr Yadaki",
        "base_url": "https://mryadaki.com",
        "kind": "html",
        "adapter_key": "pending",
        "priority": 45,
        "politeness_delay_ms": 3000,
        "is_active": False,
    },
    {
        "key": "shahreyadaki",
        "name": "Shahr Yadaki",
        "base_url": "https://www.shahreyadaki.com",
        "kind": "html",
        "adapter_key": "pending",
        "priority": 40,
        "politeness_delay_ms": 3500,
        "is_active": False,
    },
    {
        "key": "yadakmarket",
        "name": "Yadak Market",
        "base_url": "https://www.yadakmarket.com",
        "kind": "html",
        "adapter_key": "pending",
        "priority": 40,
        "politeness_delay_ms": 3500,
        "is_active": False,
    },
    {
        "key": "adrinyadak",
        "name": "Adrin Yadak",
        "base_url": "https://adrinyadak.com",
        "kind": "html",
        "adapter_key": "pending",
        "priority": 35,
        "politeness_delay_ms": 3500,
        "is_active": False,
    },
)


def seed_sources(apps: object, schema_editor: object) -> None:
    del schema_editor
    source_model = apps.get_model("crawler", "Source")
    for source in SOURCES:
        source_model.objects.update_or_create(key=source["key"], defaults=source)


def remove_sources(apps: object, schema_editor: object) -> None:
    del schema_editor
    source_model = apps.get_model("crawler", "Source")
    source_model.objects.filter(key__in=[source["key"] for source in SOURCES]).delete()


class Migration(migrations.Migration):
    dependencies = [("crawler", "0001_initial")]
    operations = [migrations.RunPython(seed_sources, remove_sources)]
