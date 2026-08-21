from __future__ import annotations

from django.core.management import call_command

from fitment.models import OutboxEvent, Vehicle
from fitment.resolver import resolve_vehicle


def test_seed_is_idempotent_and_alias_edit_emits_once(db: object) -> None:
    del db
    call_command("seed_vehicles")
    assert Vehicle.objects.count() == 29
    initial_events = OutboxEvent.objects.filter(topic="yadakchi.vehicles.changed.v1").count()
    assert initial_events == 29

    call_command("seed_vehicles")
    assert Vehicle.objects.count() == 29
    assert (
        OutboxEvent.objects.filter(topic="yadakchi.vehicles.changed.v1").count() == initial_events
    )

    vehicle = Vehicle.objects.get(slug="peugeot-206-type-5")
    vehicle.aliases = [*vehicle.aliases, "206 t5"]
    vehicle.save()
    assert (
        OutboxEvent.objects.filter(topic="yadakchi.vehicles.changed.v1").count()
        == initial_events + 1
    )


def test_resolver_handles_more_than_fifty_seed_aliases(seeded: None) -> None:
    checked = 0
    for vehicle in Vehicle.objects.all():
        for alias in vehicle.aliases:
            assert resolve_vehicle(alias) == vehicle.slug
            checked += 1
            if checked >= 75:
                break
        if checked >= 75:
            break
    assert checked >= 75


def test_resolver_normalizes_digits_zwnj_and_misspellings(seeded: None) -> None:
    cases = {
        "چراغ پژو۲۰۶ تیپ۵ سمت راست": "peugeot-206-type-5",
        "قطعه پژو ۲۰۶ تیپ 5": "peugeot-206-type-5",
        "لوازم pejo 206 type 5": "peugeot-206-type-5",
        "آینه تیبا هاچ بک": "tiba-2",
        "چراغ پژو ۲۰۶ صندوق‌دار وی ۸": "peugeot-206-sd-v8",
        "سنسور سمند ای‌اف۷": "samand-ef7",
        "قطعه پژوپارس تی‌یو۵": "peugeot-pars-tu5",
        "لنت ۴۰۵ اس ال ایکس": "peugeot-405-slx",
    }
    for hint, expected in cases.items():
        assert resolve_vehicle(hint) == expected


def test_reference_log_reconstructs_full_tree(seeded: None) -> None:
    messages = OutboxEvent.objects.filter(topic="yadakchi.vehicles.changed.v1").order_by(
        "created_at"
    )
    reconstructed = {
        event.message_key: event.envelope["payload"]
        for event in messages
        if event.envelope["payload"] is not None
    }
    assert set(reconstructed) == set(Vehicle.objects.values_list("slug", flat=True))
    assert reconstructed["peugeot-206"]["trim"] is None
