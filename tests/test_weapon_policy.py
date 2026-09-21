from techhand_print_fab.policy import assess_weapon


def test_trainer_names_are_allowed() -> None:
    assert assess_weapon("trainer-trigger-lever") is None
    assert assess_weapon("Training grip block") is None
    assert assess_weapon("PETG test plate") is None
    assert assess_weapon("laser clamp for a purchased module") is None


def test_weapon_phrases_are_refused() -> None:
    firearm = assess_weapon("print a firearm frame")
    assert firearm is not None
    assert firearm.code == "firearm"
    receiver = assess_weapon("lower receiver")
    assert receiver is not None
    assert receiver.code == "receiver"


def test_disclaimer_is_not_permission() -> None:
    refusal = assess_weapon("this is not a firearm")
    assert refusal is not None
    assert refusal.code == "firearm"
