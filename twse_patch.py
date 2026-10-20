"""TWSE-Patch in Python: TwoWorlds.exe -> TwoWorldsExtended.exe.

Nachbau von buglords twse_patcher.c (Two Worlds 1 Script Extender, CC0),
Byte fuer Byte dieselben Aenderungen und dieselben DJB2-Pruefsummen:
Header (4-GB-Flag, Einsprung auf den TWSE-Lader, Pruefsumme, Sektionsgroessen),
Ladercode fuer twse.dll, Laderdaten, Win11-Texteingabe-Fix (InsideTwoWorlds).
Damit kann das Einstellungs-Tool TWSE selbst anlegen, ohne den Patcher.
"""
import os
import shutil

QUELLE = 'TwoWorlds.exe'
ZIEL = 'TwoWorldsExtended.exe'
TWSE_DLL = 'twse.dll'

# (Start, neue Bytes) - aus twse_patcher.c
AENDERUNGEN = (
    (0x126, bytes([0x23])),                              # LARGE_ADDRESS_AWARE
    (0x138, bytes([0x15, 0x49, 0x57, 0x00])),            # Einsprung -> TWSE-Lader
    (0x168, bytes([0xf6, 0x0e, 0x6a, 0x00])),            # Header-Pruefsumme
    (0x210, bytes([0x00, 0x40])),                        # Sektionsgroessen
    (0x238, bytes([0x00, 0x20])),
    (0x260, bytes([0x00, 0xe0])),
    (0x573d15, bytes([                                   # Ladercode
        0x68, 0x14, 0x67, 0xa2, 0x00, 0xff, 0x15, 0x00,
        0x53, 0x97, 0x00, 0x85, 0xc0, 0x74, 0x13, 0x68,
        0x01, 0x00, 0x00, 0x00, 0x50, 0xff, 0x15, 0x0c,
        0x53, 0x97, 0x00, 0x68, 0x20, 0x67, 0xa2, 0x00,
        0xff, 0xd0, 0xe9, 0x67, 0x2d, 0xf9, 0xff])),
    (0x625514, bytes([                                   # Laderdaten ".\twse.dll", 1700, EMBEDDED
        0x2E, 0x5C, 0x74, 0x77, 0x73, 0x65, 0x2E, 0x64,
        0x6C, 0x6C, 0x00, 0x00, 0xA4, 0x06, 0x00, 0x00,
        0x20, 0x00, 0x00, 0x00])),
    (0x2dfb9a, bytes([0x40, 0x49, 0x97, 0x00])),         # Win11-Fix Hook
    (0x573D40, bytes([                                   # Win11-Fix Code
        0x48, 0x49, 0x97, 0x00, 0x00, 0x00, 0x00, 0x00,
        0x55, 0x89, 0xe5, 0x57, 0x8b, 0x45, 0x18, 0x50,
        0x68, 0x09, 0x01, 0x00, 0x00, 0x68, 0x00, 0x01,
        0x00, 0x00, 0x6a, 0x00, 0x8b, 0x45, 0x08, 0x50,
        0x8b, 0x3d, 0xa8, 0x53, 0x97, 0x00, 0xff, 0xd7,
        0x5f, 0x5d, 0xc2, 0x14, 0x00])),
)

# Pruefbereiche und bekannte Zustaende (1 = sauber, 2 = fremder Patch, 10 = TWSE drin)
PRUEFUNG = (
    ('header', 0x100, 0x300, {0xeb0eead9: 1, 0x58f12b19: 2, 0x91484d17: 10}),
    ('win11_hook', 0x2dfb9a, 0x2dfb9e, {0x7cbaa3b7: 1, 0x7c6b829d: 2, 0x7c8171c5: 10}),
    ('win11_code', 0x573d40, 0x573d6d, {0x9a1ea925: 1, 0xde281617: 2, 0x61b87b2e: 10}),
    ('twse_code', 0x573d15, 0x573d3c, {0x3df3f165: 1, 0x246d1307: 2, 0xe0c78c4c: 10}),
    ('twse_data', 0x625514, 0x625528, {0xa5cd7985: 1, 0xa83fab66: 10}),
)


def djb2(daten, start, ende):
    h = 5381
    for x in daten[start:ende]:
        h = (h * 33 + x) & 0xffffffff
    return h


def pruefen(daten):
    """Zustand je Bereich; 'fehler' wenn ein Bereich unbekannt ist."""
    zustand = {}
    for name, s, e, bekannt in PRUEFUNG:
        if e > len(daten):
            zustand[name] = -1
            continue
        zustand[name] = bekannt.get(djb2(daten, s, e), -1)
    zustand['fehler'] = any(v == -1 for v in zustand.values())
    zustand['fertig'] = all(v == 10 for k, v in zustand.items() if k not in ('fehler', 'fertig'))
    return zustand


def anwenden(daten):
    """Gibt die gepatchte Kopie zurueck (alle Aenderungen, wie der Patcher)."""
    b = bytearray(daten)
    for start, neu in AENDERUNGEN:
        if start + len(neu) > len(b):
            raise ValueError('Datei zu kurz fuer Patch bei 0x%x' % start)
        b[start:start + len(neu)] = neu
    return bytes(b)


def installieren(spiel, twse_dll_quelle):
    """Legt TwoWorldsExtended.exe und twse.dll im Spielordner an, wenn sie fehlen.

    Rueckgabe: Liste kurzer Meldungen (was getan wurde). Wirft ValueError,
    wenn TwoWorlds.exe unbekannt ist (andere Version).
    """
    getan = []
    ziel = os.path.join(spiel, ZIEL)
    quelle = os.path.join(spiel, QUELLE)
    braucht_exe = True
    if os.path.exists(ziel):
        with open(ziel, 'rb') as f:
            if pruefen(f.read())['fertig']:
                braucht_exe = False
    if braucht_exe:
        if not os.path.exists(quelle):
            raise ValueError('TwoWorlds.exe fehlt im Spielordner')
        with open(quelle, 'rb') as f:
            daten = f.read()
        z = pruefen(daten)
        if z['fehler']:
            raise ValueError('TwoWorlds.exe ist keine bekannte Fassung (1.7 erwartet)')
        neu = daten if z['fertig'] else anwenden(daten)
        if not pruefen(neu)['fertig']:
            raise ValueError('Patch-Ergebnis besteht die Pruefung nicht')
        tmp = ziel + '.tmp'
        with open(tmp, 'wb') as f:
            f.write(neu)
        os.replace(tmp, ziel)
        getan.append(ZIEL)
    dll = os.path.join(spiel, TWSE_DLL)
    if not os.path.exists(dll) or os.path.getsize(dll) != os.path.getsize(twse_dll_quelle):
        shutil.copy2(twse_dll_quelle, dll)
        getan.append(TWSE_DLL)
    return getan


if __name__ == '__main__':
    import sys
    with open(sys.argv[1], 'rb') as f:
        print(pruefen(f.read()))
