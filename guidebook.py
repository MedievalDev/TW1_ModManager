"""Guide window of the Mod Manager: Help > Guide, F1 (PY_TOOL_DESIGN.md 6.2).

Chapter tree on the left, text on the right, search on top, German and
English. The reference tables come from the same constants the tool uses
(mod_manager.REG_MODS, the folder names), each with a source line.
"""

import re
import tkinter as tk
from tkinter import ttk

import theme


def _lang():
    import mod_manager as M
    return M._LANG


def _l(de, en):
    return de if _lang() == 'de' else en


def _table(head, rows):
    out = ['| ' + ' | '.join(head) + ' |', '|' + '---|' * len(head)]
    for r in rows:
        out.append('| ' + ' | '.join(str(c) for c in r) + ' |')
    return '\n'.join(out)


def _source(text):
    return _l('Quelle: ', 'Source: ') + text + '\n'


def ch_start():
    keys = [('Strg+O', 'Ctrl+O', _l('Mod-Datei waehlen und einlegen', 'Pick a mod file and install it')),
            ('F5', 'F5', _l('Liste neu lesen', 'Reload the list')),
            ('Enter / Doppelklick', 'Enter / double-click', _l('Mod ein- oder ausschalten', 'Enable or disable the mod')),
            ('Entf', 'Del', _l('Mod entfernen (Kopie bleibt)', 'Remove the mod (a copy is kept)')),
            ('F1', 'F1', _l('Dieser Guide', 'This guide'))]
    rows = [(k[0] if _lang() == 'de' else k[1], k[2]) for k in keys]
    return _l('''# Einstieg

Two Worlds (2007) laedt Mods aus dem Ordner `Mods\\` neben der Spiel-Exe.
Jede Mod ist ein `.wd`-Archiv, und ob es beim Start geladen wird, steht in
der Windows-Registry - genau da, wo der "TwoWorlds1 Mod Selector" des
Spiels seine Haken setzt. Der Mod Manager zeigt beides in einer Liste,
schaltet um und legt neue Archive ein.

## Das Fenster

- **Reiter Installed mods:** alle Archive im Mods-Ordner mit Schalter,
  Groesse und Datum. Doppelklick schaltet um.
- **Ziehen und Ablegen:** eine `.wd` aus dem Explorer aufs Fenster ziehen -
  ein Fenster fragt, ob die Mod eingelegt werden soll.
- **Statusleiste:** was zuletzt passiert ist.

Nichts wird geloescht: Ausschalten setzt nur den Registry-Wert auf 0,
Entfernen verschiebt das Archiv nach `Mods\\_removed\\`.

## Tastenkuerzel

''', '''# Getting started

Two Worlds (2007) loads mods from the `Mods\\` folder next to the game exe.
Every mod is a `.wd` archive, and whether it loads at start is written in
the Windows registry - exactly where the game's "TwoWorlds1 Mod Selector"
puts its ticks. The Mod Manager shows both in one list, toggles them,
and installs new archives.

## The window

- **Installed mods tab:** every archive in the Mods folder with its
  switch, size and date. Double-click toggles.
- **Drag and drop:** drag a `.wd` from Explorer onto the window - a dialog
  asks whether to install the mod.
- **Status bar:** what happened last.

Nothing is deleted: disabling only sets the registry value to 0, removing
moves the archive to `Mods\\_removed\\`.

## Keyboard shortcuts

''') + _table([_l('Taste', 'Key'), _l('Wirkung', 'Action')], rows) + '\n' + _source('mod_manager.py, _bind_keys')


def ch_install():
    return _l('''# Mod einlegen

## Ziehen und Ablegen

Eine `.wd` aus dem Explorer auf das Fenster ziehen. Es oeffnet sich ein
Fenster mit Name, Groesse, Anzahl der Dateien im Archiv und den obersten
Ordnern (`Parameters`, `Levels`, `Scripts` ...). Liegt schon eine Datei
gleichen Namens im Mods-Ordner, sagt das Fenster, ob sie identisch ist
oder ersetzt wuerde - die alte bleibt als `.backup`.

- **Install and enable:** kopieren und in der Registry auf 1 setzen.
- **Install only:** kopieren, Schalter bleibt aus.
- **Cancel:** nichts passiert.

Mehrere Dateien auf einmal gehen auch; jede bekommt ihr Fenster.

## Ueber den Knopf

"Add external mod" (Strg+O) oeffnet den Dateidialog und fuehrt zum selben
Fenster.

## Was geprueft wird

Das Archiv muss ein WD-Archiv des Spiels sein (Kopf `FF A1 D0 31 'WD' 00 02`
im ersten zlib-Strom, gemessen an allen ausgelieferten Archiven). Alles
andere lehnt das Fenster ab, damit keine Zip-Datei im Mods-Ordner landet.

Laeuft das Spiel, verweigert der Manager das Einlegen: Two Worlds liest die
Mod-Liste nur beim Start.
''', '''# Installing a mod

## Drag and drop

Drag a `.wd` from Explorer onto the window. A dialog opens with name, size,
number of files in the archive and its top folders (`Parameters`,
`Levels`, `Scripts` ...). If a file of the same name already sits in the
Mods folder, the dialog says whether it is identical or would be replaced -
the old one stays as `.backup`.

- **Install and enable:** copy and set the registry switch to 1.
- **Install only:** copy, switch stays off.
- **Cancel:** nothing happens.

Several files at once work too; each gets its own dialog.

## Through the button

"Add external mod" (Ctrl+O) opens the file dialog and leads to the same
window.

## What is checked

The archive must be a WD archive of the game (header `FF A1 D0 31 'WD' 00
02` in the first zlib stream, measured on every shipped archive). Anything
else is refused, so no zip file ends up in the Mods folder.

While the game runs the manager refuses to install: Two Worlds reads the
mod list only at start.
''')


def ch_switch():
    return _l('''# Ein- und ausschalten

Doppelklick auf eine Zeile, Enter, oder der Knopf "Enable / disable".

Das Spiel liest beim Start den Registry-Schluessel
`HKCU\\SOFTWARE\\Reality Pump\\TwoWorlds\\Mods`. Jeder Wert darin ist ein
Archivname mit DWORD 1 (laden) oder 0 (nicht laden). Ein Archiv im
Mods-Ordner **ohne** Wert wird geladen - `0` ist ein bewusster
Ausschalter, kein Standard.

## Archive im Spielordner

Liegt eine `.wd` direkt neben `TwoWorlds.exe`, laedt das Spiel sie immer,
egal was in der Registry steht. Der Manager zeigt solche Dateien rot mit
"ALWAYS loads". Ausschalten geht nur, indem man sie aus dem Spielordner
bewegt.

## Reihenfolge

Spaeter geladene Archive gewinnen. Liegen zwei Mods mit derselben Datei
(etwa `Parameters\\TwoWorlds.par`) nebeneinander, gilt die, die das Spiel
zuletzt laedt. Welche das ist, haengt an der Ladereihenfolge des Spiels
und ist hier nicht gemessen - im Zweifel nur eine davon einschalten.

## Entfernen

"Remove (keeps a copy)" verschiebt das Archiv nach `Mods\\_removed\\` und
setzt den Wert auf 0. Zurueckholen: die Datei zurueck in `Mods\\` legen und
einschalten.
''', '''# Enabling and disabling

Double-click a row, press Enter, or use the "Enable / disable" button.

At start the game reads the registry key
`HKCU\\SOFTWARE\\Reality Pump\\TwoWorlds\\Mods`. Every value in it is an
archive name with DWORD 1 (load) or 0 (do not load). An archive in the
Mods folder **without** a value is loaded - `0` is a deliberate off
switch, not a default.

## Archives in the game folder

A `.wd` right next to `TwoWorlds.exe` is always loaded, whatever the
registry says. The manager shows such files in red with "ALWAYS loads".
The only way to disable them is to move them out of the game folder.

## Order

Later archives win. With two mods that carry the same file (say
`Parameters\\TwoWorlds.par`), the one the game loads last applies. Which
one that is depends on the game's load order and is not measured here -
when in doubt, enable only one of them.

## Removing

"Remove (keeps a copy)" moves the archive to `Mods\\_removed\\` and sets
the value to 0. To bring it back, move the file into `Mods\\` again and
enable it.
''')


def ch_reference():
    import mod_manager as M
    rows = [(M.REG_MODS, _l('Schluessel; ein DWORD je Archivname', 'key; one DWORD per archive name')),
            ('<Name>.wd = 1', _l('laden', 'load')),
            ('<Name>.wd = 0', _l('nicht laden (bewusster Ausschalter)', 'do not load (deliberate off switch)')),
            (_l('kein Wert', 'no value'), _l('Archiv im Mods-Ordner wird geladen', 'archive in the Mods folder is loaded'))]
    folders = [('Mods\\', _l('die Archive, die der Manager verwaltet', 'the archives the manager handles')),
               ('Mods\\_removed\\', _l('"Remove" verschiebt hierher, nichts wird geloescht', '"Remove" moves here, nothing is deleted')),
               ('Mods\\<Name>.wd.backup', _l('alte Fassung vor einem Ersatz', 'previous version before a replacement')),
               ('<Spiel>\\*.wd', _l('laedt immer, Registry wirkungslos', 'always loads, registry has no effect'))]
    head = _l('# Referenztabellen\n\n## Registry\n\n', '# Reference tables\n\n## Registry\n\n')
    out = head + _table([_l('Eintrag', 'Entry'), _l('Bedeutung', 'Meaning')], rows) + '\n'
    out += _source('mod_manager.py REG_MODS; ' + _l(
        'Verhalten gemessen am Mod Selector des Spiels und an Archiven ohne Wert (QuestForge, 2026)',
        'behaviour measured against the game\'s Mod Selector and archives without a value (QuestForge, 2026)'))
    out += _l('\n## Ordner und Dateien\n\n', '\n## Folders and files\n\n')
    out += _table([_l('Ort', 'Place'), _l('Bedeutung', 'Meaning')], folders) + '\n'
    out += _source('mod_manager.py (install, remove, root archives)')
    return out


def ch_trouble():
    return _l('''# Fehlersuche

## "Two Worlds install not found"

Der Manager sucht die gespeicherte Wahl, dann `DataDir` in der Registry,
dann die Steam-Bibliotheken. Datei > Change game path zeigt auf den Ordner
mit `WDFiles\\`.

## "Close Two Worlds first"

Das Spiel laeuft. Es liest die Mod-Liste nur beim Start; einlegen und
umschalten wartet, bis es zu ist.

## Mod ist eingeschaltet, im Spiel passiert nichts

- Neues Spiel gestartet? Karten und Skripte stecken in alten Spielstaenden
  fest - dafuer gibt es den TW1 Savegame Patcher.
- Zwei Mods mit derselben Datei? Die spaeter geladene gewinnt.
- Liegt die Datei wirklich in `Mods\\` und nicht in einem Unterordner?

## "file missing"

In der Registry steht ein Name, die Datei fehlt im Mods-Ordner. Entweder
die Datei zurueck (auch aus `_removed`), oder den Eintrag mit "Remove"
loeschen.

## Ablegen tut nichts

Ziehen und Ablegen braucht Windows und ein nicht als Administrator
gestartetes Programm (Windows blockt Drops von normalen Programmen auf
erhoehte). Der Knopf "Add external mod" tut dasselbe.
''', '''# Troubleshooting

## "Two Worlds install not found"

The manager tries the saved choice, then `DataDir` in the registry, then
the Steam libraries. File > Change game path points to the folder with
`WDFiles\\`.

## "Close Two Worlds first"

The game is running. It reads the mod list only at start; installing and
toggling wait until it is closed.

## Mod is enabled, nothing happens in the game

- Started a new game? Maps and scripts are frozen inside old saves - that
  is what the TW1 Savegame Patcher is for.
- Two mods with the same file? The one loaded later wins.
- Is the file really in `Mods\\` and not in a subfolder?

## "file missing"

The registry names a file that is not in the Mods folder. Either put the
file back (also from `_removed`), or drop the entry with "Remove".

## Dropping does nothing

Drag and drop needs Windows and a program that is not running as
administrator (Windows blocks drops from normal programs onto elevated
ones). The "Add external mod" button does the same thing.
''')


def ch_twse():
    return _l('''# Pakete, Plugins und TWSE

## Mod-Pakete (.zip)

Manche Mods bestehen aus mehreren Dateien: einer `.wd` und einem Plugin
fuer den Script Extender (`.dll` mit `.ini`). Solche Mods kommen als `.zip`.
Das zip aufs Fenster ziehen oder ueber "Add external mod" waehlen.

Vor dem Einlegen prueft der Manager das zip:

- keine Programme oder Skripte (`.exe`, `.bat`, `.ps1`, `.vbs` ...) - sonst wird das ganze Paket abgelehnt
- keine Pfade nach aussen (`..`, Laufwerksbuchstaben)
- jede `.wd` muss ein Two-Worlds-Archiv sein, jede `.dll` eine 32-Bit-DLL
- keine Datei, die schon zu einem anderen Paket gehoert

Wohin die Dateien gehen:

''' + _table(('Im zip', 'Ziel'), [
        ('Mods/<Name>.wd', 'Mods\\<Name>.wd'),
        ('TWSEPlugins/<Name>.dll, .ini', 'TWSEPlugins\\'),
        ('<Name>.wd (ohne Ordner)', 'Mods\\'),
        ('<Name>.dll, <Name>.ini (ohne Ordner)', 'TWSEPlugins\\'),
        ('readme.txt, Bilder', 'nur angezeigt'),
        ('modinfo.json', 'Name, Version, requires: ["twse"]')]) + '\n' + _source('packages.py (inspect)') + '''
Ein einzelner Ordner um alles herum wird uebersprungen. Der Manager merkt sich
in `Mods\\_packages.json`, welche Datei zu welchem Paket gehoert.

## Ein- und ausschalten, entfernen

Ein Paket ist eine Zeile in der Liste, seine Plugin-Dateien liegen darunter.
Doppelklick schaltet alles zusammen: die `.wd` ueber den Registry-Schalter,
die `.dll` und `.ini` wandern nach `TWSEPlugins\\_disabled\\<Paket>\\`. TWSE
laedt nur DLLs direkt in `TWSEPlugins`, Unterordner nie.

"Remove" verschiebt alle Dateien des Pakets nach `Mods\\_removed\\<Paket>\\`.
Geloescht wird nichts.

Bei einem Update bleibt eine Ini, die du geaendert hast; die neue liegt
daneben als `<Name>.ini.new`.

Plugins ohne Paket (zum Beispiel `TWExtended.dll` von Extended Settings)
stehen als eigene Zeile da und lassen sich genauso schalten.

## TWSE

TWSE ist der Two Worlds Script Extender von buglord. Er laedt die Plugins,
aber nur wenn das Spiel ueber `TwoWorldsExtended.exe` startet.

"Install TWSE":

- laedt das neueste Release von buglords GitHub und prueft die Pruefsumme. Eine twse.dll, die wir noch nicht geprueft haben, wird erst nach Rueckfrage eingespielt. Ohne Verbindung nimmt der Manager seine eigene, gepruefte twse.dll.
- legt `TwoWorldsExtended.exe` an (dieselben Bytes wie buglords Patcher) und `twse.dll`, dazu den Ordner `TWSEPlugins`. `TwoWorlds.exe` bleibt unveraendert.
- stellt Verknuepfungen auf dem Desktop und im Startmenue, die auf `TwoWorlds.exe` zeigen, auf `TwoWorldsExtended.exe` um und legt "Two Worlds (TWSE)" auf den Desktop.

Steam, GOG Galaxy und der Mod Selector starten immer `TwoWorlds.exe`. Dafuer
gibt es den Schalter "Replace TwoWorlds.exe": dann wird `TwoWorlds.exe`
selbst zur Extended-Fassung, das Original bleibt als
`TwoWorlds.exe.twse-backup`. "Dateien pruefen" in Steam oder ein Update
holt das Original zurueck; der Manager zeigt das an.

"Remove TWSE" dreht alles zurueck: alte Verknuepfungsziele, die originale
`TwoWorlds.exe`, und `TwoWorldsExtended.exe` und `twse.dll` wandern nach
`Mods\\_removed\\TWSE_<Datum>\\`. Was getan wurde, steht in
`Mods\\_twse_install.json`.

"Start game" startet `TwoWorldsExtended.exe`, wenn TWSE da ist, sonst
`TwoWorlds.exe`.
''', '''# Packages, plugins and TWSE

## Mod packages (.zip)

Some mods consist of several files: a `.wd` and a plugin for the Script
Extender (`.dll` with an `.ini`). Such mods come as a `.zip`. Drop the zip
onto the window or pick it with "Add external mod".

Before anything is installed the manager checks the zip:

- no programs or scripts (`.exe`, `.bat`, `.ps1`, `.vbs` ...) - otherwise the whole package is refused
- no paths leading outside (`..`, drive letters)
- every `.wd` must be a Two Worlds archive, every `.dll` a 32-bit DLL
- no file that already belongs to another package

Where the files go:

''' + _table(('In the zip', 'Target'), [
        ('Mods/<name>.wd', 'Mods\\<name>.wd'),
        ('TWSEPlugins/<name>.dll, .ini', 'TWSEPlugins\\'),
        ('<name>.wd (no folder)', 'Mods\\'),
        ('<name>.dll, <name>.ini (no folder)', 'TWSEPlugins\\'),
        ('readme.txt, pictures', 'only shown'),
        ('modinfo.json', 'name, version, requires: ["twse"]')]) + '\n' + _source('packages.py (inspect)') + '''
One folder around everything is skipped. The manager remembers in
`Mods\\_packages.json` which file belongs to which package.

## Enabling, disabling, removing

A package is one row in the list, its plugin files sit below it. A
double-click switches everything together: the `.wd` through its registry
switch, the `.dll` and `.ini` move to `TWSEPlugins\\_disabled\\<package>\\`.
TWSE loads only DLLs directly in `TWSEPlugins`, never from subfolders.

"Remove" moves every file of the package to `Mods\\_removed\\<package>\\`.
Nothing is deleted.

On an update an ini you changed stays; the new one lies next to it as
`<name>.ini.new`.

Plugins without a package (for example `TWExtended.dll` from Extended
Settings) have a row of their own and switch the same way.

## TWSE

TWSE is the Two Worlds Script Extender by buglord. It loads the plugins, but
only when the game starts through `TwoWorldsExtended.exe`.

"Install TWSE":

- downloads the newest release from buglord's GitHub and checks the checksum. A twse.dll we have not checked yet is only installed after you agree. Without a connection the manager uses its own checked twse.dll.
- creates `TwoWorldsExtended.exe` (the same bytes as buglord's patcher) and `twse.dll`, plus the folder `TWSEPlugins`. `TwoWorlds.exe` is not changed.
- points shortcuts on the desktop and in the start menu that start `TwoWorlds.exe` to `TwoWorldsExtended.exe` and adds "Two Worlds (TWSE)" to the desktop.

Steam, GOG Galaxy and the Mod Selector always start `TwoWorlds.exe`. That is
what the switch "Replace TwoWorlds.exe" is for: `TwoWorlds.exe` itself
becomes the extended exe, the original stays as `TwoWorlds.exe.twse-backup`.
"Verify files" in Steam or an update brings the original back; the manager
shows it.

"Remove TWSE" turns everything back: old shortcut targets, the original
`TwoWorlds.exe`, and `TwoWorldsExtended.exe` and `twse.dll` move to
`Mods\\_removed\\TWSE_<date>\\`. What was done is written to
`Mods\\_twse_install.json`.

"Start game" starts `TwoWorldsExtended.exe` when TWSE is there, otherwise
`TwoWorlds.exe`.
''')


CHAPTERS = (
    ('start', ('Einstieg', 'Getting started'), ch_start),
    ('install', ('Mod einlegen', 'Installing a mod'), ch_install),
    ('switch', ('Ein- und ausschalten', 'Enabling and disabling'), ch_switch),
    ('twse', ('Pakete, Plugins und TWSE', 'Packages, plugins and TWSE'), ch_twse),
    ('reference', ('Referenztabellen', 'Reference tables'), ch_reference),
    ('trouble', ('Fehlersuche', 'Troubleshooting'), ch_trouble),
)

HELP_CHAPTER = {'help.installed': 'switch', 'help.drop': 'install',
                'help.game': 'trouble'}


_SEPARATOR = re.compile(r'^\|[\s|:-]+\|?$')
_LIST_ITEM = re.compile(r'^(- |\d+\. )')


def _prepare(text):
    """Join wrapped prose lines into paragraphs and turn markdown tables into
    aligned columns, so the text widget shows them readably."""
    out, para, table, in_code = [], [], [], False

    def flush_para():
        if para:
            out.append(' '.join(x.strip() for x in para))
            para.clear()

    def flush_table():
        if not table:
            return
        rows = [[c.strip().replace('`', '') for c in r.strip().strip('|').split('|')]
                for r in table if not _SEPARATOR.match(r.strip())]
        ncol = max(len(r) for r in rows)
        widths = [max(len(r[i]) if i < len(r) else 0 for r in rows) for i in range(ncol)]
        out.append('```')
        for n, r in enumerate(rows):
            cells = [(r[i] if i < len(r) else '').ljust(widths[i]) for i in range(ncol)]
            out.append('  '.join(cells).rstrip())
            if n == 0:
                out.append('  '.join('-' * w for w in widths))
        out.append('```')
        table.clear()

    for ln in text.split('\n'):
        if ln.startswith('```'):
            flush_para()
            flush_table()
            in_code = not in_code
            out.append(ln)
            continue
        if in_code:
            out.append(ln)
            continue
        if ln.startswith('|'):
            flush_para()
            table.append(ln)
            continue
        flush_table()
        stripped = ln.strip()
        if not stripped or ln.startswith('#'):
            flush_para()
            out.append(ln)
        elif _LIST_ITEM.match(stripped):
            flush_para()
            para.append(ln)
        else:
            para.append(ln)
    flush_para()
    flush_table()
    return '\n'.join(out)


def render_markdown(txt, text):
    in_code = False
    for line in text.split('\n'):
        if line.startswith('```'):
            in_code = not in_code
            continue
        if in_code or line.startswith('|'):
            txt.insert('end', line + '\n', 'code')
            continue
        m = re.match(r'(#{1,3}) (.*)', line)
        if m:
            txt.insert('end', m.group(2) + '\n', 'h%d' % len(m.group(1)))
            continue
        tag = None
        if re.match(r'\s*[-*] ', line):
            line = '• ' + re.sub(r'^\s*[-*] ', '', line)
            tag = 'li'
        elif re.match(r'\s*\d+\. ', line):
            tag = 'li'
        for part in re.split(r'(`[^`]+`|\*\*[^*]+\*\*)', line):
            if part.startswith('`') and part.endswith('`') and len(part) > 1:
                txt.insert('end', part[1:-1], ('inline',) + ((tag,) if tag else ()))
            elif part.startswith('**') and part.endswith('**'):
                txt.insert('end', part[2:-2], ('bold',) + ((tag,) if tag else ()))
            else:
                part = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', part)
                txt.insert('end', part, tag)
        txt.insert('end', '\n', tag)


def chapter_text(cid):
    for key, _title, fn in CHAPTERS:
        if key == cid:
            return fn()
    return ''


def check_sources():
    """Every table in every chapter (both languages) carries a source line."""
    import mod_manager as M
    saved = M._LANG
    try:
        for lang in ('de', 'en'):
            M._LANG = lang
            for cid, _t, fn in CHAPTERS:
                lines = fn().split('\n')
                for i, ln in enumerate(lines):
                    if ln.startswith('|---'):
                        j = i + 1
                        while j < len(lines) and lines[j].startswith('|'):
                            j += 1
                        tail = '\n'.join(lines[j:j + 3])
                        assert 'Quelle:' in tail or 'Source:' in tail, f'{cid}/{lang}: table without source'
    finally:
        M._LANG = saved


class GuideWindow:
    _open = None

    @classmethod
    def show(cls, app, chapter='start'):
        win = cls._open
        if win is not None:
            try:
                win.front()
                win.select(chapter)
                return win
            except tk.TclError:
                cls._open = None
        cls._open = cls(app, chapter)
        return cls._open

    def __init__(self, app, chapter='start'):
        self.app = app
        self.win = tk.Toplevel(app.root)
        self.win.title('Mod Manager Guide')
        self.win.geometry('1000x700')
        self.win.minsize(820, 520)
        theme.dark_titlebar(self.win)
        self.win.protocol('WM_DELETE_WINDOW', self.close)
        self.win.bind('<Escape>', lambda e: self.close())
        top = ttk.Frame(self.win, padding=(10, 8))
        top.pack(fill='x')
        ttk.Label(top, text=_l('Suche', 'Search')).pack(side='left')
        self.q = tk.StringVar()
        ent = ttk.Entry(top, textvariable=self.q, width=32)
        ent.pack(side='left', padx=6)
        ent.bind('<KeyRelease>', lambda e: self._search())
        self.hits = ttk.Label(top, style='Muted.TLabel')
        self.hits.pack(side='left', padx=8)
        body = ttk.PanedWindow(self.win, orient='horizontal')
        body.pack(fill='both', expand=True)
        left = ttk.Frame(body)
        self.tree = ttk.Treeview(left, show='tree', selectmode='browse')
        self.tree.pack(fill='both', expand=True)
        self.tree.bind('<<TreeviewSelect>>', lambda e: self._show_selected())
        right = ttk.Frame(body)
        sb = ttk.Scrollbar(right, orient='vertical')
        self.txt = tk.Text(right, wrap='word', bd=0, padx=26, pady=20, cursor='arrow',
                           spacing1=2, spacing3=4, yscrollcommand=sb.set, font=('Segoe UI', 10))
        sb.configure(command=self.txt.yview)
        sb.pack(side='right', fill='y')
        self.txt.pack(fill='both', expand=True)
        for tag, kw in (('h1', dict(font=theme.FONT_H1, foreground=theme.GOLD, spacing1=18)),
                        ('h2', dict(font=theme.FONT_H2, foreground=theme.GOLD_HI, spacing1=14)),
                        ('h3', dict(font=('Segoe UI Semibold', 10), foreground=theme.GOLD_HI, spacing1=8)),
                        ('li', dict(lmargin1=20, lmargin2=34)),
                        ('code', dict(font=theme.FONT_MONO, background=theme.FIELD, lmargin1=16, lmargin2=16)),
                        ('inline', dict(font=theme.FONT_MONO, foreground=theme.GOLD_HI)),
                        ('bold', dict(font=('Segoe UI Semibold', 10))),
                        ('hit', dict(background=theme.SEL, foreground=theme.GOLD_HI))):
            self.txt.tag_configure(tag, **kw)
        body.add(left, weight=0)
        body.add(right, weight=1)
        self.win.update_idletasks()
        try:
            body.sashpos(0, 250)
        except tk.TclError:
            pass
        self._fill_tree()
        self.select(chapter)
        self.front()

    def front(self):
        """Das Fenster nach vorn holen - sonst geht es hinter dem Hauptfenster auf."""
        try:
            self.win.lift()
            self.win.focus_force()
            self.win.attributes('-topmost', True)          # einmal nach vorn,
            self.win.after(120, lambda: self.win.attributes('-topmost', False))
        except tk.TclError:                                # und gleich wieder normal
            pass

    def close(self):
        GuideWindow._open = None
        self.win.destroy()

    def _fill_tree(self, only=None):
        self.tree.delete(*self.tree.get_children())
        lang = 0 if _lang() == 'de' else 1
        for i, (cid, titles, _fn) in enumerate(CHAPTERS, start=1):
            if only is not None and cid not in only:
                continue
            self.tree.insert('', 'end', iid=cid, text=f'{i}. {titles[lang]}')

    def select(self, cid):
        if cid not in {c for c, _t, _f in CHAPTERS}:
            cid = 'start'
        if not self.tree.exists(cid):
            self._fill_tree()
        self.tree.selection_set(cid)
        self.tree.see(cid)
        self._show(cid)

    def _show_selected(self):
        sel = self.tree.selection()
        if sel:
            self._show(sel[0])

    def _show(self, cid):
        self.current = cid
        self.txt.configure(state='normal')
        self.txt.delete('1.0', 'end')
        render_markdown(self.txt, _prepare(chapter_text(cid)))
        self._mark_hits()
        self.txt.configure(state='disabled')

    def _search(self):
        needle = self.q.get().strip().lower()
        if not needle:
            self._fill_tree()
            self.hits.configure(text='')
            self.select(getattr(self, 'current', 'start'))
            return
        found = [cid for cid, _t, fn in CHAPTERS if needle in fn().lower()]
        self._fill_tree(set(found))
        self.hits.configure(text=_l('{n} Kapitel', '{n} chapters').format(n=len(found)))
        if found:
            self.select(found[0])

    def _mark_hits(self):
        needle = self.q.get().strip() if hasattr(self, 'q') else ''
        self.txt.tag_remove('hit', '1.0', 'end')
        if not needle:
            return
        first = None
        pos = '1.0'
        while True:
            pos = self.txt.search(needle, pos, nocase=True, stopindex='end')
            if not pos:
                break
            end = f'{pos}+{len(needle)}c'
            self.txt.tag_add('hit', pos, end)
            first = first or pos
            pos = end
        if first:
            self.txt.see(first)
