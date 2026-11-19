"""从发行版本生成预设变化清单及当前预设校验值。"""
import hashlib
import argparse
import json
from pathlib import Path
import subprocess
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--from-ref', help='Git commit for an older release; omit to verify the shipped upgrade manifest')
    parser.add_argument('--from-version', default='6.7.3')
    parser.add_argument('--to-version', default='6.7.4')
    parser.add_argument('--history', action='append', default=[], metavar='VERSION=REF',
                        help='include an additional verified release as an upgrade source')
    args = parser.parse_args()
    styles = Path(__file__).resolve().parents[2] / "dev/CSLModernMapRenderer/styles"
    repository = styles.parents[2]
    sys.path.insert(0, str(styles.parent))
    import yaml
    from csmodernmap.style import load_style, canonical_theme_id
    from csmodernmap.theme_library import content_digest
    from csmodernmap.theme_migration import convert_theme
    from csmodernmap.theme_preset_update import release_changes
    manifest = {path.name: {"sha256": hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest(),
                            "content": content_digest(load_style(str(path)).data)}
                for path in sorted(styles.glob("*.yaml")) if path.stem != "metro_map"}
    (styles / "presets.sha256.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    baseline = {path.stem: load_style(str(path)).data for path in sorted(styles.glob("*.yaml"))
                if path.stem != "metro_map"}
    (styles / "presets.current.json").write_text(json.dumps(baseline, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    def git(*arguments):
        return subprocess.run(['git', *arguments], cwd=repository, check=True, capture_output=True).stdout
    prefix = 'dev/CSLModernMapRenderer/styles/'
    def compare(ref):
        revision = git('rev-parse', '--verify', ref + '^{commit}').decode().strip()
        paths = git('ls-tree', '-r', '--name-only', revision, '--', prefix).decode().splitlines()
        changes = {}
        for path in paths:
            source = Path(path)
            identity = canonical_theme_id(source.stem)
            if source.suffix != '.yaml' or identity not in baseline:
                continue
            if identity in changes:
                raise ValueError(f'{ref}: duplicate preset {identity}')
            previous = convert_theme(yaml.safe_load(git('show', revision + ':' + path)), path)
            changes[identity] = release_changes(previous, baseline[identity])
        if not changes:
            raise ValueError(f'{ref}: no historic release presets found')
        return {'sourceRevision': revision, 'themes': changes}

    revision_digest = hashlib.sha256(json.dumps({'version': args.to_version, 'themes': baseline}, sort_keys=True).encode()).hexdigest()
    if not args.from_ref:
        if args.history:
            parser.error('--history requires --from-ref')
        update = json.loads((styles / 'presets.updates.json').read_text(encoding='utf-8'))
        if update['revision'] != revision_digest or update['toVersion'] != args.to_version or update['fromVersion'] != args.from_version:
            raise ValueError('Shipped preset history differs from the current release; regenerate with --from-ref')
        return

    target_revision = git('rev-parse', 'HEAD').decode().strip()
    for identity in baseline:
        path = prefix + identity + '.yaml'
        if yaml.safe_load(git('show', target_revision + ':' + path)) != yaml.safe_load((styles / (identity + '.yaml')).read_bytes()):
            raise ValueError(f'Commit the target release theme first: {path}')
    history = {}
    for argument in args.history:
        version, separator, ref = argument.partition('=')
        if not separator or not version or not ref or version in {*history, args.from_version, args.to_version}:
            parser.error(f'invalid or duplicate upgrade source: {argument}')
        history[version] = compare(ref)
    update = {'schemaVersion': 1, 'fromVersion': args.from_version, 'toVersion': args.to_version,
              'targetRevision': target_revision, **compare(args.from_ref)}
    if history:
        update['history'] = history
    update['revision'] = revision_digest
    (styles / 'presets.updates.json').write_text(json.dumps(update, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


if __name__ == "__main__":
    main()
