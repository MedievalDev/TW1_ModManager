"""Mod packages: one zip with several files - .wd archives, TWSE plugins and
their inis - installed, switched and removed together.

Rules (Marco, 07.10.2026):

* a zip is checked before anything is written: no absolute paths, no "..",
  size limits, every .wd must read as a Two Worlds archive, every .dll must be
  a 32-bit Windows DLL. A zip with programs or scripts (.exe, .bat, .ps1 ...)
  is refused as a whole.
* where a file goes: a path that starts with Mods/ or TWSEPlugins/ keeps that
  place; otherwise the extension decides - .wd to Mods, .dll and .ini to
  TWSEPlugins. One wrapper folder around everything is ignored. Readmes and
  pictures are shown, not installed; other files are listed as ignored.
* the list of packages lives in <Game>\\Mods\\_packages.json, so it stays with
  the game.
* off: a .wd gets its registry switch 0; plugins and inis move to
  TWSEPlugins\\_disabled\\<package>\\ (TWSE loads only TWSEPlugins\\*.dll in the
  folder itself, see twse_dll.c). Loose plugins that belong to no package move
  to TWSEPlugins\\_disabled\\ directly.
* remove: everything moves to Mods\\_removed\\<package>\\ - nothing is deleted.
* update: an ini the player changed stays; the new one lands next to it as
  <name>.ini.new.

No tkinter in here; the window calls these functions.
"""
import datetime
import hashlib
import json
import os
import shutil
import struct
import tempfile
import zipfile

LIST_NAME = '_packages.json'
DISABLED = '_disabled'
REMOVED = '_removed'
PLUGINS = 'TWSEPlugins'
MODS = 'Mods'
INFO_FILE = 'modinfo.json'

BLOCKED = {'.exe', '.bat', '.cmd', '.com', '.ps1', '.psm1', '.psd1', '.vbs', '.vbe', '.js', '.jse',
           '.wsf', '.wsh', '.msi', '.msp', '.scr', '.pif', '.lnk', '.url', '.reg', '.hta', '.cpl',
           '.jar', '.py', '.pyw', '.pyc', '.sh', '.ahk', '.au3', '.sys', '.drv', '.ocx', '.inf'}
SHOWN = {'.txt', '.md', '.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp', '.pdf', '.rtf'}
BY_EXT = {'.wd': MODS, '.dll': PLUGINS, '.ini': PLUGINS}
# under an explicit TWSEPlugins/ folder a plugin may bring data files too
PLUGIN_DATA = {'.dll', '.ini', '.txt', '.json', '.cfg', '.csv'}

MAX_FILES = 300
MAX_TOTAL = 2 * 1024 ** 3          # unpacked bytes for the whole zip
MAX_RATIO = 400                     # unpacked / packed for one big entry: zip bomb


class PackageError(Exception):
    pass


def now():
    return datetime.datetime.now().strftime('%d.%m.%Y %H:%M')


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(1 << 20), b''):
            h.update(block)
    return h.hexdigest()


def safe_name(name):
    """A package name usable as a folder name."""
    out = ''.join(c if (c.isalnum() or c in ' ._-()') else '_' for c in name).strip(' .')
    return out[:80] or 'package'


def dll_machine(data):
    """'i386', 'x64', other machine id as text, or '' when it is no PE DLL."""
    try:
        if data[:2] != b'MZ':
            return ''
        pe = struct.unpack_from('<I', data, 0x3C)[0]
        if data[pe:pe + 4] != b'PE\0\0':
            return ''
        machine = struct.unpack_from('<H', data, pe + 4)[0]
        chars = struct.unpack_from('<H', data, pe + 22)[0]
        if not chars & 0x2000:                       # IMAGE_FILE_DLL
            return 'exe'
        return {0x14C: 'i386', 0x8664: 'x64'}.get(machine, hex(machine))
    except (struct.error, IndexError):
        return ''


def _parts(name):
    p = name.replace('\\', '/')
    return [x for x in p.split('/') if x not in ('', '.')]


def _bad_path(name):
    p = name.replace('\\', '/')
    if p.startswith('/') or (len(p) > 1 and p[1] == ':'):
        return True
    return any(x == '..' for x in p.split('/'))


# ------------------------------------------------------------------ inspect --

def inspect(zip_path, wd_check=None):
    """What a zip would install. Returns a dict:

    ok, errors (refuse when not empty), name, version, needs_twse,
    files [{src, dest, kind, size, sha256}], shown [names], ignored [names],
    readme (text), zip_sha256.

    ``wd_check(path)`` gets every .wd unpacked to a temp file and returns
    (ok, error text)."""
    plan = {'ok': False, 'errors': [], 'name': '', 'version': '', 'needs_twse': False,
            'files': [], 'shown': [], 'ignored': [], 'readme': '', 'zip_sha256': '',
            'zip': os.path.basename(zip_path)}
    errors = plan['errors']
    try:
        z = zipfile.ZipFile(zip_path)
    except (zipfile.BadZipFile, OSError) as e:
        errors.append(f'not a readable zip archive: {e}')
        return plan
    with z:
        infos = [i for i in z.infolist() if not i.is_dir()]
        if not infos:
            errors.append('the zip is empty')
            return plan
        if len(infos) > MAX_FILES:
            errors.append(f'too many files ({len(infos)}, at most {MAX_FILES})')
            return plan
        total = 0
        for i in infos:
            if _bad_path(i.filename):
                errors.append(f'unsafe path in the zip: {i.filename}')
            total += i.file_size
            if i.file_size > 50 * 1024 ** 2 and i.compress_size and i.file_size / i.compress_size > MAX_RATIO:
                errors.append(f'suspicious compression (zip bomb?): {i.filename}')
            if i.flag_bits & 0x1:
                errors.append(f'encrypted file in the zip: {i.filename}')
        if total > MAX_TOTAL:
            errors.append('the zip unpacks to more than 2 GB')
        blocked = [i.filename for i in infos if os.path.splitext(i.filename)[1].lower() in BLOCKED]
        if blocked:
            errors.append('programs or scripts are not allowed in a mod package: ' + ', '.join(blocked[:8]))
        if errors:
            return plan

        # one wrapper folder around everything ("SpellRework_v2/Mods/x.wd")
        parts = {i.filename: _parts(i.filename) for i in infos}
        firsts = {p[0].lower() for p in parts.values() if len(p) > 1}
        if (len(firsts) == 1 and all(len(p) > 1 for p in parts.values())
                and next(iter(firsts)) not in (MODS.lower(), PLUGINS.lower())):
            parts = {k: v[1:] for k, v in parts.items()}

        info = {}
        for i in infos:
            p = parts[i.filename]
            if len(p) == 1 and p[0].lower() == INFO_FILE:
                try:
                    info = json.loads(z.read(i).decode('utf-8-sig'))
                    if not isinstance(info, dict):
                        raise ValueError('not an object')
                except (ValueError, UnicodeDecodeError) as e:
                    errors.append(f'{INFO_FILE} cannot be read: {e}')
        plan['name'] = str(info.get('name') or os.path.splitext(os.path.basename(zip_path))[0]).strip()
        plan['version'] = str(info.get('version') or '').strip()
        req = info.get('requires') or []
        if isinstance(req, str):
            req = [req]
        plan['needs_twse'] = any(str(r).lower() == 'twse' for r in req)

        seen = {}
        tmp = tempfile.mkdtemp(prefix='mm_pkg_')
        try:
            for i in infos:
                p = parts[i.filename]
                ext = os.path.splitext(p[-1])[1].lower()
                top = p[0].lower() if len(p) > 1 else ''
                if len(p) == 1 and p[0].lower() == INFO_FILE:
                    continue
                dest = None
                if top == MODS.lower():
                    if ext == '.wd' and len(p) == 2:
                        dest = MODS + '/' + p[1]
                    else:
                        plan['ignored'].append(i.filename)
                        continue
                elif top == PLUGINS.lower():
                    if ext in PLUGIN_DATA and p[1].lower() != DISABLED:
                        dest = PLUGINS + '/' + '/'.join(p[1:])
                        if ext == '.dll' and len(p) > 2:
                            errors.append(f'a plugin must sit directly in TWSEPlugins, not in a subfolder: {i.filename}')
                            continue
                    else:
                        plan['ignored'].append(i.filename)
                        continue
                elif ext in BY_EXT:
                    dest = BY_EXT[ext] + '/' + p[-1]
                elif ext in SHOWN:
                    plan['shown'].append(i.filename)
                    if not plan['readme'] and ext in ('.txt', '.md') and 'readme' in p[-1].lower():
                        plan['readme'] = z.read(i)[:20000].decode('utf-8', 'replace')
                    continue
                else:
                    plan['ignored'].append(i.filename)
                    continue
                low = dest.lower()
                if low in seen:
                    errors.append(f'two files want the same place {dest}: {seen[low]} and {i.filename}')
                    continue
                seen[low] = i.filename
                data = z.read(i)
                kind = {'.wd': 'wd', '.dll': 'dll', '.ini': 'ini'}.get(ext, 'data')
                if kind == 'dll':
                    m = dll_machine(data)
                    if m != 'i386':
                        errors.append(f'{p[-1]} is not a 32-bit Windows DLL ({m or "no DLL"}) - Two Worlds cannot load it')
                    plan['needs_twse'] = True
                if kind == 'wd' and wd_check:
                    t = os.path.join(tmp, 'check.wd')
                    with open(t, 'wb') as f:
                        f.write(data)
                    ok, err = wd_check(t)
                    if not ok:
                        errors.append(f'{p[-1]}: {err}')
                plan['files'].append({'src': i.filename, 'dest': dest, 'kind': kind,
                                      'size': len(data), 'sha256': sha256_bytes(data)})
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    if not plan['files'] and not errors:
        errors.append('nothing to install: no .wd, .dll or .ini in the zip')
    plan['zip_sha256'] = sha256_file(zip_path)
    plan['ok'] = not errors
    return plan


# ------------------------------------------------------------------ the list --

class Packages:
    """The installed packages of one game folder and everything you can do
    with them. ``set_switch(name, 0/1)`` and ``drop_switch(name)`` write the
    registry switch of a .wd (injected so tests stay off the registry)."""

    def __init__(self, game_dir, set_switch=None, drop_switch=None):
        self.game = game_dir
        self.mods = os.path.join(game_dir, MODS)
        self.plugins = os.path.join(game_dir, PLUGINS)
        self.path = os.path.join(self.mods, LIST_NAME)
        self.set_switch = set_switch or (lambda name, v: None)
        self.drop_switch = drop_switch or (lambda name: None)
        self.data = {'format': 1, 'packages': {}}
        self.load()

    # ---- storage
    def load(self):
        try:
            with open(self.path, encoding='utf-8') as f:
                d = json.load(f)
            if isinstance(d, dict) and isinstance(d.get('packages'), dict):
                self.data = d
        except (OSError, ValueError):
            pass

    def save(self):
        os.makedirs(self.mods, exist_ok=True)
        tmp = self.path + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(self.data, f, indent=2, ensure_ascii=False)
        os.replace(tmp, self.path)

    @property
    def all(self):
        return self.data['packages']

    def get(self, name):
        return self.all.get(name)

    def owner(self, dest):
        """Package that installed ``dest`` (e.g. 'Mods/x.wd'), or None."""
        low = dest.lower()
        for name, p in self.all.items():
            if any(f['dest'].lower() == low for f in p['files']):
                return name
        return None

    def owners_by_wd(self):
        """{archive name: package} for the .wd files of all packages."""
        out = {}
        for name, p in self.all.items():
            for f in p['files']:
                if f['kind'] == 'wd':
                    out[f['dest'].split('/', 1)[1]] = name
        return out

    # ---- places
    def _abs(self, dest):
        return os.path.join(self.game, *dest.split('/'))

    def _off(self, pkg, dest):
        """Where a plugin file of a switched-off package lies."""
        rel = dest.split('/', 1)[1]
        return os.path.join(self.plugins, DISABLED, safe_name(pkg), *rel.split('/'))

    def where(self, pkg, f):
        """Current path of one package file (on or off)."""
        if f['kind'] != 'wd' and not self.all[pkg].get('enabled', True):
            return self._off(pkg, f['dest'])
        return self._abs(f['dest'])

    # ---- checks before installing
    def conflicts(self, plan):
        """[(dest, text)] for files that are in the way: another package owns
        the place. A file of no package is replaced (kept as .backup)."""
        out = []
        for f in plan['files']:
            o = self.owner(f['dest'])
            if o and o != plan['name']:
                out.append((f['dest'], f'belongs to the package {o}'))
        return out

    def preview(self, plan):
        """[(dest, what happens)] for the install dialog."""
        old = self.get(plan['name'])
        old_files = {f['dest'].lower(): f for f in (old or {}).get('files', [])}
        out = []
        for f in plan['files']:
            target = self.where(plan['name'], f) if old else self._abs(f['dest'])
            if not os.path.exists(target):
                out.append((f['dest'], 'new'))
                continue
            cur = sha256_file(target)
            if cur == f['sha256']:
                out.append((f['dest'], 'unchanged'))
            elif f['kind'] == 'ini':
                prev = old_files.get(f['dest'].lower())
                if prev and prev['sha256'] == cur:
                    out.append((f['dest'], 'updated'))
                else:
                    out.append((f['dest'], 'yours kept, new one as .ini.new'))
            elif f['dest'].lower() in old_files:
                out.append((f['dest'], 'updated'))
            else:
                out.append((f['dest'], 'replaces a file of no package (kept as .backup)'))
        return out

    # ---- install / update
    def install(self, zip_path, plan, enable=True):
        """Writes the files of a checked plan. Returns a list of notes."""
        if not plan.get('ok'):
            raise PackageError('the package did not pass the check')
        clash = self.conflicts(plan)
        if clash:
            raise PackageError('; '.join(f'{d}: {t}' for d, t in clash))
        name = plan['name']
        old = self.get(name)
        old_files = {f['dest'].lower(): f for f in (old or {}).get('files', [])}
        if old and old.get('enabled', True) != enable:
            self.set_enabled(name, enable)          # bring the old files where the new state wants them
            old = self.get(name)
        notes = []
        new_entries = []
        with zipfile.ZipFile(zip_path) as z:
            for f in plan['files']:
                data = z.read(f['src'])
                if sha256_bytes(data) != f['sha256']:
                    raise PackageError(f'{f["src"]} changed since the check')
                entry = {'dest': f['dest'], 'kind': f['kind'], 'sha256': f['sha256']}
                target = (self._off(name, f['dest']) if (f['kind'] != 'wd' and not enable)
                          else self._abs(f['dest']))
                os.makedirs(os.path.dirname(target), exist_ok=True)
                if os.path.exists(target):
                    cur = sha256_file(target)
                    prev = old_files.get(f['dest'].lower())
                    if cur == f['sha256']:
                        pass
                    elif f['kind'] == 'ini' and not (prev and prev['sha256'] == cur):
                        _write(target + '.new', data)
                        notes.append(f'{f["dest"]}: your changed ini stays, the new one is {os.path.basename(target)}.new')
                        entry['kept'] = True         # sha stays the shipped one: the next update keeps it too
                    else:
                        if not prev:
                            backup = target + '.backup'
                            if not os.path.exists(backup):
                                shutil.copy2(target, backup)
                                notes.append(f'{f["dest"]}: the old file is kept as .backup')
                        _write(target, data)
                else:
                    _write(target, data)
                if f['kind'] == 'wd':
                    self.set_switch(f['dest'].split('/', 1)[1], 1 if enable else 0)
                old_files.pop(f['dest'].lower(), None)
                new_entries.append(entry)
        # files the new version no longer ships: out of the way, not deleted
        leftover = list(old_files.values())
        if leftover:
            keep = self._removed_dir(name)
            for f in leftover:
                src = self.where(name, f) if old else self._abs(f['dest'])
                if os.path.exists(src):
                    dst = os.path.join(keep, *f['dest'].split('/'))
                    os.makedirs(os.path.dirname(dst), exist_ok=True)
                    shutil.move(src, dst)
                if f['kind'] == 'wd':
                    self.set_switch(f['dest'].split('/', 1)[1], 0)
            notes.append(f'{len(leftover)} file(s) of the old version moved to {os.path.relpath(keep, self.game)}')
        p = self.all.setdefault(name, {})
        p.update({'name': name, 'version': plan['version'], 'source': plan['zip'],
                  'zip_sha256': plan['zip_sha256'], 'installed': now(), 'enabled': enable,
                  'needs_twse': plan['needs_twse'], 'files': new_entries})
        self.save()
        return notes

    # ---- switch
    def set_enabled(self, name, on):
        p = self.all[name]
        was = p.get('enabled', True)
        for f in p['files']:
            if f['kind'] == 'wd':
                self.set_switch(f['dest'].split('/', 1)[1], 1 if on else 0)
                continue
            if was == on:
                continue
            a, b = self._off(name, f['dest']), self._abs(f['dest'])
            src, dst = (a, b) if on else (b, a)
            if not os.path.exists(src):
                continue
            if os.path.exists(dst):
                raise PackageError(f'{os.path.relpath(dst, self.game)} is in the way')
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.move(src, dst)
        p['enabled'] = on
        _prune(os.path.join(self.plugins, DISABLED))
        self.save()

    # ---- remove
    def _removed_dir(self, name):
        base = os.path.join(self.mods, REMOVED, safe_name(name))
        out, i = base, 2
        while os.path.exists(out):
            out = f'{base}_{i}'
            i += 1
        return out

    def remove(self, name):
        """Moves every file of the package to Mods\\_removed\\<package>\\ and
        forgets the package. Returns the folder."""
        p = self.all[name]
        keep = self._removed_dir(name)
        for f in p['files']:
            src = self.where(name, f)
            if os.path.exists(src):
                dst = os.path.join(keep, *f['dest'].split('/'))
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.move(src, dst)
            if f['kind'] == 'wd':
                self.set_switch(f['dest'].split('/', 1)[1], 0)
        os.makedirs(keep, exist_ok=True)
        with open(os.path.join(keep, 'package.json'), 'w', encoding='utf-8') as fp:
            json.dump(p, fp, indent=2, ensure_ascii=False)
        del self.all[name]
        _prune(os.path.join(self.plugins, DISABLED))
        self.save()
        return keep

    # ---- state for the window
    def state(self, name):
        """'on', 'off' or 'incomplete' (a file is missing)."""
        p = self.all[name]
        for f in p['files']:
            if not os.path.exists(self.where(name, f)):
                return 'incomplete'
        return 'on' if p.get('enabled', True) else 'off'

    # ---- files Vortex deployed: shown, never switched or moved here
    def vortex_files(self):
        """Lower-case paths relative to the game folder ('mods/x.wd',
        'twseplugins/x.dll') that Vortex lists in its deployment manifests
        (vortex.deployment.json in Mods, vortex.deployment.<type>.json in
        the game folder for the Two Worlds game-root type)."""
        out = set()
        for folder, prefix in ((self.mods, MODS + '/'), (self.game, '')):
            try:
                names = [n for n in os.listdir(folder)
                         if n.lower().startswith('vortex.deployment') and n.lower().endswith('.json')]
            except OSError:
                continue
            for n in names:
                if folder == self.game and n.lower() == 'vortex.deployment.json':
                    continue                      # would be a type with the game folder as root only when named
                try:
                    with open(os.path.join(folder, n), encoding='utf-8') as f:
                        d = json.load(f)
                except (OSError, ValueError):
                    continue
                for e in d.get('files', []) if isinstance(d, dict) else []:
                    rel = str(e.get('relPath', '')).replace('\\', '/')
                    if rel:
                        out.add((prefix + rel).lower())
        return out

    # ---- loose plugins (no package): TWExtended.dll, TWTrainer.dll ...
    def loose_plugins(self):
        """[(file name, on)] for DLLs in TWSEPlugins (on) and in
        TWSEPlugins\\_disabled\\ (off) that belong to no package."""
        out = []
        for folder, on in ((self.plugins, True), (os.path.join(self.plugins, DISABLED), False)):
            try:
                names = sorted(os.listdir(folder), key=str.lower)
            except OSError:
                continue
            for n in names:
                if n.lower().endswith('.dll') and os.path.isfile(os.path.join(folder, n)):
                    if not self.owner(PLUGINS + '/' + n):
                        out.append((n, on))
        return out

    def set_loose(self, dll, on):
        """Moves a loose plugin (and an ini of the same name) in or out."""
        off_dir = os.path.join(self.plugins, DISABLED)
        stem = os.path.splitext(dll)[0]
        moved = 0
        for n in (dll, stem + '.ini'):
            a, b = os.path.join(off_dir, n), os.path.join(self.plugins, n)
            src, dst = (a, b) if on else (b, a)
            if os.path.exists(src):
                if os.path.exists(dst):
                    raise PackageError(f'{os.path.relpath(dst, self.game)} is in the way')
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.move(src, dst)
                moved += 1
        if not moved:
            raise PackageError(f'{dll} not found')
        _prune(off_dir)


def _write(path, data):
    tmp = path + '.mmtmp'
    with open(tmp, 'wb') as f:
        f.write(data)
    os.replace(tmp, path)


def _prune(folder):
    """Removes empty folders below and including ``folder``."""
    if not os.path.isdir(folder):
        return
    for root, dirs, files in os.walk(folder, topdown=False):
        if not dirs and not files:
            try:
                os.rmdir(root)
            except OSError:
                pass
        else:
            try:
                if not os.listdir(root):
                    os.rmdir(root)
            except OSError:
                pass
