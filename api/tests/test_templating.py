from app.templating import profession_name, profession_slug


def test_profession_name_none_is_general():
    assert profession_name(None) == "General"


def test_profession_name_unknown_id_is_labeled_unknown():
    assert profession_name(999) == "Unknown (999)"


def test_profession_slug_none_is_general():
    assert profession_slug(None) == "general"


def test_profession_slug_unknown_id_falls_back_to_the_raw_id():
    assert profession_slug(999) == "999"
