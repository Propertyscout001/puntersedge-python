"""racing_next_to_go / racing_events pass every documented filter straight through to the API,
so the British and Irish card (country="GB,IE") is reachable from the client (0.3.3)."""
from puntersedge import PuntersEdge


class _Spy(PuntersEdge):
    def __init__(self):
        super().__init__(api_key="pe_test_key")
        self.calls = []

    def _get(self, path, **params):
        self.calls.append((path, params))
        return []


def _params(call):
    return {k: v for k, v in call[1].items() if v is not None}


def test_next_to_go_passes_country_venue_bookmakers_and_caps():
    pe = _Spy()
    pe.racing_next_to_go(categories="horse", country="GB,IE", venue="Leicester", bookmakers="ladbrokes_uk,williamhill",
                         num_races=50, include_unresolved=True)
    path, params = pe.calls[0][0], _params(pe.calls[0])
    assert path == "/racing/next-to-go"
    assert params == {"categories": "horse", "country": "GB,IE", "venue": "Leicester",
                      "bookmakers": "ladbrokes_uk,williamhill", "num_races": 50, "include_unresolved": "true"}


def test_next_to_go_without_arguments_sends_nothing_extra():
    pe = _Spy()
    pe.racing_next_to_go()
    assert _params(pe.calls[0]) == {}


def test_events_passes_country():
    pe = _Spy()
    pe.racing_events(hours_ahead=12, country="IE")
    assert pe.calls[0][0] == "/racing/events" and _params(pe.calls[0]) == {"hours_ahead": 12, "country": "IE"}
