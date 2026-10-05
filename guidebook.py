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
schaltet um, legt neue Archive ein und laedt gepruefte Mods vom
Community-Server.

## Das Fenster

- **Reiter Installed mods:** alle Archive im Mods-Ordner mit Schalter,
  Groesse und Datum. Doppelklick schaltet um.
- **Reiter My Mods (server):** die Liste von alchemy-fox.de mit
  Pruefsumme; Install laedt, prueft und schaltet ein.
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
installs new archives and downloads verified mods from the community
server.

## The window

- **Installed mods tab:** every archive in the Mods folder with its
  switch, size and date. Double-click toggles.
- **My Mods (server) tab:** the list from alchemy-fox.de with checksums;
  Install downloads, verifies and enables.
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


def ch_server():
    return _l('''# Mods vom Server

Der Reiter "My Mods (server)" liest `mods.json` von alchemy-fox.de: Name,
Version, Groesse, SHA-256 und Download-Adresse jeder gepruefen Mod.

- **not installed:** noch nicht auf diesem PC.
- **update available:** die Datei im Mods-Ordner hat eine andere
  Pruefsumme als die auf dem Server.
- **installed · enabled / disabled:** liegt hier, Schalter wie gezeigt.

Ein Klick auf eine Mod oeffnet rechts die Seitenleiste: Bilder mit Pfeilen
zum Durchschalten, Name, Beschreibung, die Readme zum Aufklappen, Tags und
Credits (alles, was die Mod mitbringt). Mods in der Gruppe "Merged mods"
enthalten mehrere andere Mods in einer Datei; ihre Credits nennen die Urheber.
Gibt es eine Mod in mehreren Sprachen, steht unten neben "Install / update"
eine Sprachauswahl (Vorgabe: die Sprache des Tools). Beim Wechsel der Sprache
wird die andere Fassung ausgeschaltet, weil beide dieselben Spieldateien ersetzen.

"Install / update" laedt nach `Mods\\<Datei>.download`, prueft die
SHA-256, legt bei Ersatz ein `.backup` an und schaltet ein. Stimmt die
Pruefsumme nicht, wird der Download verworfen und nichts geaendert.

Ohne Internet bleibt der Reiter leer; die Statusleiste sagt warum.

## Community (GitHub)

Der dritte Reiter zeigt den Ordner "Two Worlds" des Archivs
`github.com/InsideTwoWorlds/MODs`: jede `.wd` mit Groesse, und wo eine
`.wd.txt` daneben liegt, ihre Beschreibung (erscheint beim Anklicken).
Install laedt die Datei von GitHub und prueft sie gegen den Hash, den
GitHub fuer die Datei fuehrt (git-Blob-SHA1); "update available" heisst,
die Datei im Mods-Ordner hat einen anderen Hash als die auf GitHub.
Doppelklick auf eine Zeile installiert ebenfalls.

Auch hier oeffnet ein Klick die Seitenleiste mit Bild (gibt es eines im Archiv,
sonst das Two-Worlds-Logo), Name und der Beschreibung aus der `.txt`.

Die Mods dort stammen von verschiedenen Autoren; die Beschreibung sagt,
was sie tun und ob sie sich mit anderen vertragen. Lesen, dann einlegen.
''', '''# Mods from the server

The "My Mods (server)" tab reads `mods.json` from alchemy-fox.de: name,
version, size, SHA-256 and download address of every verified mod.

- **not installed:** not on this PC yet.
- **update available:** the file in the Mods folder has a different
  checksum than the one on the server.
- **installed · enabled / disabled:** present here, switch as shown.

Clicking a mod opens the side panel on the right: pictures with arrows to
page through, name, description, the readme (click to unfold), tags and
credits (whatever the mod brings along). Mods in the "Merged mods" group
contain several other mods in one file; their credits name the authors.
If a mod exists in several languages, a language box sits next to
"Install / update" (default: the tool language). Switching the language
turns the other version off, because both replace the same game files.

"Install / update" downloads to `Mods\\<file>.download`, verifies the
SHA-256, keeps a `.backup` when replacing and enables the mod. If the
checksum does not match, the download is discarded and nothing changes.

Without internet the tab stays empty; the status bar says why.

## Community (GitHub)

The third tab shows the folder "Two Worlds" of the archive
`github.com/InsideTwoWorlds/MODs`: every `.wd` with its size, and where a
`.wd.txt` sits next to it, its description (shown when you click the
row). Install downloads the file from GitHub and checks it against the
hash GitHub keeps for the file (git blob SHA-1); "update available" means
the file in the Mods folder has a different hash than the one on GitHub.
Double-clicking a row installs as well.

Here too a click opens the side panel with a picture (if the archive has
one, else the Two Worlds logo), the name and the description from the `.txt`.

The mods there come from different authors; the description says what
they do and whether they get along with others. Read, then install.
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


def ch_merge():
    import modscan
    rows = [(_l('Gruen', 'green'), _l('keine Ueberschneidung innerhalb von Dateien', 'no overlap inside files'),
             _l('laeuft ohne Rueckfrage', 'merges without a question')),
            (_l('Gelb', 'yellow'), _l('Ueberschneidungen in Dateien, gemeinsame Karten oder ganze Dateien',
                                      'overlaps inside files, shared maps or whole files'),
             _l('jede wird gefragt, egal wie viele; "alle an eine Mod" geht mit einem Klick',
                'each one is asked, however many; "give all to one mod" is one click')),
            (_l('Rot', 'red'), _l('beide aendern kompilierte Skripte (.eco)',
                                  'both change compiled scripts (.eco)'),
             _l('Haupt-Mod waehlen, sie behaelt ihre Skripte; alles andere wird trotzdem gefragt',
                'choose a main mod, it keeps its scripts; everything else is still asked'))]
    units = [('Parameters\\TwoWorlds.par', _l('je Feld eines Eintrags; neue Eintraege als Ganzes',
                                              'per field of an entry; new entries as a whole')),
             ('Scripts\\Quests\\TwoWorldsQuests.qtx', _l('je Block (QUEST, NPC, CONTAINER bis END) und je LOCATION-Zeile',
                                                         'per block (QUEST, NPC, CONTAINER up to END) and per LOCATION line')),
             ('Language\\*.lan', _l('je Textschluessel und je Dialogbaum', 'per text key and per dialog tree')),
             ('Levels\\Map_X.lnd + physic\\Map_X.phx', _l('je Kachel, Karte und Physik immer aus derselben Mod',
                                                          'per tile, map and physics always from the same mod')),
             ('Levels\\Map_LevelHeaders.lhc', _l('wird nie uebernommen, sondern neu gebaut', 'never taken over, built new')),
             (_l('alles andere', 'everything else'), _l('ganze Datei', 'whole file'))]
    return _l('''# Mods zusammenfuehren (experimentell)

Zwei Mods, die dieselbe Datei mitbringen, schliessen sich im Spiel aus: eine
gewinnt, die andere Datei laedt nie. Der Reiter **Merge mods** baut aus
mehreren Mods eine neue. Die Quell-Mods werden nur gelesen, nichts wird
ueberschrieben.

## Was eine Mod aendert

Mit der Maus ueber einer Zeile stehen bleiben: welche Dateiarten die Mod
mitbringt, wie viele Par-Felder, Quest-Bloecke, Texte und welche Karten sie
gegenueber dem Spiel aendert. Im Reiter Installed mods faerbt ein Klick auf
eine Mod die anderen Zeilen danach, wie sie zu ihr passen.

## Farben

''', '''# Merging mods (experimental)

Two mods that ship the same file shut each other out in the game: one wins,
the other file never loads. The **Merge mods** tab builds one new mod out of
several. The source mods are only read, nothing is overwritten.

## What a mod changes

Rest the mouse on a row: which kinds of files the mod ships, how many par
fields, quest blocks, texts and which maps it changes against the game. In
the Installed mods tab a click on a mod colours the other rows by how they
fit it.

## Colours

''') + _table([_l('Farbe', 'Colour'), _l('Bedeutung', 'Meaning'), _l('Beim Zusammenfuehren', 'When merging')], rows) \
        + '\n\n' + _source('modscan.py, compare') + _l('''
## Wie fein zusammengefuehrt wird

''', '''
## How fine the merge goes

''') + _table([_l('Datei', 'File'), _l('Einheit', 'Unit')], units) + '\n\n' + _source('modscan.py, merger.py') + _l('''
"Geaendert" heisst: anders als die Datei des Spiels. Aendern zwei Mods
dasselbe Feld auf denselben Wert, ist das keine Ueberschneidung.

## Schritt fuer Schritt

1. **Reiter Merge mods oeffnen.** Die Liste zeigt jede Mod im Mods-Ordner.
   Mit **Add archive from elsewhere...** kommt auch eine `.wd` von anderswo
   dazu, ohne sie vorher einzulegen.
2. **Mindestens zwei Mods anhaken.** Sobald zwei Haken sitzen, faerbt sich
   jede Zeile danach, wie sie zu den angehakten passt, und unter der Liste
   steht das Urteil (gruen, gelb oder rot) mit den ersten Ueberschneidungen.
   Die Reihenfolge der Haken ist die Reihenfolge der Mods.
3. **Namen der neuen Mod eintragen.** Ein Name, den es im Mods-Ordner schon
   gibt, wird abgelehnt - der Merger ueberschreibt nie etwas.
4. **Haupt-Mod waehlen (nur bei Rot noetig).** Rot heisst: beide Mods
   aendern kompilierte Skripte (`.eco`), und die lassen sich nicht mischen.
   Die Haupt-Mod behaelt ihre Skripte. Bei Gelb ist sie freiwillig: dann ist
   sie im Auswahlfenster ueberall vorgewaehlt.
5. **Merge... klicken.** Gibt es Ueberschneidungen ausserhalb von Skripten,
   oeffnet sich das **Auswahlfenster** (siehe unten) - egal ob es fuenf oder
   fuenfhundert sind.
6. **Bestaetigen.** Die neue Mod entsteht im Mods-Ordner und ist
   **ausgeschaltet**. Der Manager fragt, ob er sie einschalten und die
   Quell-Mods ausschalten soll - beides zugleich wuerde alles doppelt laden.
7. **Im Spiel testen**, am besten mit einem neuen Spielstand. Wenn es nicht
   passt: neue Mod ausschalten, Quell-Mods wieder ein - nichts ist verloren.

## Das Auswahlfenster

Links steht jede Stelle, die mehr als eine Mod aendert, nach Art gruppiert:
Par-Felder, Quest-Bloecke, Texte, Karten, ganze Dateien. Rechts steht zur
gewaehlten Stelle:

- je Mod ein Knopf mit ihrem Wert - einen anklicken heisst: dieser Wert kommt
  in die neue Mod,
- darunter **The game itself**: der Wert im Spiel ohne Mods, zum Vergleich,
- bei Par-Feldern der Feldname aus dem SDK, damit klar ist, was sich aendert.

Oben in der Leiste **Give all to:** mit einem Knopf je Mod - ein Klick gibt
ihr jede Stelle, die sie ueberhaupt aendert. Praktisch bei vielen
Ueberschneidungen: erst alles an die Mod geben, die meistens gewinnen soll,
dann nur die Ausnahmen einzeln umstellen. Die Spalte **Winner** zeigt fuer
jede Zeile, wer gerade gewinnt.

**Merge with these choices** baut die Mod, **Cancel** bricht ohne Folgen ab.
Stellen, die nur eine Mod aendert, erscheinen gar nicht: die werden immer
uebernommen.

## Nach dem Zusammenfuehren

- **Bericht:** zu jeder neuen Mod liegt unter `merges` im Datenordner des
  Tools ein Textbericht - welche Datei aus welcher Mod kam, jede Entscheidung
  im Auswahlfenster, jeder uebertragene Marker.
- **Level-Header-Cache:** bringt das Ergebnis Karten mit, baut der Manager
  den Cache fuer die neue Mod selbst - ohne SDK.
- **Rueckgaengig:** die Quell-Mods sind unveraendert. Neue Mod ausschalten
  oder entfernen (sie wandert nach `Mods\_removed\`), Quell-Mods wieder
  einschalten.

## Karten

Bringen zwei Mods dieselbe Kachel, waehlst du je Kachel die Mod. Marker, die
nur die andere Mod auf ihrer Fassung hat (Questmarker!), werden auf die
gewaehlte Karte uebertragen und auf deren Boden gesetzt; Marker des Spiels,
die der gewaehlten Karte fehlen, kommen zurueck. Bei Innenraeumen ohne
lesbare Hoehenkarte bleibt die Hoehe, wie sie war. Der Bericht nennt jeden
uebertragenen Marker.

## Grenzen

Das Tool kann nicht pruefen, ob das Spiel mit dem Ergebnis startet. Zwei
Balance-Mods lassen sich sauber zusammenfuehren und ergeben trotzdem Unsinn.
Kompilierte Skripte (.eco) sind nicht teilbar. Der Bericht jeder
Zusammenfuehrung liegt im Datenordner des Tools unter `merges`.
''', '''
"Changed" means: different from the game's own file. Two mods that set the
same field to the same value do not overlap.

## Step by step

1. **Open the Merge mods tab.** The list shows every mod in the Mods
   folder. **Add archive from elsewhere...** brings in a `.wd` from another
   place without installing it first.
2. **Tick at least two mods.** As soon as two are ticked, every row takes
   the colour of how it fits the ticked ones, and below the list the verdict
   (green, yellow or red) names the first overlaps. The order of the ticks is
   the order of the mods.
3. **Type the name of the new mod.** A name that already exists in the Mods
   folder is refused - the merger never overwrites anything.
4. **Pick a main mod (needed only for red).** Red means: both mods change
   compiled scripts (`.eco`), and those cannot be mixed. The main mod keeps
   its scripts. For yellow it is optional: it is then preselected everywhere
   in the choice window.
5. **Click Merge...** If there are overlaps outside scripts, the **choice
   window** opens (see below) - whether there are five of them or five
   hundred.
6. **Confirm.** The new mod appears in the Mods folder, **switched off**.
   The manager asks whether to enable it and switch the source mods off -
   both at once would load everything twice.
7. **Test in the game**, best with a new save. If it does not fit: switch
   the new mod off and the source mods back on - nothing is lost.

## The choice window

On the left is every place that more than one mod changes, grouped by kind:
par fields, quest blocks, texts, maps, whole files. On the right, for the
selected place:

- one button per mod with its value - clicking one means: this value goes
  into the new mod,
- below it **The game itself**: the value in the game without mods, to
  compare,
- for par fields the field name from the SDK, so it is clear what changes.

At the top **Give all to:** has one button per mod - one click hands it
every place it changes at all. Handy with many overlaps: first give
everything to the mod that should mostly win, then switch only the
exceptions one by one. The **Winner** column shows who wins each row right
now.

**Merge with these choices** builds the mod, **Cancel** stops without any
effect. Places only one mod changes do not appear at all: those are always
taken over.

## After the merge

- **Report:** for every new mod there is a text report under `merges` in the
  tool's data folder - which file came from which mod, every choice made in
  the window, every marker carried over.
- **Level header cache:** if the result brings maps, the manager builds the
  cache for the new mod itself - no SDK needed.
- **Undo:** the source mods are unchanged. Switch the new mod off or remove
  it (it moves to `Mods\_removed\`), switch the source mods back on.

## Maps

When two mods bring the same tile you choose the mod per tile. Markers only
the other mod has on its version (quest markers!) are carried over onto the
chosen map and set onto its ground; game markers the chosen map lacks come
back. For interiors without a readable heightmap the height stays as it
was. The report names every marker carried over.

## Limits

The tool cannot test whether the game starts with the result. Two balance
mods merge cleanly and can still be nonsense in play. Compiled scripts
(.eco) cannot be split. The report of every merge is kept in the tool's
data folder under `merges`.
''')


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


CHAPTERS = (
    ('start', ('Einstieg', 'Getting started'), ch_start),
    ('install', ('Mod einlegen', 'Installing a mod'), ch_install),
    ('switch', ('Ein- und ausschalten', 'Enabling and disabling'), ch_switch),
    ('server', ('Mods vom Server und von GitHub', 'Mods from the server and GitHub'), ch_server),
    ('merge', ('Mods zusammenfuehren', 'Merging mods'), ch_merge),
    ('reference', ('Referenztabellen', 'Reference tables'), ch_reference),
    ('trouble', ('Fehlersuche', 'Troubleshooting'), ch_trouble),
)

HELP_CHAPTER = {'help.installed': 'switch', 'help.server': 'server', 'help.drop': 'install',
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
