"""Wardrobe package import must not take down gunicorn /buy."""


def test_wardrobe_ids_import_does_not_need_schema():
    from sincor2.wardrobe.ids import valid_agent_id

    assert valid_agent_id("E-sirius-08")
    assert not valid_agent_id("../etc/passwd")


def test_missing_legacy_modules_now_exist():
    from sincor2.wardrobe.schema import validate_wardrobe
    from sincor2.wardrobe.quote import quote_sku
    from sincor2.wardrobe.cards import swarm_index, machine_card

    ok, reason = validate_wardrobe({"id": "E-sirius-08", "name": "Sirius"})
    assert ok
    quote = quote_sku("starter", "USDC")
    assert quote["ok"] is True
    idx = swarm_index([{"id": "E-sirius-08", "name": "Sirius", "archetype": "Scout", "status": "Hatch"}])
    assert idx["loaded"] == 1
    assert machine_card({"id": "E-sirius-08", "name": "Sirius"})["name"] == "Sirius"
