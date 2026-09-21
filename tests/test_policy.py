from techhand_print_fab.policy import assess, normalize_reproduction


def test_explicit_proprietary_clone_is_refused() -> None:
    refusal = assess("a plain bracket", reproduction=normalize_reproduction("proprietary_clone"))
    assert refusal is not None
    assert refusal.code == "reproduction_proprietary_clone"


def test_exact_copy_phrase_is_refused() -> None:
    refusal = assess(
        "Make an exact copy of the commercial product",
        reproduction="original",
    )
    assert refusal is not None
    assert refusal.code == "exact_copy"


def test_one_to_one_scale_note_is_allowed() -> None:
    assert assess("Draw this 1:1 in millimeters from my sketch.", reproduction="original") is None


def test_original_fixture_language_is_allowed() -> None:
    text = "PETG mounting bracket for a 2020 extrusion, original dimensions from my calipers."
    assert assess(text, reproduction="original") is None


def test_interoperable_fixture_flag_is_allowed() -> None:
    assert (
        assess("fixture I designed", reproduction=normalize_reproduction("interoperable_fixture"))
        is None
    )
