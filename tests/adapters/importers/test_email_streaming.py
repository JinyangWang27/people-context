"""The mbox reader streams its messages and meters one message's address expansion (M20.2).

What extraction *produces* for these two sources is pinned byte for byte by the equivalence
corpus, so nothing here restates it. These tests cover the properties that corpus cannot see:
that the mailbox is consumed one message at a time rather than drained into a list, that the
handle stays open for exactly as long as the loop reads through it and is then closed exactly
once on every path, and that what the parser holds live is bounded by the budget seam rather
than by how many messages the file contains.
"""

from __future__ import annotations

import mailbox
from email._parseaddr import AddressList
from email.message import EmailMessage
from email.utils import getaddresses
from pathlib import Path

import pytest

from people_context.adapters.importers import email as email_module
from people_context.adapters.importers.bounded_source import (
    PARSER_WORK_EXHAUSTED,
    TOO_MANY_CANDIDATES,
)
from people_context.adapters.importers.email import (
    EmailImportExtractor,
    ImportExtractionError,
    _address_upper_bound,
)

_SELF = "me@example.com"


class _RecordingMbox(email_module._BoundedMbox):
    """A `_BoundedMbox` that remembers its metered handle and counts its own closes."""

    instances: list[_RecordingMbox] = []

    def __init__(self, path: str, factory: object, budget: object) -> None:
        super().__init__(path, factory, budget)  # type: ignore[arg-type]
        self.close_calls = 0
        self.handle = self._file
        _RecordingMbox.instances.append(self)

    def close(self) -> None:
        self.close_calls += 1
        super().close()


@pytest.fixture()
def recorded_mboxes(monkeypatch: pytest.MonkeyPatch) -> list[_RecordingMbox]:
    """Route the extractor's mailbox through the recording subclass for one test."""
    _RecordingMbox.instances = []
    monkeypatch.setattr(email_module, "_BoundedMbox", _RecordingMbox)
    return _RecordingMbox.instances


def _write_mbox(path: Path, *, count: int, correspondent: bool, dated: bool = False) -> Path:
    """Write a mailbox of ``count`` messages, optionally naming one external correspondent each."""
    box = mailbox.mbox(path)
    try:
        for index in range(count):
            message = EmailMessage()
            message["From"] = f"person{index}@example.com" if correspondent else _SELF
            message["To"] = _SELF
            if dated:
                message["Date"] = "Wed, 04 Mar 2026 09:06:00 +0000"
            message["Message-ID"] = f"<message-{index}@example.com>"
            message.set_content("body")
            box.add(message)
        box.flush()
    finally:
        box.close()
    return path


def _count_parsed_messages(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """Count factory parses, so laziness is observed rather than inferred from the code."""
    parsed = [0]
    real_parser = email_module.BytesParser

    class _CountingParser(real_parser):  # type: ignore[valid-type, misc]
        def parsebytes(self, data: bytes, headersonly: bool = False) -> object:
            parsed[0] += 1
            return super().parsebytes(data, headersonly=headersonly)

    monkeypatch.setattr(email_module, "BytesParser", _CountingParser)
    return parsed


def test_the_mailbox_is_consumed_lazily_rather_than_drained_into_a_list(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A refusal early in the loop must leave most of the mailbox unparsed.

    `list(mbox)` parsed every message in the file before extraction looked at the first one, so
    the count of factory parses is what separates a streamed mailbox from a materialized one.
    """
    source = _write_mbox(tmp_path / "many.mbox", count=50, correspondent=True)
    parsed = _count_parsed_messages(monkeypatch)

    with pytest.raises(ImportExtractionError) as refusal:
        EmailImportExtractor().extract(
            "mbox",
            content=None,
            path=str(source),
            self_addresses={_SELF},
            max_candidates=1,
        )

    assert refusal.value.code == TOO_MANY_CANDIDATES
    assert 0 < parsed[0] <= 3


def test_the_mailbox_stays_open_for_the_whole_loop_and_closes_exactly_once(
    tmp_path: Path,
    recorded_mboxes: list[_RecordingMbox],
) -> None:
    source = _write_mbox(tmp_path / "three.mbox", count=3, correspondent=True, dated=True)

    extracted = EmailImportExtractor().extract(
        "mbox",
        content=None,
        path=str(source),
        self_addresses={_SELF},
    )

    # Reading all three through a handle that was closed early would have raised instead.
    assert [person.email for person in extracted.people] == [f"person{index}@example.com" for index in range(3)]
    assert len(recorded_mboxes) == 1
    assert recorded_mboxes[0].close_calls == 1
    assert recorded_mboxes[0].handle.closed is True


def test_a_refusal_mid_iteration_still_closes_the_mailbox_exactly_once(
    tmp_path: Path,
    recorded_mboxes: list[_RecordingMbox],
) -> None:
    """The handle now outlives the call that opened it, so every exit path has to release it."""
    source = _write_mbox(tmp_path / "many.mbox", count=20, correspondent=True)

    with pytest.raises(ImportExtractionError) as refusal:
        EmailImportExtractor().extract(
            "mbox",
            content=None,
            path=str(source),
            self_addresses={_SELF},
            max_candidates=1,
        )

    assert refusal.value.code == TOO_MANY_CANDIDATES
    assert len(recorded_mboxes) == 1
    assert recorded_mboxes[0].close_calls == 1
    assert recorded_mboxes[0].handle.closed is True


def test_a_correspondent_free_mailbox_retains_a_constant_rather_than_its_message_count(
    tmp_path: Path,
) -> None:
    """The candidate ceiling cannot meter this: a self-only mailbox stages nothing at all.

    The budget a hundred-fold larger mailbox needs is the same budget the small one needs, which
    is the property under test — `list(mbox)` would have made it grow with the message count.
    Both are pinned as the *minimum*, so the equality is not two numbers that merely both fit.
    """
    extractor = EmailImportExtractor()
    peak = 17

    for count in (5, 500):
        source = _write_mbox(tmp_path / f"self-only-{count}.mbox", count=count, correspondent=False)

        extracted = extractor.extract(
            "mbox",
            content=None,
            path=str(source),
            self_addresses={_SELF},
            max_retained_parse_records=peak,
        )

        assert extracted.people == []
        assert extracted.interactions == []
        assert extracted.skipped_message_ids == []
        assert extracted.skipped_without_id == 0

        with pytest.raises(ImportExtractionError) as refusal:
            extractor.extract(
                "mbox",
                content=None,
                path=str(source),
                self_addresses={_SELF},
                max_retained_parse_records=peak - 1,
            )

        assert refusal.value.code == PARSER_WORK_EXHAUSTED


def test_the_message_being_read_is_itself_accounted_against_the_parser_budget(
    tmp_path: Path,
) -> None:
    """A budget of zero refuses the first message, which is what proves the accounting is live.

    Without it, the bounded result above would be indistinguishable from a seam that is never
    reached at all.
    """
    source = _write_mbox(tmp_path / "self-only.mbox", count=3, correspondent=False)

    with pytest.raises(ImportExtractionError) as refusal:
        EmailImportExtractor().extract(
            "mbox",
            content=None,
            path=str(source),
            self_addresses={_SELF},
            max_retained_parse_records=0,
        )

    assert refusal.value.code == PARSER_WORK_EXHAUSTED
    # The refusal names the limit and nothing about the mailbox it rejected.
    assert str(source) not in str(refusal.value)


def test_one_message_address_expansion_is_refused_before_the_addresses_are_parsed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`getaddresses` allocates the whole list at once, so metering has to precede the call.

    Accounting only the correspondents that survive filtering would refuse after the allocation
    worth bounding had already happened, which is exactly the spike the budget exists to stop.
    """
    calls = [0]
    real_getaddresses = email_module.getaddresses

    def _counting_getaddresses(fieldvalues: object) -> object:
        calls[0] += 1
        return real_getaddresses(fieldvalues)  # type: ignore[arg-type]

    monkeypatch.setattr(email_module, "getaddresses", _counting_getaddresses)
    recipients = ", ".join(f"Person {index} <person{index}@example.com>" for index in range(20))
    content = f"To: {recipients}\n\nbody\n"

    with pytest.raises(ImportExtractionError) as refusal:
        EmailImportExtractor().extract(
            "email",
            content=content,
            path=None,
            self_addresses={_SELF},
            max_retained_parse_records=4,
        )

    assert refusal.value.code == PARSER_WORK_EXHAUSTED
    assert calls[0] == 0


def test_only_one_header_address_list_is_charged_at_a_time() -> None:
    """The peak is one header's bound plus what is already retained, not every header's bound.

    At the last of four address headers the message holds itself, the twenty-one correspondents
    kept from the three before it, and that header's own 260-byte bound: 282. Charging all four
    bounds as if every list were live at once would reach 823, and this message would then need
    a budget it has no reason to need.
    """
    header = ", ".join(f"Person{index} <p{index}@example.com>" for index in range(10))
    content = f"From: Ada <ada@example.com>\nTo: {header}\nCc: {header}\nReply-To: {header}\n\nbody\n"
    extractor = EmailImportExtractor()

    extracted = extractor.extract(
        "email",
        content=content,
        path=None,
        self_addresses={_SELF},
        max_retained_parse_records=282,
    )

    assert [person.email for person in extracted.people] == [
        "ada@example.com",
        *[f"p{index}@example.com" for index in range(10)],
    ]

    with pytest.raises(ImportExtractionError) as refusal:
        extractor.extract(
            "email",
            content=content,
            path=None,
            self_addresses={_SELF},
            max_retained_parse_records=281,
        )

    assert refusal.value.code == PARSER_WORK_EXHAUSTED


@pytest.mark.parametrize(
    "value",
    [
        "@" * 500,
        ";" * 500,
        "<>" * 250,
        "a@x <b@y> " * 50,
        ", ".join(f"p{index}@example.com" for index in range(40)),
        "",
    ],
    ids=["at-signs", "semicolons", "angle-pairs", "malformed-pairs", "well-formed", "empty"],
)
def test_the_header_bound_covers_the_address_parser_intermediate_not_just_its_result(
    value: str,
) -> None:
    """What has to be bounded is what the parser builds, which a comma count does not describe.

    Strict validation collapses a malformed result to one tuple only after every tuple has been
    built, so the returned length says nothing about the allocation — a comma-free run of
    `a@x <b@y>` returns one and builds one per repetition. The densest inputs cost one tuple per
    byte and none costs less, which is why the bound is taken from the length of the string the
    parser is handed rather than from the separators inside it.
    """
    bound = _address_upper_bound([value])

    assert bound >= len(AddressList(value).addresslist)
    assert bound >= len(getaddresses([value]))


def test_a_message_inside_the_parser_budget_extracts_exactly_as_it_did_unbudgeted() -> None:
    """The budget is a backstop, so a message under it must be byte-identical to no budget."""
    recipients = ", ".join(f"Person {index} <person{index}@example.com>" for index in range(20))
    content = f"From: Ada <ada@example.com>\nTo: {recipients}\n\nbody\n"
    extractor = EmailImportExtractor()

    unbudgeted = extractor.extract("email", content=content, path=None, self_addresses={_SELF})
    budgeted = extractor.extract(
        "email",
        content=content,
        path=None,
        self_addresses={_SELF},
        max_retained_parse_records=len(content) + 1,
    )

    assert budgeted == unbudgeted
    assert [person.email for person in budgeted.people] == [
        "ada@example.com",
        *[f"person{index}@example.com" for index in range(20)],
    ]


def test_an_invalid_mbox_request_is_refused_before_any_handle_is_opened(
    recorded_mboxes: list[_RecordingMbox],
) -> None:
    """Input validation still precedes the read, so a refused request opens nothing to close."""
    extractor = EmailImportExtractor()

    with pytest.raises(ImportExtractionError) as refusal:
        extractor.extract("mbox", content="x", path=None, self_addresses=set())

    assert refusal.value.code == "invalid_source"
    assert recorded_mboxes == []
