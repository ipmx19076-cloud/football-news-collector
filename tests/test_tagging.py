import pytest

from football_news.config import ConfigError, load_config
from football_news.tagging import Tagger, fold


@pytest.fixture(scope="module")
def tagger() -> Tagger:
    """The real keyword map shipped in config.yaml."""
    return Tagger.from_config(load_config().raw["tagging"])


def test_fold():
    assert fold("Barça’s 2-1 win!") == " barca s 2 1 win "


def test_spec_example_arsenal_spurs(tagger):
    assert tagger.tag_text("Arsenal edge Spurs in North London derby") == (
        ("Premier League",), ("Arsenal", "Tottenham"))


@pytest.mark.parametrize("headline, leagues, clubs", [
    ("Man Utd and Barca agree swap deal", ("Premier League", "La Liga"), ("Barcelona", "Manchester United")),
    ("Mbappé scores twice as Real Madrid win El Clásico", ("La Liga",), ("Real Madrid",)),
    ("Arsenal beat PSG in the Champions League", ("Champions League", "Premier League", "Ligue 1"),
     ("Arsenal", "Paris Saint-Germain")),
    ("Messi stars for Inter Miami", ("Other/General",), ()),
    ("Arteta hails Arsenal’s ‘winning era’", ("Premier League",), ("Arsenal",)),
    ("Inter Milan sign striker from AC Milan", ("Serie A",), ("AC Milan", "Inter")),
    ("David Silva joins Hong Kong Premier League side", ("Other/General",), ()),
    ("QPR beat Queens Park Rangers rivals", ("Other/General",), ()),
    ("Rangers eye top spot in Scot Prem", ("Scottish Premiership",), ("Rangers",)),
    ("Monaco Grand Prix tickets", ("Other/General",), ()),
    ("Internal review at FA", ("Other/General",), ()),
    ("England squad named for World Cup qualifier", ("International",), ()),
    ("Chelsea win Club World Cup", ("Premier League",), ("Chelsea",)),
])
def test_keyword_map_cases(tagger, headline, leagues, clubs):
    assert tagger.tag_text(headline) == (leagues, clubs)


def test_summary_is_matched_too(tagger):
    assert tagger.tag_text("Late drama at the Allianz", "Bayern beat Dortmund 3-2") == (
        ("Bundesliga",), ("Bayern Munich", "Borussia Dortmund"))


def test_unknown_league_reference_rejected():
    with pytest.raises(ConfigError, match="unknown league"):
        Tagger.from_config({"leagues": {"A": ["a"]}, "clubs": {"X": {"league": "B", "keywords": ["x"]}}})


def test_missing_keywords_rejected():
    with pytest.raises(ConfigError):
        Tagger.from_config({"leagues": {"A": {"keywords": []}}})


def test_league_order_puts_default_last(tagger):
    assert tagger.league_order[0] == "Premier League" and tagger.league_order[-1] == "Other/General"
