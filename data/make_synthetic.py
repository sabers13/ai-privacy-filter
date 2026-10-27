#!/usr/bin/env python3
"""Generate the seeded synthetic enquiry corpus used for evaluation and demos.

Everything here is synthetic: names, addresses, phone numbers, e-mail addresses
and IBANs come from a seeded Faker ``de_DE`` instance, and the message bodies are
hand-written German templates. No real person, company or account is used.

The output is deterministic for a given seed and Faker version: running the
generator twice writes byte-identical files.

Usage::

    uv run python data/make_synthetic.py --seed 20261027

Outputs ``dev.jsonl`` (80 records), ``test.jsonl`` (160 records) and
``README.md`` with the seed and the counts by style, category, urgency and gold
span type.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import TYPE_CHECKING

from faker import Faker

from ai_privacy_filter.models import Category, Enquiry, EntityType, Span, Urgency

if TYPE_CHECKING:
    from collections.abc import Sequence

GENERATOR_VERSION = "1.0.0"
DEFAULT_SEED = 20261027
DEFAULT_OUT_DIR = Path(__file__).resolve().parents[1] / "data" / "synthetic"
DEV_COUNT = 80
TEST_COUNT = 160
GOLD_SOURCE = "gold"
GOLD_SCORE = 1.0

STYLES: tuple[str, ...] = (
    "formal_letter",
    "web_form",
    "email_signature",
    "forwarded_email",
    "informal_du",
    "mixed_people_company",
    "hard_cases",
)
STYLE_LABELS: dict[str, str] = {
    "formal_letter": "Formal letter",
    "web_form": "Short web-form message",
    "email_signature": "E-mail with signature block",
    "forwarded_email": "Forwarded e-mail with quoted header",
    "informal_du": "Informal du-form",
    "mixed_people_company": "Mixed: two people and a company",
    "hard_cases": "Hard cases: word surnames, name in company, split IBAN, odd phones",
}

CATEGORIES: tuple[Category, ...] = (
    Category.QUOTE_REQUEST,
    Category.SUPPORT,
    Category.JOB_APPLICATION,
    Category.PARTNERSHIP,
    Category.OTHER,
)

# Roughly 60 % NORMAL, 20 % HIGH, 20 % LOW.
URGENCY_CYCLE: tuple[Urgency, ...] = (
    Urgency.NORMAL,
    Urgency.NORMAL,
    Urgency.HIGH,
    Urgency.NORMAL,
    Urgency.LOW,
)

SUBJECTS: dict[Category, str] = {
    Category.QUOTE_REQUEST: "Anfrage: Angebot für die Besprechungsraum-Ausstattung",
    Category.SUPPORT: "Störung im Kundenportal",
    Category.JOB_APPLICATION: "Bewerbung als Datenanalystin",
    Category.PARTNERSHIP: "Anfrage: gemeinsame Workshop-Reihe",
    Category.OTHER: "Allgemeine Frage zum Schulungsprogramm",
}

# Invented, company-neutral organisations. They keep the corpus independent of
# any real employer and give the ORG spans a stable shape.
ORGS: tuple[str, ...] = (
    "Nordlicht Systeme GmbH",
    "Klarwerk Beratung GmbH",
    "Weitblick Medien GmbH",
    "Flusslauf Logistik GmbH",
    "Sonnenhof Handel GmbH",
    "Blauwerk Technik GmbH",
    "Eichenhof Services GmbH",
    "Morgenrot Bildung GmbH",
)

# German surnames that are also common nouns. Used only in the hard-case style.
WORD_SURNAMES: tuple[str, ...] = (
    "Berg",
    "Wolf",
    "Vogel",
    "Fuchs",
    "Hahn",
    "Bauer",
    "Koch",
    "Weber",
    "Fischer",
    "Schneider",
    "Bach",
    "Kern",
)

# body[tone][category][urgency] -> two hand-written variants.
BODIES: dict[str, dict[Category, dict[Urgency, tuple[str, ...]]]] = {
    "formal": {
        Category.QUOTE_REQUEST: {
            Urgency.NORMAL: (
                "wir planen die Ausstattung von zwei Besprechungsräumen und benötigen dafür "
                "ein Angebot. Bitte senden Sie uns Ihre Preisliste und die Lieferzeiten zu.",
                "für zwei neue Besprechungsräume suchen wir noch passende Technik. Können Sie "
                "uns ein Angebot mit Preisen und Lieferzeiten erstellen?",
            ),
            Urgency.HIGH: (
                "wir benötigen ein Angebot für die Ausstattung von zwei Besprechungsräumen. Die "
                "Entscheidung fällt bis spätestens Freitag, 6. November, daher brauchen wir Ihre "
                "Rückmeldung dringend in dieser Woche.",
                "für die Ausstattung von zwei Besprechungsräumen brauchen wir dringend ein "
                "Angebot, weil die Frist am Montag endet. Bitte melden Sie sich bis dahin.",
            ),
            Urgency.LOW: (
                "wir denken über die Ausstattung von zwei Besprechungsräumen nach und hätten gern "
                "ein Angebot. Es eilt nicht, eine Rückmeldung in den nächsten Wochen genügt.",
                "langfristig möchten wir zwei Besprechungsräume ausstatten und sammeln dafür "
                "Angebote. Es besteht keine Eile; schreiben Sie uns gern, wenn es Ihnen passt.",
            ),
        },
        Category.SUPPORT: {
            Urgency.NORMAL: (
                "bei der Anmeldung im Kundenportal erscheint seit gestern eine Fehlermeldung. "
                "Bitte prüfen Sie den Fall und teilen Sie uns mit, wie wir weiter vorgehen können.",
                "im Kundenportal lässt sich unser Zugang seit gestern nicht mehr öffnen. Können "
                "Sie den Fehler prüfen und uns eine kurze Rückmeldung geben?",
            ),
            Urgency.HIGH: (
                "seit drei Wochen erhalten wir keine Antwort, und der Fehler im Kundenportal "
                "blockiert unsere Buchhaltung. Ich bin sehr unzufrieden und erwarte dringend eine "
                "Lösung bis Freitag.",
                "der Fehler im Kundenportal besteht weiterhin, und niemand hat auf unsere letzten "
                "Nachrichten reagiert. Das ist inakzeptabel; wir brauchen dringend Hilfe, die "
                "Frist läuft morgen ab.",
            ),
            Urgency.LOW: (
                "ich habe eine Frage zur Anmeldung im Kundenportal. Es eilt nicht; eine Antwort "
                "irgendwann in den nächsten Wochen reicht.",
                "uns ist ein kleiner Fehler im Kundenportal aufgefallen, der uns nicht blockiert. "
                "Es eilt nicht; schauen Sie ihn sich an, wenn Sie Zeit haben.",
            ),
        },
        Category.JOB_APPLICATION: {
            Urgency.NORMAL: (
                "ich bewerbe mich auf die ausgeschriebene Stelle als Datenanalystin. Meinen "
                "Lebenslauf und meine Zeugnisse habe ich beigefügt.",
                "mit diesem Schreiben bewerbe ich mich auf die ausgeschriebene Stelle als "
                "Datenanalyst. Meine Unterlagen finden Sie im Anhang.",
            ),
            Urgency.HIGH: (
                "ich bewerbe mich auf die ausgeschriebene Stelle als Datenanalystin. Die "
                "Bewerbungsfrist endet am Freitag, daher reiche ich meine Unterlagen dringend "
                "heute ein.",
                "die Stelle als Datenanalyst ist ausgeschrieben, und die Frist endet morgen. "
                "Deshalb sende ich meine Bewerbung dringend jetzt und bitte um eine kurze "
                "Bestätigung.",
            ),
            Urgency.LOW: (
                "ich interessiere mich für die ausgeschriebene Stelle als Datenanalystin und "
                "sende meine Unterlagen unverbindlich. Es eilt nicht; falls die Stelle noch offen "
                "ist, freue ich mich über eine Rückmeldung.",
                "ich möchte mich unverbindlich für die Stelle als Datenanalyst bewerben. Es "
                "besteht keine Eile; eine Rückmeldung in den nächsten Wochen genügt.",
            ),
        },
        Category.PARTNERSHIP: {
            Urgency.NORMAL: (
                "wir möchten prüfen, ob eine gemeinsame Workshop-Reihe für unsere Teams möglich "
                "ist. Über ein erstes Gespräch würden wir uns freuen.",
                "uns interessiert eine Zusammenarbeit bei gemeinsamen Workshops. Hätten Sie "
                "demnächst Zeit für ein kurzes Gespräch?",
            ),
            Urgency.HIGH: (
                "wir möchten eine Kooperation für gemeinsame Workshops aufsetzen. Die Förderfrist "
                "endet am 15. November, daher benötigen wir dringend eine Rückmeldung bis Montag.",
                "für eine mögliche Partnerschaft bei Workshops läuft die Bewerbungsfrist am "
                "Freitag ab. Bitte geben Sie uns dringend Bescheid, ob Sie mitmachen.",
            ),
            Urgency.LOW: (
                "wir möchten unverbindlich anfragen, ob eine Kooperation für gemeinsame Workshops "
                "denkbar wäre. Es eilt nicht; melden Sie sich gern, wenn es Ihnen passt.",
                "vielleicht wäre eine Partnerschaft bei gemeinsamen Workshops interessant. Wir "
                "haben keine Eile und freuen uns über eine Nachricht, wenn Sie Zeit haben.",
            ),
        },
        Category.OTHER: {
            Urgency.NORMAL: (
                "ich habe eine allgemeine Frage zu Ihrem Schulungsprogramm und würde gern wissen, "
                "welche Kurse im nächsten Quartal starten.",
                "können Sie mir allgemein sagen, ob Ihr Schulungsprogramm auch Kurse für "
                "Einsteiger enthält?",
            ),
            Urgency.HIGH: (
                "ich habe eine Frage zur Anmeldung für Ihr Schulungsprogramm. Die Anmeldefrist "
                "läuft morgen ab, bitte antworten Sie dringend.",
                "für Ihr Schulungsprogramm endet die Anmeldung am Freitag. Ich brauche dringend "
                "eine Auskunft, ob noch Plätze frei sind.",
            ),
            Urgency.LOW: (
                "ich habe eine kurze allgemeine Frage zu Ihrem Schulungsprogramm. Es eilt nicht, "
                "ich freue mich über eine Antwort, wenn Sie Zeit haben.",
                "mich interessiert Ihr Schulungsprogramm, ich möchte mich aber noch nicht "
                "anmelden. Eine Antwort hat keine Eile.",
            ),
        },
    },
    "informal": {
        Category.QUOTE_REQUEST: {
            Urgency.NORMAL: (
                "ich bräuchte ein Angebot für zwei Besprechungsräume. Kannst du mir deine "
                "Preisliste und die Lieferzeiten schicken?",
                "für zwei Besprechungsräume hätte ich gern ein Angebot von dir. Sag mir einfach, "
                "was das kosten würde.",
            ),
            Urgency.HIGH: (
                "ich brauche dringend ein Angebot für zwei Besprechungsräume, weil die "
                "Entscheidung bis spätestens Freitag fällt. Kannst du mir bis dahin antworten?",
                "für zwei Besprechungsräume brauche ich dringend ein Angebot; die Frist endet am "
                "Montag. Melde dich bitte bis dahin.",
            ),
            Urgency.LOW: (
                "ich hätte gern irgendwann ein Angebot für zwei Besprechungsräume. Es eilt nicht, "
                "deine Rückmeldung kann ruhig ein paar Wochen dauern.",
                "wir sammeln gerade Angebote für zwei Besprechungsräume. Keine Eile, schick mir "
                "deins einfach, wenn du dazu kommst.",
            ),
        },
        Category.SUPPORT: {
            Urgency.NORMAL: (
                "bei mir erscheint seit gestern eine Fehlermeldung im Kundenportal. Kannst du dir "
                "das mal anschauen und mir sagen, was ich tun soll?",
                "ich komme seit gestern nicht mehr ins Kundenportal. Weißt du, woran das liegen "
                "könnte?",
            ),
            Urgency.HIGH: (
                "seit drei Wochen bekomme ich keine Antwort, und der Fehler blockiert meine "
                "Buchhaltung. Ich bin echt unzufrieden und brauche dringend eine Lösung bis "
                "Freitag.",
                "der Fehler im Kundenportal ist immer noch da, und keiner hat sich gemeldet. Das "
                "nervt sehr; ich brauche dringend Hilfe, die Frist läuft morgen ab.",
            ),
            Urgency.LOW: (
                "ich habe eine kurze Frage zur Anmeldung im Kundenportal. Kein Stress, antworte "
                "einfach, wenn du Zeit hast.",
                "mir ist ein kleiner Fehler im Kundenportal aufgefallen, der mich nicht blockiert. "
                "Kein Stress, schau ihn dir an, wenn du mal Zeit hast.",
            ),
        },
        Category.JOB_APPLICATION: {
            Urgency.NORMAL: (
                "ich bewerbe mich auf die Stelle als Datenanalystin. Lebenslauf und Zeugnisse "
                "habe ich angehängt.",
                "hier kommt meine Bewerbung für die Stelle als Datenanalyst. Meine Unterlagen "
                "sind im Anhang.",
            ),
            Urgency.HIGH: (
                "ich bewerbe mich auf die Stelle als Datenanalystin. Die Frist endet am Freitag, "
                "deshalb schicke ich meine Unterlagen dringend heute.",
                "für die Stelle als Datenanalyst endet die Frist morgen. Ich schicke dir meine "
                "Bewerbung deshalb dringend jetzt.",
            ),
            Urgency.LOW: (
                "ich interessiere mich für die Stelle als Datenanalystin und schicke dir "
                "unverbindlich meine Unterlagen. Es eilt nicht, falls die Stelle noch offen ist.",
                "ich möchte mich unverbindlich für die Stelle als Datenanalyst bewerben. Keine "
                "Eile, melde dich einfach, wenn du Zeit hast.",
            ),
        },
        Category.PARTNERSHIP: {
            Urgency.NORMAL: (
                "wir wollen prüfen, ob eine gemeinsame Workshop-Reihe klappt. Hast du Lust auf "
                "ein erstes Gespräch?",
                "uns interessiert eine Zusammenarbeit bei Workshops. Wann würde dir ein kurzes "
                "Gespräch passen?",
            ),
            Urgency.HIGH: (
                "wir wollen eine Kooperation für gemeinsame Workshops starten. Die Förderfrist "
                "endet am 15. November, deshalb brauchen wir dringend eine Antwort bis Montag.",
                "für die Partnerschaft bei Workshops läuft die Frist am Freitag ab. Gib uns bitte "
                "dringend Bescheid, ob du dabei bist.",
            ),
            Urgency.LOW: (
                "wir fragen unverbindlich, ob eine Kooperation für gemeinsame Workshops passt. Es "
                "eilt nicht, melde dich einfach, wenn du Zeit hast.",
                "vielleicht passt eine Partnerschaft bei Workshops ja zu dir. Keine Eile, wir "
                "freuen uns über eine Nachricht, wenn du Zeit hast.",
            ),
        },
        Category.OTHER: {
            Urgency.NORMAL: (
                "ich habe eine allgemeine Frage zu deinem Schulungsprogramm und würde gern "
                "wissen, welche Kurse im nächsten Quartal starten.",
                "kannst du mir allgemein sagen, ob dein Schulungsprogramm auch Kurse für "
                "Einsteiger hat?",
            ),
            Urgency.HIGH: (
                "ich habe eine Frage zur Anmeldung für dein Schulungsprogramm. Die Frist läuft "
                "morgen ab, bitte antworte dringend.",
                "für dein Schulungsprogramm endet die Anmeldung am Freitag. Ich brauche dringend "
                "eine Auskunft, ob noch Plätze frei sind.",
            ),
            Urgency.LOW: (
                "ich habe eine kurze Frage zu deinem Schulungsprogramm. Es eilt nicht, antworte "
                "einfach, wenn du Zeit hast.",
                "mich interessiert dein Schulungsprogramm, ich will mich aber noch nicht "
                "anmelden. Eine Antwort hat keine Eile.",
            ),
        },
    },
}


class Builder:
    """Accumulate text and character-exact spans in one pass."""

    def __init__(self) -> None:
        self._parts: list[str] = []
        self._spans: list[Span] = []
        self._length = 0

    def add(self, text: str) -> None:
        self._parts.append(text)
        self._length += len(text)

    def entity(self, entity_type: EntityType, text: str) -> None:
        start = self._length
        self.add(text)
        self._spans.append(
            Span(
                start=start,
                end=self._length,
                type=entity_type,
                text=text,
                score=GOLD_SCORE,
                source=GOLD_SOURCE,
            )
        )

    def build(self) -> tuple[str, list[Span]]:
        return "".join(self._parts), list(self._spans)


def _value(raw: str) -> str:
    value = raw.strip()
    if not value:
        raise ValueError("Faker produced an empty value")
    return value


def _address(faker: Faker) -> tuple[str, str]:
    return _value(faker.street_address()), _value(f"{faker.postcode()} {faker.city()}")


def _org(faker: Faker) -> str:
    return faker.random_element(ORGS)


def _phone(faker: Faker) -> str:
    return _value(faker.phone_number())


def _odd_phone(faker: Faker) -> str:
    digits = faker.numerify("##########")
    variant = faker.random_int(min=0, max=2)
    if variant == 0:
        return f"+49 (0){digits[:2]} {digits[2:6]}-{digits[6:10]}"
    if variant == 1:
        return f"0{digits[:2]} / {digits[2:4]} {digits[4:6]} {digits[6:8]} {digits[8:10]}"
    return f"0{digits[:3]}-{digits[3:]}"


def _split_iban(faker: Faker) -> str:
    """Return an IBAN written in groups with a line break after the first half."""
    raw = faker.iban().replace(" ", "")
    groups = [raw[index : index + 4] for index in range(0, len(raw), 4)]
    middle = len(groups) // 2
    return " ".join(groups[:middle]) + "\n" + " ".join(groups[middle:])


def _word_surname(faker: Faker) -> str:
    return faker.random_element(WORD_SURNAMES)


def _render(
    faker: Faker, style: str, category: Category, urgency: Urgency
) -> tuple[str, list[Span]]:
    tone = "informal" if style == "informal_du" else "formal"
    body = faker.random_element(BODIES[tone][category][urgency])
    builder = Builder()

    if style == "formal_letter":
        name = _value(faker.name())
        street, postcode_city = _address(faker)
        builder.entity(EntityType.PERSON, name)
        builder.add("\n")
        builder.entity(EntityType.ADDRESS_STREET, street)
        builder.add("\n")
        builder.entity(EntityType.POSTCODE_CITY, postcode_city)
        builder.add("\nE-Mail: ")
        builder.entity(EntityType.EMAIL, _value(faker.email()))
        builder.add("\nTelefon: ")
        builder.entity(EntityType.PHONE, _phone(faker))
        builder.add("\n\nSehr geehrte Damen und Herren,\n\n")
        builder.add(body)
        builder.add("\n\nMit freundlichen Grüßen\n")
        builder.entity(EntityType.PERSON, name)

    elif style == "web_form":
        street, postcode_city = _address(faker)
        builder.add("Name: ")
        builder.entity(EntityType.PERSON, _value(faker.name()))
        builder.add("\nE-Mail: ")
        builder.entity(EntityType.EMAIL, _value(faker.email()))
        builder.add("\nTelefon: ")
        builder.entity(EntityType.PHONE, _phone(faker))
        builder.add("\nUnternehmen: ")
        builder.entity(EntityType.ORG, _org(faker))
        builder.add("\nNachricht:\n")
        builder.add(body[0].upper() + body[1:])
        # The address fields exist in the form; label them even though the
        # message body is short.
        builder.add("\nStraße: ")
        builder.entity(EntityType.ADDRESS_STREET, street)
        builder.add("\nPLZ/Ort: ")
        builder.entity(EntityType.POSTCODE_CITY, postcode_city)

    elif style == "email_signature":
        street, postcode_city = _address(faker)
        builder.add(f"Betreff: {SUBJECTS[category]}\n\nGuten Tag,\n\n")
        builder.add(body)
        builder.add("\n\nMit freundlichen Grüßen\n")
        builder.entity(EntityType.PERSON, _value(faker.name()))
        builder.add("\n")
        builder.entity(EntityType.ORG, _org(faker))
        builder.add("\n")
        builder.entity(EntityType.ADDRESS_STREET, street)
        builder.add("\n")
        builder.entity(EntityType.POSTCODE_CITY, postcode_city)
        builder.add("\nTelefon: ")
        builder.entity(EntityType.PHONE, _phone(faker))
        builder.add("\nE-Mail: ")
        builder.entity(EntityType.EMAIL, _value(faker.email()))
        builder.add("\nIBAN: ")
        builder.entity(EntityType.IBAN, faker.iban())

    elif style == "forwarded_email":
        street, postcode_city = _address(faker)
        builder.add("---------- Weitergeleitete Nachricht ----------\nVon: ")
        builder.entity(EntityType.PERSON, _value(faker.name()))
        builder.add(" <")
        builder.entity(EntityType.EMAIL, _value(faker.email()))
        builder.add(">\nGesendet: Montag, 12. Oktober 2026\nAn: ")
        builder.entity(EntityType.EMAIL, _value(faker.email()))
        builder.add(f"\nBetreff: {SUBJECTS[category]}\n\n")
        builder.add(body)
        builder.add("\n\nViele Grüße\n")
        builder.entity(EntityType.PERSON, _value(faker.name()))
        builder.add("\n")
        builder.entity(EntityType.ORG, _org(faker))
        builder.add("\n")
        builder.entity(EntityType.ADDRESS_STREET, street)
        builder.add("\n")
        builder.entity(EntityType.POSTCODE_CITY, postcode_city)
        builder.add("\nTelefon: ")
        builder.entity(EntityType.PHONE, _phone(faker))

    elif style == "informal_du":
        first = _value(faker.first_name())
        full_name = f"{first} {_value(faker.last_name())}"
        builder.add("Hallo ")
        builder.entity(EntityType.PERSON, first)
        builder.add(",\n\n")
        builder.add(body)
        builder.add("\n\nLG\n")
        builder.entity(EntityType.PERSON, full_name)
        builder.add("\nE-Mail: ")
        builder.entity(EntityType.EMAIL, _value(faker.email()))
        builder.add("\nTelefon: ")
        builder.entity(EntityType.PHONE, _phone(faker))

    elif style == "mixed_people_company":
        street, postcode_city = _address(faker)
        builder.add("Von: ")
        builder.entity(EntityType.PERSON, _value(faker.name()))
        builder.add(" <")
        builder.entity(EntityType.EMAIL, _value(faker.email()))
        builder.add(">\nAn: ")
        builder.entity(EntityType.PERSON, _value(faker.name()))
        builder.add(" <")
        builder.entity(EntityType.EMAIL, _value(faker.email()))
        builder.add(">\nUnternehmen: ")
        builder.entity(EntityType.ORG, _org(faker))
        builder.add("\nTelefon: ")
        builder.entity(EntityType.PHONE, _phone(faker))
        builder.add("\n\n")
        builder.add(body)
        builder.add("\n\nRechnung bitte an:\n")
        builder.entity(EntityType.ADDRESS_STREET, street)
        builder.add("\n")
        builder.entity(EntityType.POSTCODE_CITY, postcode_city)
        builder.add("\nIBAN: ")
        builder.entity(EntityType.IBAN, faker.iban())

    elif style == "hard_cases":
        surname = _word_surname(faker)
        person = f"{_value(faker.first_name())} {surname}"
        company = faker.random_element(
            (
                f"{surname} & Söhne GmbH",
                f"{surname} Systeme GmbH",
                f"{surname} und Partner GmbH",
            )
        )
        street, postcode_city = _address(faker)
        builder.add(f"Betreff: {SUBJECTS[category]}\n\nGuten Tag,\n\n")
        builder.add(body)
        builder.add("\n\nMit freundlichen Grüßen\n")
        builder.entity(EntityType.PERSON, person)
        builder.add("\n")
        builder.entity(EntityType.ORG, company)
        builder.add("\n")
        builder.entity(EntityType.ADDRESS_STREET, street)
        builder.add("\n")
        builder.entity(EntityType.POSTCODE_CITY, postcode_city)
        builder.add("\nTelefon: ")
        builder.entity(EntityType.PHONE, _odd_phone(faker))
        builder.add("\nE-Mail: ")
        builder.entity(EntityType.EMAIL, _value(faker.email()))
        builder.add("\nIBAN: ")
        builder.entity(EntityType.IBAN, _split_iban(faker))

    else:  # pragma: no cover - guarded by STYLES
        raise ValueError(f"unknown style: {style}")

    return builder.build()


def generate(
    seed: int,
    *,
    dev_count: int = DEV_COUNT,
    test_count: int = TEST_COUNT,
) -> list[Enquiry]:
    """Build all records for one seed. Pure apart from the seeded Faker instance."""
    faker = Faker("de_DE")
    faker.seed_instance(seed)
    records: list[Enquiry] = []
    for split, count in (("dev", dev_count), ("test", test_count)):
        for index in range(count):
            category = CATEGORIES[index % len(CATEGORIES)]
            style = STYLES[index % len(STYLES)]
            urgency = URGENCY_CYCLE[index % len(URGENCY_CYCLE)]
            text, spans = _render(faker, style, category, urgency)
            records.append(
                Enquiry(
                    id=f"syn-{split}-{index + 1:04d}",
                    split=split,
                    style=style,
                    text=text,
                    gold_spans=spans,
                    gold_category=category,
                    gold_urgency=urgency,
                )
            )
    return records


def _jsonl(records: Sequence[Enquiry]) -> str:
    lines = [json.dumps(record.model_dump(mode="json"), ensure_ascii=False) for record in records]
    return "\n".join(lines) + "\n"


def _table(headers: Sequence[str], rows: Sequence[Sequence[object]]) -> str:
    lines = [
        "| " + " | ".join(headers) + " |",
        "|" + "|".join("---" for _ in headers) + "|",
    ]
    for row in rows:
        lines.append("| " + " | ".join(str(cell) for cell in row) + " |")
    return "\n".join(lines)


def _readme(records: Sequence[Enquiry], seed: int, seed_note: str) -> str:
    counts: dict[str, Counter[str]] = {
        "style": Counter(),
        "category": Counter(),
        "urgency": Counter(),
        "type": Counter(),
        "total": Counter(),
    }
    for record in records:
        counts["total"][record.split] += 1
        counts["style"][f"{record.split}:{record.style}"] += 1
        counts["category"][f"{record.split}:{record.gold_category.value}"] += 1
        counts["urgency"][f"{record.split}:{record.gold_urgency.value}"] += 1
        for span in record.gold_spans:
            counts["type"][f"{record.split}:{span.type.value}"] += 1

    def split_rows(key: str, values: Sequence[str]) -> list[list[object]]:
        return [
            [
                value if key != "style" else STYLE_LABELS[value],
                counts[key][f"dev:{value}"],
                counts[key][f"test:{value}"],
            ]
            for value in values
        ]

    lines = [
        "# Synthetic enquiries",
        "",
        "Synthetic data only. Names, organisations, street addresses, phone numbers,",
        "e-mail addresses and IBANs are produced by a seeded Faker `de_DE` instance;",
        "the message bodies are hand-written German templates. Nothing here comes from",
        "a real person, a real company or a real account.",
        "",
        f"- Generator: `data/make_synthetic.py` v{GENERATOR_VERSION}",
        f"- Seed: {seed}",
        f"- Seed note: {seed_note}",
        "- Faker locale: `de_DE`",
        f"- Records: dev {counts['total']['dev']}, test {counts['total']['test']}",
        "",
        "Regenerate:",
        "",
        f"    uv run python data/make_synthetic.py --seed {seed}",
        "",
        "## Counts by style",
        "",
        _table(("Style", "dev", "test"), split_rows("style", STYLES)),
        "",
        "## Counts by category",
        "",
        _table(
            ("Category", "dev", "test"),
            split_rows("category", [category.value for category in CATEGORIES]),
        ),
        "",
        "## Counts by urgency",
        "",
        _table(
            ("Urgency", "dev", "test"),
            split_rows("urgency", [urgency.value for urgency in Urgency]),
        ),
        "",
        "## Gold spans by type",
        "",
        _table(
            ("Entity type", "dev", "test"),
            split_rows("type", [entity.value for entity in EntityType]),
        ),
        "",
        "## Notes",
        "",
        "- `gold_spans` offsets are half-open and character-exact: `text[start:end] == span.text`.",
        "- Categories and urgency follow explicit cues in the text: deadlines, "
        "`dringend` and complaint tone mark HIGH; `es eilt nicht`, `keine Eile` and "
        "`unverbindlich` mark LOW; everything else is NORMAL.",
        "- The `hard_cases` records contain surnames that are common words, a person "
        "name inside a company name, an IBAN broken across a line and odd phone formats.",
        "- The data is regenerated only from the seed above. A different Faker version "
        "may change individual values.",
        "",
    ]
    return "\n".join(lines)


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED, help="Faker seed")
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=DEFAULT_OUT_DIR,
        help="directory for dev.jsonl, test.jsonl and README.md",
    )
    parser.add_argument(
        "--seed-note",
        default="none",
        help="short provenance note for the README (for example after a forbidden-scan bump)",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    records = generate(args.seed)
    out_dir: Path = args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    dev = [record for record in records if record.split == "dev"]
    test = [record for record in records if record.split == "test"]
    (out_dir / "dev.jsonl").write_text(_jsonl(dev), encoding="utf-8")
    (out_dir / "test.jsonl").write_text(_jsonl(test), encoding="utf-8")
    (out_dir / "README.md").write_text(
        _readme(records, args.seed, args.seed_note), encoding="utf-8"
    )

    type_counts = Counter(span.type.value for record in test for span in record.gold_spans)
    print(f"seed {args.seed}: dev {len(dev)} records, test {len(test)} records")
    print(
        "test gold spans: "
        + ", ".join(f"{name}={count}" for name, count in sorted(type_counts.items()))
    )
    print(f"wrote {out_dir / 'dev.jsonl'}, {out_dir / 'test.jsonl'}, {out_dir / 'README.md'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
