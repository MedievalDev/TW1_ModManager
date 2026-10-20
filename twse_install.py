"""TWSE - buglord's Two Worlds Script Extender (CC0) - set up and taken back
by the Mod Manager (2.6.0, Marco 07.10.2026).

* twse.dll comes from the newest GitHub release of buglord's repository. The
  zip must match the sha256 GitHub publishes; a twse.dll we have not checked
  yet is only installed after the player agrees (the window asks). Without a
  connection the twse.dll bundled with the Mod Manager is used.
* TwoWorldsExtended.exe is made by twse_patch.py - the same bytes and checks
  as buglord's patcher (byte-identical to the exe in his release, measured).
  TwoWorlds.exe stays as it is, unless the switch "replace TwoWorlds.exe" is
  on: then TwoWorlds.exe becomes the extended exe (so Steam and GOG Galaxy load
  TWSE too) and the original is kept as TwoWorlds.exe.twse-backup.
* shortcuts on the desktops and in the start menus that point to
  TwoWorlds.exe of this game folder get TwoWorldsExtended.exe as target; a
  new desktop shortcut "Two Worlds (TWSE)" is added for Steam players.
* everything done is written to Mods\\_twse_install.json; "remove" turns it
  back: shortcuts get their old targets, the original exe returns, and
  TwoWorldsExtended.exe and twse.dll move to Mods\\_removed\\TWSE_<date>\\.
  TWSEPlugins stays.

No tkinter in here; the window calls these functions.
"""
import ctypes
import datetime
import hashlib
import io
import json
import os
import shutil
import subprocess
import tempfile
import zipfile

import twse_patch

REPO = 'buglord/Two-Worlds-1-Script-Extender'
API_LATEST = f'https://api.github.com/repos/{REPO}/releases/latest'
PAGE = f'https://github.com/{REPO}'
EXE = 'TwoWorlds.exe'
EXT = 'TwoWorldsExtended.exe'
DLL = 'twse.dll'
BACKUP = 'TwoWorlds.exe.twse-backup'
PLUGINS = 'TWSEPlugins'
RECORD = '_twse_install.json'
SHORTCUT = 'Two Worlds (TWSE).lnk'

# checked by us: twse.dll of release v0.1.0 (also bundled), and the same
# source built locally (two bytes of timestamp differ)
KNOWN_DLLS = {
    '58bce2a8d6b66d3aa3110b591a58f5e311608e752bc572266aac6d5c07e5b2f7': 'v0.1.0',
    '51092696e27427b0c126bb4e855282568ddd61fd06ccdb91eaf13422a7e52da6': 'v0.1.0 (built from source)',
}


class TwseError(Exception):
    pass


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def sha256_file(path):
    with open(path, 'rb') as f:
        return sha256(f.read())


def _now():
    return datetime.datetime.now().strftime('%Y-%m-%d %H:%M')


def _is_i386_dll(data):
    import packages
    return packages.dll_machine(data) == 'i386'


# ------------------------------------------------------------- the release --

def latest_release(http_get):
    """{'tag', 'url', 'name', 'size', 'sha256'} of the newest release zip."""
    d = json.loads(http_get(API_LATEST))
    for a in d.get('assets', []):
        if a.get('name', '').lower().endswith('.zip'):
            dig = a.get('digest') or ''
            return {'tag': d.get('tag_name', ''), 'url': a['browser_download_url'], 'name': a['name'],
                    'size': a.get('size', 0), 'sha256': dig.split(':', 1)[1] if dig.startswith('sha256:') else ''}
    raise TwseError('the newest TWSE release has no zip')


def dll_from_release(zip_bytes, rel):
    """twse.dll out of a downloaded release zip, after the checksum.
    Returns (dll bytes, known: bool)."""
    if not rel.get('sha256'):
        raise TwseError('GitHub gives no checksum for this release - not installed')
    if sha256(zip_bytes) != rel['sha256']:
        raise TwseError('checksum mismatch - download discarded')
    try:
        z = zipfile.ZipFile(io.BytesIO(zip_bytes))
    except zipfile.BadZipFile as e:
        raise TwseError(f'the release zip cannot be read: {e}')
    names = [n for n in z.namelist() if n.replace('\\', '/').lower().split('/')[-1] == DLL]
    names.sort(key=lambda n: (not n.lower().startswith('bin/'), len(n)))
    if not names:
        raise TwseError('no twse.dll in the release zip')
    dll = z.read(names[0])
    if not _is_i386_dll(dll):
        raise TwseError('twse.dll in the release is no 32-bit Windows DLL')
    return dll, sha256(dll) in KNOWN_DLLS


def bundled_dll(res_dir):
    p = os.path.join(res_dir, 'bin', DLL)
    if not os.path.isfile(p):
        p = os.path.join(res_dir, DLL)
    with open(p, 'rb') as f:
        return f.read()


# ---------------------------------------------------------------- record --

def record_path(game):
    return os.path.join(game, 'Mods', RECORD)


def load_record(game):
    try:
        with open(record_path(game), encoding='utf-8') as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def save_record(game, rec):
    p = record_path(game)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    rec['format'] = 1
    with open(p + '.tmp', 'w', encoding='utf-8') as f:
        json.dump(rec, f, indent=2, ensure_ascii=False)
    os.replace(p + '.tmp', p)


# ---------------------------------------------------------------- status --

def _exe_state(path):
    """'stock', 'extended', 'unknown' or 'missing' for an exe file."""
    try:
        with open(path, 'rb') as f:
            z = twse_patch.pruefen(f.read())
    except OSError:
        return 'missing'
    if z['fertig']:
        return 'extended'
    return 'unknown' if z['fehler'] else 'stock'


def status(game):
    """Everything the window shows about TWSE in this game folder."""
    rec = load_record(game)
    ext = _exe_state(os.path.join(game, EXT))
    exe = _exe_state(os.path.join(game, EXE))
    dll_path = os.path.join(game, DLL)
    dll_sha = sha256_file(dll_path) if os.path.isfile(dll_path) else ''
    replaced = bool(rec.get('exe_replaced'))
    return {
        'installed': ext == 'extended' and bool(dll_sha),
        'ext': ext, 'exe': exe, 'dll': bool(dll_sha), 'dll_known': dll_sha in KNOWN_DLLS,
        'dll_label': KNOWN_DLLS.get(dll_sha, ''),
        'exe_replaced': replaced and exe == 'extended',
        # Steam "verify files" or an update put the original back
        'exe_restored_by_launcher': replaced and exe == 'stock',
        'shortcuts': len(rec.get('shortcuts', [])),
        'removed_by_user': bool(rec.get('removed_by_user')),
        'record': rec,
    }


def launch_exe(game):
    """What "Start game" starts: the extended exe when TWSE is there."""
    st = status(game)
    if st['installed'] or st['exe_replaced']:
        return os.path.join(game, EXT if st['ext'] == 'extended' else EXE)
    return os.path.join(game, EXE)


# ------------------------------------------------------------- shortcuts --

def shortcut_folders():
    """[(folder, recursive)] - user and public desktop, user and all-users
    start menu."""
    out = []
    buf = ctypes.create_unicode_buffer(260)
    for csidl, rec in ((0x10, False), (0x19, False), (0x0B, True), (0x16, True)):
        try:
            if ctypes.windll.shell32.SHGetFolderPathW(None, csidl, None, 0, buf) == 0 and buf.value:
                out.append((buf.value, rec))
        except Exception:
            pass
    return out


def user_desktop():
    for folder, _r in shortcut_folders()[:1]:
        return folder
    return os.path.join(os.path.expanduser('~'), 'Desktop')


_PS = r'''
param([string]$inFile, [string]$outFile)
$ErrorActionPreference = 'Stop'
$jobs = Get-Content -LiteralPath $inFile -Raw -Encoding UTF8 | ConvertFrom-Json
$sh = New-Object -ComObject WScript.Shell
$res = @()
foreach ($j in $jobs) {
  $r = [ordered]@{ path = $j.path; ok = $true; error = ''; target = ''; args = ''; workdir = ''; icon = '' }
  try {
    $l = $sh.CreateShortcut($j.path)
    if ($j.op -eq 'write') {
      $l.TargetPath = $j.target
      if ($j.workdir) { $l.WorkingDirectory = $j.workdir }
      if ($j.icon) { $l.IconLocation = $j.icon }
      if ($j.PSObject.Properties.Name -contains 'args') { $l.Arguments = $j.args }
      $l.Save()
      $l = $sh.CreateShortcut($j.path)
    }
    $r.target = $l.TargetPath; $r.args = $l.Arguments; $r.workdir = $l.WorkingDirectory; $r.icon = $l.IconLocation
  } catch { $r.ok = $false; $r.error = $_.Exception.Message }
  $res += [pscustomobject]$r
}
ConvertTo-Json -InputObject @($res) -Depth 3 | Set-Content -LiteralPath $outFile -Encoding UTF8
'''


class Shell:
    """Reads and writes .lnk files through WScript.Shell in one PowerShell
    call per batch (no extra package, no console window)."""

    def run(self, jobs):
        if not jobs:
            return []
        tmp = tempfile.mkdtemp(prefix='mm_lnk_')
        try:
            ps1, fin, fout = (os.path.join(tmp, n) for n in ('lnk.ps1', 'in.json', 'out.json'))
            with open(ps1, 'w', encoding='utf-8-sig') as f:
                f.write(_PS)
            with open(fin, 'w', encoding='utf-8') as f:
                json.dump(jobs, f, ensure_ascii=False)
            r = subprocess.run(['powershell', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
                                '-File', ps1, fin, fout], capture_output=True, text=True,
                               creationflags=0x08000000, timeout=120)
            if not os.path.isfile(fout):
                raise TwseError('shortcuts could not be read: ' + (r.stderr or r.stdout).strip()[:300])
            with open(fout, encoding='utf-8-sig') as f:
                out = json.load(f)
            return out if isinstance(out, list) else [out]
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def read(self, paths):
        return {r['path']: r for r in self.run([{'op': 'read', 'path': p} for p in paths])}

    def write(self, items):
        """items: [{path, target, workdir?, icon?, args?}] -> {path: result}"""
        return {r['path']: r for r in self.run([dict(i, op='write') for i in items])}


def _same(a, b):
    return bool(a) and bool(b) and os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


def find_shortcuts(game, shell, folders=None):
    """{path: info} of .lnk files that start TwoWorlds.exe of this folder."""
    paths = []
    for folder, recursive in (folders if folders is not None else shortcut_folders()):
        if not os.path.isdir(folder):
            continue
        if recursive:
            for root, _d, files in os.walk(folder):
                paths += [os.path.join(root, n) for n in files if n.lower().endswith('.lnk')]
        else:
            paths += [os.path.join(folder, n) for n in os.listdir(folder) if n.lower().endswith('.lnk')]
    if not paths:
        return {}
    exe = os.path.join(game, EXE)
    return {p: r for p, r in shell.read(paths).items() if r.get('ok') and _same(r.get('target'), exe)}


# ---------------------------------------------------------------- install --

def _base_exe(game):
    """The original TwoWorlds.exe bytes: the file itself, or its backup when
    the switch already replaced it."""
    exe = os.path.join(game, EXE)
    if not os.path.isfile(exe):
        raise TwseError('TwoWorlds.exe is missing in the game folder')
    with open(exe, 'rb') as f:
        data = f.read()
    z = twse_patch.pruefen(data)
    if z['fertig']:
        b = os.path.join(game, BACKUP)
        if os.path.isfile(b):
            with open(b, 'rb') as f:
                data = f.read()
            z = twse_patch.pruefen(data)
        else:
            return data, True                       # already extended, no original at hand
    if z['fehler']:
        raise TwseError('TwoWorlds.exe is not a known version (1.7 expected) - nothing changed')
    return data, z['fertig']


def _write(path, data):
    with open(path + '.mmtmp', 'wb') as f:
        f.write(data)
    os.replace(path + '.mmtmp', path)


def install(game, dll, source, replace_exe=False, retarget=True, add_shortcut=True,
            shell=None, folders=None, desktop=None):
    """Sets TWSE up. Returns notes; raises TwseError / OSError (no rights)."""
    if not _is_i386_dll(dll):
        raise TwseError('twse.dll is no 32-bit Windows DLL')
    data, already = _base_exe(game)
    ext_bytes = data if already else twse_patch.anwenden(data)
    if not twse_patch.pruefen(ext_bytes)['fertig']:
        raise TwseError('the patched exe fails the check - nothing changed')
    rec = load_record(game)
    rec.pop('removed_by_user', None)
    rec.pop('removed', None)
    notes = []
    ext = os.path.join(game, EXT)
    if not os.path.isfile(ext) or sha256_file(ext) != sha256(ext_bytes):
        if os.path.isfile(ext) and _exe_state(ext) == 'extended':
            pass                                    # already a working extended exe (maybe whistle-patched): keep
        else:
            _write(ext, ext_bytes)
            rec.setdefault('created', [])
            if EXT not in rec['created']:
                rec['created'].append(EXT)
            notes.append(f'{EXT} created')
    dll_path = os.path.join(game, DLL)
    if not os.path.isfile(dll_path) or sha256_file(dll_path) != sha256(dll):
        if os.path.isfile(dll_path) and not os.path.isfile(dll_path + '.backup'):
            shutil.copy2(dll_path, dll_path + '.backup')
        _write(dll_path, dll)
        rec.setdefault('created', [])
        if DLL not in rec['created']:
            rec['created'].append(DLL)
        notes.append(f'{DLL} ({source})')
    os.makedirs(os.path.join(game, PLUGINS), exist_ok=True)
    rec.update({'installed': _now(), 'source': source, 'dll_sha256': sha256(dll)})
    save_record(game, rec)
    notes += set_replace_exe(game, replace_exe)
    if retarget or add_shortcut:
        shell = shell or Shell()
        if retarget:
            notes += retarget_shortcuts(game, shell, folders)
        if add_shortcut:
            notes += add_twse_shortcut(game, shell, desktop or user_desktop())
    return notes


def set_replace_exe(game, on):
    """The switch: TwoWorlds.exe becomes the extended exe (original kept as
    TwoWorlds.exe.twse-backup), or the original comes back."""
    rec = load_record(game)
    exe = os.path.join(game, EXE)
    backup = os.path.join(game, BACKUP)
    notes = []
    state = _exe_state(exe)
    if on:
        if state == 'extended' and rec.get('exe_replaced'):
            return notes
        if state != 'stock':
            raise TwseError('TwoWorlds.exe is not the original 1.7 - not replaced')
        with open(exe, 'rb') as f:
            orig = f.read()
        new = twse_patch.anwenden(orig)
        if not os.path.isfile(backup) or sha256_file(backup) != sha256(orig):
            _write(backup, orig)
        _write(exe, new)
        rec['exe_replaced'] = {'backup': BACKUP, 'orig_sha256': sha256(orig), 'new_sha256': sha256(new),
                               'when': _now()}
        notes.append('TwoWorlds.exe replaced by the extended exe (original kept as ' + BACKUP + ')')
    else:
        info = rec.get('exe_replaced')
        if not info:
            return notes
        if state == 'extended' and os.path.isfile(backup):
            with open(backup, 'rb') as f:
                orig = f.read()
            if sha256(orig) != info.get('orig_sha256'):
                raise TwseError(BACKUP + ' is not the saved original - TwoWorlds.exe left as it is')
            os.replace(backup, exe)
            notes.append('original TwoWorlds.exe restored')
        elif state == 'stock':
            notes.append('TwoWorlds.exe is already the original (put back by Steam or an update)')
            if os.path.isfile(backup):
                os.remove(backup) if sha256_file(backup) == sha256_file(exe) else None
        rec.pop('exe_replaced', None)
    save_record(game, rec)
    return notes


def retarget_shortcuts(game, shell, folders=None):
    rec = load_record(game)
    found = find_shortcuts(game, shell, folders)
    notes = []
    if not found:
        return notes
    new = os.path.join(game, EXT)
    res = shell.write([{'path': p, 'target': new, 'workdir': game} for p in found])
    done = rec.setdefault('shortcuts', [])
    for p, info in found.items():
        r = res.get(p, {})
        if r.get('ok') and _same(r.get('target'), new):
            done.append({'path': p, 'old_target': info['target'], 'old_workdir': info.get('workdir', ''),
                         'new_target': new})
            notes.append(f'shortcut {os.path.basename(p)} now starts {EXT}')
        else:
            notes.append(f'shortcut {p} not changed: {r.get("error") or "no write access"} (needs administrator rights)')
    save_record(game, rec)
    return notes


def add_twse_shortcut(game, shell, desktop):
    path = os.path.join(desktop, SHORTCUT)
    rec = load_record(game)
    if os.path.exists(path):
        return []
    res = shell.write([{'path': path, 'target': os.path.join(game, EXT), 'workdir': game,
                        'icon': os.path.join(game, EXE) + ',0'}])
    if res.get(path, {}).get('ok'):
        rec.setdefault('created_shortcuts', []).append(path)
        save_record(game, rec)
        return [f'desktop shortcut "{SHORTCUT[:-4]}" added']
    return [f'desktop shortcut not added: {res.get(path, {}).get("error", "?")}']


# --------------------------------------------------------------- remove --

def foreign_files(game):
    """TWSE files in the game folder that the Mod Manager did not create
    (set up by hand, by Extended Settings, or a whistle-patched exe)."""
    created = set(load_record(game).get('created', []))
    return [n for n in (EXT, DLL) if os.path.isfile(os.path.join(game, n)) and n not in created]


def uninstall(game, shell=None, include_foreign=False):
    """Turns everything back. Returns (notes, folder with the moved files).
    Files the Mod Manager did not create stay unless ``include_foreign``."""
    rec = load_record(game)
    notes = []
    shell = shell or Shell()
    lnks = rec.get('shortcuts', [])
    if lnks:
        cur = shell.read([s['path'] for s in lnks if os.path.exists(s['path'])])
        back, kept = [], []
        for s in lnks:
            c = cur.get(s['path'])
            if c and c.get('ok') and _same(c.get('target'), s['new_target']):
                back.append({'path': s['path'], 'target': s['old_target'],
                             'workdir': s.get('old_workdir') or os.path.dirname(s['old_target'])})
            else:
                notes.append(f'shortcut {s["path"]} was changed or removed meanwhile - left as it is')
        res = shell.write(back)
        for b in back:
            if res.get(b['path'], {}).get('ok'):
                notes.append(f'shortcut {os.path.basename(b["path"])} starts TwoWorlds.exe again')
            else:
                kept.append(next(s for s in lnks if s['path'] == b['path']))
                notes.append(f'shortcut {b["path"]} could not be reset: {res.get(b["path"], {}).get("error", "?")}')
        rec['shortcuts'] = kept
    created = rec.get('created_shortcuts', [])
    if created:
        cur = shell.read([p for p in created if os.path.exists(p)])
        for p in created:
            c = cur.get(p)
            if c and c.get('ok') and _same(c.get('target'), os.path.join(game, EXT)):
                os.remove(p)                       # our own shortcut, still pointing where we put it
                notes.append(f'desktop shortcut "{os.path.basename(p)[:-4]}" removed')
        rec['created_shortcuts'] = []
    save_record(game, rec)
    notes += set_replace_exe(game, False)
    rec = load_record(game)
    stamp = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
    keep = os.path.join(game, 'Mods', '_removed', f'TWSE_{stamp}')
    created = set(rec.get('created', []))
    moved = []
    for n in (EXT, DLL, DLL + '.backup'):
        src = os.path.join(game, n)
        mine = n in created or (n == DLL + '.backup' and DLL in created)
        if os.path.isfile(src) and (mine or include_foreign):
            os.makedirs(keep, exist_ok=True)
            shutil.move(src, os.path.join(keep, n))
            moved.append(n)
        elif os.path.isfile(src) and n != DLL + '.backup':
            notes.append(f'{n} was not set up by the Mod Manager - left in place')
    if moved:
        notes.append(f'{", ".join(moved)} moved to Mods\\_removed\\{os.path.basename(keep)}')
    rec.update({'removed_by_user': True, 'removed': _now(), 'created': []})
    save_record(game, rec)
    return notes, keep
