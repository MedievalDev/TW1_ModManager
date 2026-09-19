"""SDK names for par fields (TwoWorlds.xls of the SDK, table of the TW1 PAR Editor).

``tw1_sdk_fields.json``: 'sheets' {sheet: [column names]}, 'entries'
{entry name: sheet}. A field name is only given when the entry is known and
the sheet has a column for that index.
"""
import json
import os
import sys

_data = None


def _load():
    global _data
    if _data is None:
        base = getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__)))
        try:
            with open(os.path.join(base, 'tw1_sdk_fields.json'), encoding='utf-8') as f:
                _data = json.load(f)
        except (OSError, ValueError):
            _data = {'sheets': {}, 'entries': {}}
    return _data


def field_name(entry_name, index):
    d = _load()
    cols = d['sheets'].get(d['entries'].get(entry_name) or '', [])
    return cols[index] if 0 <= index < len(cols) else None
