#!/usr/bin/env python3
"""Validate cloud build artifacts and produce the public update manifest."""
import argparse
import hashlib
import json
from pathlib import Path

from studio_update import select_release, version


def assemble(folder, release, legacy=None):
    folder = Path(folder)
    version(release)
    records = {}
    reports = {}
    for architecture in ('arm64', 'x86_64', 'universal'):
        record_path = folder / f'package-{architecture}.json'
        if not record_path.exists() and architecture == 'universal':
            continue
        record = json.loads(record_path.read_text())
        filename = f'MotionLab-{release}-{architecture}.zip'
        archive = folder / filename
        digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        if (record.get('version') != release or record.get('architecture') != architecture
                or record.get('asset') != filename or record.get('sha256') != digest
                or record.get('bytes') != archive.stat().st_size):
            raise ValueError('Package metadata mismatch: ' + architecture)
        report = json.loads((folder / f'verification-{architecture}.json').read_text())
        if report.get('archiveSha256') != digest or report.get('version') != release:
            raise ValueError('Verification does not match archive: ' + architecture)
        if architecture != 'universal':
            runtime = report.get('runtimes', {}).get(architecture, {})
            if (report.get('hostArchitecture') != architecture or runtime.get('execution') != 'native'
                    or not all(runtime.get(key) is True for key in ('startup', 'restartPreservesData', 'export'))
                    or runtime.get('clips') != 9 or runtime.get('skeletonSources') != 43):
                raise ValueError('Native verification is required: ' + architecture)
        records[architecture] = {key: record[key] for key in ('asset', 'bytes', 'sha256')}
        reports[architecture] = report
    if 'universal' in records:
        bridge = {'version': release, **records['universal']}
    else:
        if not legacy or version(legacy.get('version', '')) < version('0.1.3'):
            raise ValueError('A published legacy upgrade bridge is required')
        bridge = {key: legacy[key] for key in ('version', 'asset', 'bytes', 'sha256')}
        select_release(bridge, '0.1.2', 'arm64')
    manifest = {'schema': 2, **bridge, 'latestVersion': release,
                'platforms': {key: records[key] for key in ('arm64', 'x86_64')}}
    for architecture in manifest['platforms']:
        select_release(manifest, '0.1.2', architecture)
    (folder / 'update.json').write_text(json.dumps(manifest, indent=2) + '\n')
    (folder / 'verification.json').write_text(json.dumps(reports, indent=2) + '\n')
    (folder / 'SHA256SUMS.txt').write_text(''.join(f"{item['sha256']}  {item['asset']}\n" for item in records.values()))
    base = f'https://github.com/rainlotus97/motionlab-releases/releases/download/v{release}'
    lines = [f'# MotionLab {release}', '', 'macOS 12+，按芯片下载：', '']
    for architecture, label in [('arm64', 'Apple Silicon · M 芯片'), ('x86_64', 'Intel')]:
        item = records[architecture]
        lines.append(f"- [{label}]({base}/{item['asset']}) — {item['bytes'] / 1024**2:.1f} MiB")
    lines += ['', '解压后将 MotionLab.app 拖入应用程序。无需安装 Xcode、Python 或 Git；无需 GitHub 登录或密钥。',
              '', '自带工作台、技能、松团一套日常装和 UAL 1 一套骨骼库。升级点击菜单“检查更新…” → “下载并安装”；保留资源库与旧 app 备份。',
              '', '两个单芯片包均由 GitHub Actions 在对应原生 Mac runner 构建并验证启动、重启保留工程、技能安装与导出。测试报告和 SHA-256 随包提供。',
              '', '当前为 ad-hoc 签名，尚未 Apple 公证。首次被系统拦截时，确认来源后在“系统设置 → 隐私与安全性”允许打开。']
    if 'universal' in records:
        lines += ['', 'universal 包仅供 0.1.2 旧版自动更新过渡。手动下载请选择上面的 M 芯片或 Intel 包。']
    (folder / 'release-notes.md').write_text('\n'.join(lines) + '\n')
    return manifest


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('folder', type=Path)
    parser.add_argument('--version', required=True)
    parser.add_argument('--legacy', type=Path)
    args = parser.parse_args()
    assemble(args.folder, args.version, json.loads(args.legacy.read_text()) if args.legacy else None)
