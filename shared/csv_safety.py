"""CSV formula-injection (spreadsheet "CSV injection") hardening.

Every CSV that Elder emits and a user may open in Excel/LibreOffice/Sheets must
pass its cells through :func:`neutralize_cell` (or use :func:`safe_writer` /
:func:`safe_dict_writer`). Follows the OWASP CSV Injection guidance: string
cells whose first non-whitespace character is ``=``, ``+``, ``-``, ``@``, tab or
carriage return are prefixed with a single quote so spreadsheet applications
treat them as text. Quoting of embedded quotes/separators is left to the
``csv`` module (never build CSV rows with manual string joins).

Import/export policy: the CSV importer (API upload and the svc-worker S3 batch
importer, both via ``apps.api.services.csv_import.processor``) already rejects
string fields whose first non-whitespace character is a trigger, so formula
payloads never enter via CSV. Values that reach the DB by other routes (API,
UI, integrations) are stored raw and neutralised on every export. Export-side
neutralisation is therefore the authoritative control; import rejection is
defence in depth.

Numeric values (``int``/``float``/``Decimal``, excluding ``bool``) pass through
untouched, so legitimate negative numbers in numeric columns stay numbers.
"""

from __future__ import annotations

import csv
from collections.abc import Iterable, Mapping
from decimal import Decimal
from typing import Any

FORMULA_TRIGGERS: tuple[str, ...] = ("=", "+", "-", "@", "\t", "\r")


def neutralize_cell(value: Any) -> Any:
    """Return ``value`` made safe for a spreadsheet cell.

    Non-string numbers and ``None`` are returned unchanged; anything else is
    stringified and prefixed with ``'`` if it (or its whitespace-stripped form)
    starts with a formula trigger character.
    """
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, (int, float, Decimal)):
        return value
    text = value if isinstance(value, str) else str(value)
    if not text:
        return text
    if text.startswith(FORMULA_TRIGGERS) or text.lstrip().startswith(FORMULA_TRIGGERS):
        return "'" + text
    return text


def neutralize_row(row: Iterable[Any]) -> list[Any]:
    """Neutralise every cell of a sequence row."""
    return [neutralize_cell(v) for v in row]


def neutralize_mapping(row: Mapping[str, Any]) -> dict[str, Any]:
    """Neutralise every value of a dict row (keys/headers are neutralised too)."""
    return {k: neutralize_cell(v) for k, v in row.items()}


class SafeCsvWriter:
    """``csv.writer`` wrapper that neutralises every cell it writes."""

    def __init__(self, fileobj: Any, **kwargs: Any) -> None:
        """Create the wrapped writer; ``kwargs`` are passed to ``csv.writer``."""
        self._writer = csv.writer(fileobj, **kwargs)

    def writerow(self, row: Iterable[Any]) -> Any:
        """Write one neutralised row."""
        return self._writer.writerow(neutralize_row(row))

    def writerows(self, rows: Iterable[Iterable[Any]]) -> None:
        """Write many neutralised rows."""
        for row in rows:
            self.writerow(row)


class SafeDictWriter:
    """``csv.DictWriter`` wrapper that neutralises headers and values."""

    def __init__(self, fileobj: Any, fieldnames: Iterable[str], **kwargs: Any):
        """Create the wrapped DictWriter; ``kwargs`` go to ``csv.DictWriter``."""
        self._fieldnames = list(fieldnames)
        self._fileobj = fileobj
        self._writer = csv.DictWriter(fileobj, fieldnames=self._fieldnames, **kwargs)

    def writeheader(self) -> Any:
        """Write the (neutralised) header row."""
        return csv.writer(self._fileobj, dialect=self._writer.writer.dialect).writerow(
            neutralize_row(self._fieldnames)
        )

    def writerow(self, row: Mapping[str, Any]) -> Any:
        """Write one neutralised dict row."""
        return self._writer.writerow(neutralize_mapping(row))

    def writerows(self, rows: Iterable[Mapping[str, Any]]) -> None:
        """Write many neutralised dict rows."""
        for row in rows:
            self.writerow(row)


def safe_writer(fileobj: Any, **kwargs: Any) -> SafeCsvWriter:
    """Return a formula-neutralising drop-in for ``csv.writer``."""
    return SafeCsvWriter(fileobj, **kwargs)


def safe_dict_writer(
    fileobj: Any, fieldnames: Iterable[str], **kwargs: Any
) -> SafeDictWriter:
    """Return a formula-neutralising drop-in for ``csv.DictWriter``."""
    return SafeDictWriter(fileobj, fieldnames, **kwargs)
