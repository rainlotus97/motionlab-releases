#!/usr/bin/env python3
"""Anonymous updates from the public MotionLab distribution repository."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import plistlib
import platform
import re
import shutil
import ssl
import stat
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile

REPOSITORY = 'rainlotus97/motionlab-releases'
API = 'https://github.com/' + REPOSITORY + '/releases'
BUNDLE_ID = 'local.character-motion.studio'
MAX_DOWNLOAD = 1024 * 1024 * 1024


def version(value):
    if not isinstance(value, str) or not re.fullmatch(r'v?\d+\.\d+\.\d+', value):
        raise ValueError('Release version must be major.minor.patch')
    return tuple(int(part) for part in value.removeprefix('v').split('.'))


class SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        parsed = urllib.parse.urlparse(newurl)
        if parsed.scheme != 'https' or parsed.hostname not in {
                'api.github.com', 'github.com', 'release-assets.githubusercontent.com', 'objects.githubusercontent.com'}:
            raise ValueError('Update redirected outside GitHub asset hosts')
        result = super().redirect_request(req, fp, code, msg, headers, newurl)
        if parsed.hostname != 'api.github.com':
            result.remove_header('Authorization')
        return result


def request(url, binary=False):
    if not url.startswith(API + '/'):
        raise ValueError('Unexpected update endpoint')
    context = ssl.create_default_context(cafile='/etc/ssl/cert.pem')
    opener = urllib.request.build_opener(SafeRedirect(), urllib.request.HTTPSHandler(context=context))
    headers = {'Accept': 'application/octet-stream' if binary else 'application/json',
               'User-Agent': 'MotionLab-Updater'}
    return opener.open(urllib.request.Request(url, headers=headers), timeout=60)


def hardware_architecture():
    # A Rosetta-launched Intel app on an M-series Mac should migrate to the ARM build.
    if sys.platform == 'darwin':
        result = subprocess.run(['/usr/sbin/sysctl', '-n', 'hw.optional.arm64'], capture_output=True, text=True)
        if result.returncode == 0 and result.stdout.strip() == '1':
            return 'arm64'
    architecture = platform.machine()
    if architecture not in ('arm64', 'x86_64'):
        raise ValueError('Unsupported Mac architecture')
    return architecture


def select_release(release, current, architecture):
    if 'platforms' in release:
        latest = release['latestVersion']
        record = release['platforms'].get(architecture)
        if not isinstance(record, dict):
            raise ValueError('当前芯片暂无可用安装包，请稍后重试。')
        package_architecture = architecture
    else:
        latest = release['version']
        record = release
        package_architecture = 'universal'
    newer = version(latest) > version(current)
    filename = f'MotionLab-{latest}-{package_architecture}.zip'
    digest, size = record.get('sha256', ''), record.get('bytes')
    if (record.get('asset') != filename or not re.fullmatch(r'[a-f0-9]{64}', digest)
            or type(size) is not int or not 0 < size <= MAX_DOWNLOAD):
        raise ValueError('公开版本清单不完整，请稍后重试。')
    asset = {'name': filename, 'url': API + f'/download/v{latest}/{filename}',
             'size': size, 'digest': 'sha256:' + digest}
    return {'version': latest, 'available': newer, 'architecture': architecture, 'asset': asset,
            'releaseUrl': f'https://github.com/{REPOSITORY}/releases/tag/v{latest}'}


def check(current):
    with request(API + '/latest/download/update.json') as response:
        release = json.loads(response.read(32768))
    return select_release(release, current, hardware_architecture())


def validate_zip(path):
    total = 0
    with zipfile.ZipFile(path) as archive:
        for item in archive.infolist():
            name = Path(item.filename)
            if name.is_absolute() or '..' in name.parts or not name.parts or name.parts[0] not in ('MotionLab.app', '__MACOSX'):
                raise ValueError('Unsafe update archive path')
            total += item.file_size
            if total > 3 * MAX_DOWNLOAD:
                raise ValueError('Expanded update archive too large')
            if stat.S_ISLNK(item.external_attr >> 16):
                link = Path(archive.read(item).decode('utf-8'))
                resolved = (Path('/payload') / name.parent / link).resolve()
                if link.is_absolute() or not resolved.is_relative_to('/payload/MotionLab.app'):
                    raise ValueError('Unsafe update archive symlink')


def app_info(app):
    data = plistlib.loads((Path(app) / 'Contents/Info.plist').read_bytes())
    if data.get('CFBundleIdentifier') != BUNDLE_ID or not data.get('StudioBundled'):
        raise ValueError('仅支持更新 MotionLab Release 应用，请先下载并安装 Release 包。')
    version(data['CFBundleShortVersionString'])
    return data


def prepare(app, expected, owner_pid):
    app = Path(app).resolve()
    current = app_info(app)['CFBundleShortVersionString']
    if '/AppTranslocation/' in str(app) or str(app).startswith('/Volumes/') or not os.access(app.parent, os.W_OK):
        raise ValueError('请先将 MotionLab.app 放到可写的 Applications 文件夹，再安装更新。')
    release = check(current)
    if not release['available'] or release['version'] != expected:
        raise ValueError('发布版本已变化，请重新检查更新。')
    asset = release['asset']
    expected_names = [f'MotionLab-{expected}-{item}.zip' for item in ('universal', hardware_architecture())]
    if not 0 < asset['size'] <= MAX_DOWNLOAD or asset['url'] not in [API + f'/download/v{expected}/' + name for name in expected_names]:
        raise ValueError('Invalid release asset')
    stage = Path(tempfile.mkdtemp(prefix='.motionlab-update-', dir=app.parent))
    try:
        archive = stage / 'download.zip'
        count = 0
        digest = hashlib.sha256()
        with request(asset['url'], binary=True) as response, archive.open('wb') as output:
            while chunk := response.read(1024 * 1024):
                count += len(chunk)
                if count > asset['size'] or count > MAX_DOWNLOAD:
                    raise ValueError('Update download exceeds announced size')
                digest.update(chunk)
                output.write(chunk)
        if count != asset['size'] or 'sha256:' + digest.hexdigest() != asset['digest']:
            raise ValueError('更新包校验失败，当前应用保持不变。')
        validate_zip(archive)
        subprocess.run(['/usr/bin/ditto', '-x', '-k', str(archive), str(stage)], check=True, capture_output=True)
        candidate = stage / 'MotionLab.app'
        candidate_info = app_info(candidate)
        if hardware_architecture() not in candidate_info.get('StudioArchitectures', []):
            raise ValueError('Downloaded app does not support this Mac')
        if candidate_info['CFBundleShortVersionString'] != expected:
            raise ValueError('Release metadata does not match application version')
        subprocess.run(['/usr/bin/codesign', '--verify', '--deep', '--strict', str(candidate)], check=True, capture_output=True)
        archive.unlink()
        job = {'app': str(app), 'candidate': str(candidate), 'ownerPid': owner_pid, 'from': current, 'to': expected}
        (stage / 'update.json').write_text(json.dumps(job))
        return {'stage': str(stage), 'version': expected}
    except BaseException:
        shutil.rmtree(stage)
        raise


def replace_app(candidate, app, opener=None):
    candidate, app = Path(candidate), Path(app)
    info = app_info(app)
    app_info(candidate)
    backup = app.with_name(f'MotionLab-backup-{info["CFBundleShortVersionString"]}-{time.time_ns()}.app')
    app.rename(backup)
    try:
        candidate.rename(app)
        if opener:
            opener(app)
    except BaseException:
        if app.exists():
            app.rename(candidate)
        backup.rename(app)
        if opener:
            try:
                opener(app)
            except Exception:
                pass
        raise
    return backup


def apply(stage):
    stage = Path(stage).resolve()
    job = json.loads((stage / 'update.json').read_text())
    app, candidate = Path(job['app']), Path(job['candidate'])
    if candidate != stage / 'MotionLab.app' or app.parent != stage.parent or not stage.name.startswith('.motionlab-update-'):
        raise ValueError('Invalid update transaction')
    for _ in range(120):
        try:
            os.kill(job['ownerPid'], 0)
        except ProcessLookupError:
            break
        time.sleep(.5)
    else:
        raise ValueError('应用尚未退出，更新已取消，原应用保持不变。')
    if app_info(app)['CFBundleShortVersionString'] != job['from']:
        raise ValueError('Installed application changed while waiting')
    # All imports are loaded before moving the running helper's bundled interpreter.
    backup = replace_app(candidate, app, lambda path: subprocess.run(['/usr/bin/open', '-n', str(path)], check=True))
    (stage / 'result.json').write_text(json.dumps({'updated': job['to'], 'backup': str(backup)}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('check', 'prepare', 'apply'))
    parser.add_argument('--app', type=Path)
    parser.add_argument('--version')
    parser.add_argument('--owner-pid', type=int)
    parser.add_argument('--stage', type=Path)
    args = parser.parse_args()
    try:
        if args.action == 'apply':
            apply(args.stage)
            return
        if args.action == 'check':
            result = check(args.version)
            result.pop('asset', None)
        else:
            result = prepare(args.app, args.version, args.owner_pid)
        print(json.dumps(result, ensure_ascii=False))
    except urllib.error.HTTPError as error:
        message = '暂无可用的正式 Release，请稍后重试。' if error.code == 404 else f'下载服务返回 HTTP {error.code}，请稍后重试；也可从公开 Releases 页面手动下载。'
        print(json.dumps({'error': message}, ensure_ascii=False))
        sys.exit(1)
    except Exception as error:
        # Do not print request headers, tokens or signed asset redirect URLs.
        message = str(error) if isinstance(error, ValueError) else '更新失败，请检查网络、可用磁盘空间及应用目录权限；当前素材保持不变。'
        print(json.dumps({'error': message}, ensure_ascii=False))
        sys.exit(1)


if __name__ == '__main__':
    main()
