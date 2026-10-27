"""The in-memory ticket store: lifetime, bound and isolation from the caller."""

from __future__ import annotations

import pytest
from support import FakeClock

from ai_privacy_filter.tickets import DEFAULT_MAX_ENTRIES, DEFAULT_TTL_S, TicketStore

MAPPING = {"[PERSON_1]": "Erika Mustermann"}


def test_defaults_are_fifteen_minutes_and_bounded() -> None:
    store = TicketStore()
    assert store.ttl_s == DEFAULT_TTL_S == 900
    assert store.max_entries == DEFAULT_MAX_ENTRIES
    assert len(store) == 0


def test_issue_then_lookup_round_trips_the_mapping() -> None:
    store = TicketStore()
    ticket = store.issue(MAPPING)
    assert ticket.expires_in_s == 900
    assert len(ticket.id) >= 16
    assert store.mapping(ticket.id) == MAPPING
    assert len(store) == 1


def test_ticket_ids_are_unique() -> None:
    store = TicketStore()
    ids = {store.issue(MAPPING).id for _ in range(20)}
    assert len(ids) == 20


def test_expired_ticket_is_gone_and_dropped(clock: FakeClock) -> None:
    store = TicketStore(ttl_s=900, clock=clock)
    ticket = store.issue(MAPPING)
    clock.advance(899)
    assert store.mapping(ticket.id) == MAPPING
    clock.advance(1)
    assert store.mapping(ticket.id) is None
    assert len(store) == 0


def test_unknown_ticket_returns_none() -> None:
    assert TicketStore().mapping("nope") is None


def test_store_is_bounded_and_evicts_the_oldest() -> None:
    store = TicketStore(max_entries=2)
    first = store.issue({"[PERSON_1]": "one"})
    second = store.issue({"[PERSON_1]": "two"})
    third = store.issue({"[PERSON_1]": "three"})
    assert len(store) == 2
    assert store.mapping(first.id) is None
    assert store.mapping(second.id) is not None
    assert store.mapping(third.id) is not None


def test_issue_purges_expired_entries(clock: FakeClock) -> None:
    store = TicketStore(ttl_s=10, clock=clock)
    stale = store.issue(MAPPING)
    clock.advance(11)
    fresh = store.issue(MAPPING)
    assert len(store) == 1
    assert store.mapping(stale.id) is None
    assert store.mapping(fresh.id) == MAPPING


def test_store_copies_the_mapping_in_both_directions() -> None:
    store = TicketStore()
    source = dict(MAPPING)
    ticket = store.issue(source)
    source["[PERSON_1]"] = "changed"
    returned = store.mapping(ticket.id)
    assert returned is not None
    returned["[PERSON_1]"] = "changed again"
    assert store.mapping(ticket.id) == MAPPING


@pytest.mark.parametrize(("ttl_s", "max_entries"), [(0, 1), (-1, 1), (900, 0)])
def test_invalid_configuration_is_rejected(ttl_s: float, max_entries: int) -> None:
    with pytest.raises(ValueError):
        TicketStore(ttl_s=ttl_s, max_entries=max_entries)
