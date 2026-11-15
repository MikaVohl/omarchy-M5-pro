#!/usr/bin/env python3
"""Build a pinned ARM64 candidate on an SSH build host; finalize locally.

Only public source, policy, patches and config are uploaded. The target's
baseline DT, firmware, ADT, captures and deployment receipts stay local.
No boot selection or hardware operation is performed.
"""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import stat
import subprocess
import tarfile
import tempfile
import uuid

ROOT = Path(__file__).resolve().parents[1]
ARCHIVE_SHA = '06acab85a9e668010fe9e664d9fc92a15d506a986faab8b9659edd4bbcaaeac2'
CONFIG_SHA = 'e7ff6452a937de895bf351f8539636cbf98998493835b80113da5d5ca4ecba14'
RETURN_FILES = {'Image.gz', 't6050-j716s.dtb', '.config', 'System.map',
                'Module.symvers', 'kernel.release', 'modules.tar.gz', 'compile-receipt.json'}


def run(argv, **kw):
    return subprocess.run(argv, check=True, **kw)


def sha(path):
    with path.open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def alias(value):
    if not re.fullmatch(r'[A-Za-z0-9_.-]+', value):
        raise argparse.ArgumentTypeError('Use a configured SSH host alias, not shell syntax')
    return value


def ssh(jump, command, **kw):
    return run(['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=20', jump,
                shlex.join(command)], **kw)


def extract_return(archive, directory):
    with tarfile.open(archive, 'r:gz') as tar:
        members = tar.getmembers()
        names = [m.name for m in members]
        if set(names) != RETURN_FILES or len(names) != len(RETURN_FILES):
            raise ValueError('Remote return inventory differs')
        if any(not m.isfile() or m.size < 0 for m in members):
            raise ValueError('Remote return contains links, directories or special files')
        if sum(m.size for m in members) > 2 * 1024**3:
            raise ValueError('Remote return exceeds the bounded artifact envelope')
        tar.extractall(directory, members=members, filter='data')


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--jump', type=alias, required=True)
    p.add_argument('--host', type=alias, required=True)
    p.add_argument('--variant', required=True)
    p.add_argument('--jobs', type=int, default=64)
    args = p.parse_args()
    if not 1 <= args.jobs <= 192:
        p.error('jobs must be 1..192; the build host is shared')
    spec = importlib.util.spec_from_file_location('remote_candidate', ROOT / 'outputs/persistent-install/candidate_boot.py')
    candidate = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(candidate)
    if args.variant not in candidate.TOUCHID_VARIANTS:
        p.error('This remote path supports the pinned Touch ID/full candidate config only')
    output = ROOT / ('outputs/kernel-' + args.variant + '-candidate')
    if output.exists() or output.is_symlink():
        p.error('Output already exists; never overwrite a candidate')
    config = ROOT / 'outputs/kernel-bringup-candidate/.config'
    if sha(config) != CONFIG_SHA:
        raise ValueError('Pinned configuration differs')
    worker = ROOT / 'scripts/kernel-build-worker.sh'
    finalize = ROOT / 'scripts/finalize-kernel-candidate.py'
    if not worker.is_file() or not finalize.is_file():
        raise ValueError('Shared worker/finalizer is unavailable')
    pins = candidate.VARIANTS[args.variant]['patches']
    source_files = ['scripts/kernel-build-worker.sh', 'outputs/persistent-install/candidate_boot.py',
                    'outputs/kernel-bringup-candidate/.config', *('patches/' + n for n in pins)]
    for name in source_files:
        st = (ROOT / name).lstat()
        if not stat.S_ISREG(st.st_mode):
            raise ValueError('Public bundle source is not a regular file: ' + name)
    for name, expected in pins.items():
        if sha(ROOT / 'patches' / name) != expected:
            raise ValueError('Patch pin differs: ' + name)
    started = __import__('time').monotonic()
    with tempfile.TemporaryDirectory(prefix='j716s-remote-') as tmp:
        tmp = Path(tmp)
        bundle = tmp / 'input.tar.gz'
        with tarfile.open(bundle, 'w:gz') as tar:
            for name in source_files:
                tar.add(ROOT / name, arcname=name, recursive=False)
        bundle_sha = sha(bundle)
        response = ssh(args.jump, ['bash', '-c', 'umask 077; mktemp -d /tmp/j716s-shuttle.XXXXXXXX'],
                       capture_output=True, text=True)
        shuttle = response.stdout.strip().splitlines()[-1]
        if not re.fullmatch(r'/tmp/j716s-shuttle\.[A-Za-z0-9]+', shuttle):
            raise ValueError('Unexpected jump-host staging path')
        run(['scp', '-o', 'BatchMode=yes', str(bundle), args.jump + ':' + shuttle + '/input.tar.gz'])
        # The second-hop keys remain on the jump host. No key forwarding/copying.
        remote = f'/tmp/omarchy-j716s-builds/runs/{args.variant}-{bundle_sha[:16]}'
        prepare = ('set -e; umask 077; cache=/tmp/omarchy-j716s-builds; '
                   '[ ! -L "$cache" ]; '
                   'if [ -e "$cache" ]; then '
                   '[ -d "$cache" ] && [ "$(stat -c %u "$cache")" = "$(id -u)" ] && '
                   '[ "$(stat -c %a "$cache")" = 700 ]; '
                   'else mkdir -m 700 "$cache"; fi; '
                   'mkdir -p -m 700 "$cache/runs"; mkdir -m 700 ' + shlex.quote(remote))
        ssh(args.jump, ['ssh', '-o', 'BatchMode=yes', args.host, prepare])
        ssh(args.jump, ['scp', '-o', 'BatchMode=yes', shuttle + '/input.tar.gz',
                       args.host + ':' + remote + '/input.tar.gz'])
        container = 'j716s-' + uuid.uuid4().hex[:16]
        patch_names = ' '.join(pins)
        commit = candidate.KERNEL_COMMIT
        archive_name = 'kernel-' + commit + '.tar.gz'
        script = f'''set -euo pipefail
umask 077
root={shlex.quote(remote)}
cache=/tmp/omarchy-j716s-builds
[ ! -L "$cache" ] && [ ! -L "$root" ]
[ "$(stat -c %u "$cache")" = "$(id -u)" ] && [ "$(stat -c %a "$cache")" = 700 ]
exec 9>"$cache/build.lock"
flock -x 9
printf '%s  %s\\n' {bundle_sha} "$root/input.tar.gz" | sha256sum -c -
mkdir -p "$root/input" "$root/out" "$cache/source" "$cache/src" "$cache/build" "$cache/module-stage"
tar -xzf "$root/input.tar.gz" -C "$root/input"
archive="$cache/source/{archive_name}"
if [ ! -f "$archive" ]; then
  curl --fail --location --output "$archive.part" https://codeload.github.com/rolotrealanis98/linux/tar.gz/{commit}
  printf '%s  %s\\n' {ARCHIVE_SHA} "$archive.part" | sha256sum -c -
  mv "$archive.part" "$archive"
fi
printf '%s  %s\\n' {ARCHIVE_SHA} "$archive" | sha256sum -c -
mkdir -p "$root/input/work/sources"
cp --reflink=auto "$archive" "$root/input/work/sources/{archive_name}"
image=j716s-kernel-llvm21-rust199-x86_64
if ! docker image inspect "$image" >/dev/null 2>&1; then image=docker.io/library/fedora:43; fi
docker run --name {container} --platform linux/amd64 --cpus={args.jobs} \\
  -v "$root/input:/exchange:ro" -v "$root/out:/candidate" \\
  -v "$cache/src:/src" -v "$cache/build:/build" -v "$cache/module-stage:/module-stage" \\
  -e ROOT=/exchange -e OUT=/candidate -e SRC=/src/kernel-cross-v2 -e BUILD=/build/kernel -e MODULE_STAGE=/module-stage \\
  -e CONFIG=outputs/kernel-bringup-candidate/.config -e CONFIG_SHA256={CONFIG_SHA} \\
  -e PATCHES={shlex.quote(patch_names)} -e PROBE_VARIANT={shlex.quote(args.variant)} \\
  -e JOBS={args.jobs} -e BUILD_HOST_ARCH=x86_64 -e SYSROOT_RPMS=/opt/arm64-rpms "$image" bash /exchange/scripts/kernel-build-worker.sh
docker commit {container} j716s-kernel-llvm21-rust199-x86_64 >/dev/null
docker rm {container} >/dev/null
tar -czf "$root/return.tar.gz" -C "$root/out" Image.gz t6050-j716s.dtb .config System.map Module.symvers kernel.release modules.tar.gz compile-receipt.json
printf 'REMOTE_BUILD_COMPLETE\\n'
'''
        ssh(args.jump, ['ssh', '-o', 'BatchMode=yes', args.host, 'bash', '-se'], input=script.encode())
        ssh(args.jump, ['scp', '-o', 'BatchMode=yes', args.host + ':' + remote + '/return.tar.gz',
                       shuttle + '/return.tar.gz'])
        archive = tmp / 'return.tar.gz'
        run(['scp', '-o', 'BatchMode=yes', args.jump + ':' + shuttle + '/return.tar.gz', str(archive)])
        incoming = tmp / 'incoming'
        incoming.mkdir()
        extract_return(archive, incoming)
        baseline = tmp / 'baseline.dtb'
        run(['sudo', '-n', 'cp', '/boot/aurora/root/t6050-j716s.dtb', str(baseline)])
        run(['sudo', '-n', 'chown', str(os.getuid()) + ':' + str(os.getgid()), str(baseline)])
        run(['python3', '-B', str(finalize), '--variant', args.variant, '--output', str(incoming),
             '--baseline-dtb', str(baseline), '--worker-receipt', str(incoming / 'compile-receipt.json'),
             '--builder-arch', 'x86_64'])
        shutil.move(str(incoming), output)
        ssh(args.jump, ['rm', '-rf', shuttle])
    print(json.dumps({'output': str(output), 'host': args.host, 'jobs': args.jobs,
                      'total_seconds': round(__import__('time').monotonic() - started, 2),
                      'hardware_deployed': False}))


if __name__ == '__main__':
    main()
