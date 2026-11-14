#!/bin/bash
# Build only inside a fresh ARM64 Linux guest; never alters laptop partitions.
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
PROJECT=$(cd "$HERE/../.." && pwd)
MODE=check
RUNTIME=auto
GUEST="j716s-image-build-$(date +%Y%m%d-%H%M%S)"
OUTPUT="$PROJECT/outputs/rootfs/omarchy-j716s.img.gz"
FIRMWARE="$PROJECT/outputs/firmware/j716s-26C5035r-linux"
SIZE=32
while [ $# -gt 0 ]; do
    case "$1" in
        --check) MODE=check; shift ;;
        --build) MODE=build; shift ;;
        --runtime) RUNTIME=${2:?runtime required}; shift 2 ;;
        --guest) GUEST=${2:?guest name required}; shift 2 ;;
        --output) OUTPUT=${2:?image output required}; shift 2 ;;
        --firmware-dir) FIRMWARE=${2:?firmware directory required}; shift 2 ;;
        --size-gib) SIZE=${2:?image size required}; shift 2 ;;
        -h|--help)
            printf '%s\n' 'usage: bash build-image.sh [--check | --build] [--runtime auto|apple|docker|podman] [--guest NEW_NAME] [--output NEW_IMAGE.img.gz] [--firmware-dir PROJECT_DIR] [--size-gib 32]' \
                'Default check is workspace-only. Build creates an isolated ARM64 guest with CAP_SYS_ADMIN for private chroot mounts.' \
                'Apple Container on macOS is the original build route; Docker/Podman are optional on native Linux ARM64.' \
                'No host sudo, partition/firmware/policy changes or reboot. Failed guests are stopped and retained, never automatically resumed.'
            exit 0 ;;
        *) printf 'Unknown argument: %s\n' "$1" >&2; exit 2 ;;
    esac
done
case "$RUNTIME" in auto|apple|docker|podman) ;; *) printf 'Unknown runtime.\n' >&2; exit 2 ;; esac
for path in build-rootfs.py persistent_payload.py build-mesa-j716s.sh m5-omarchy-j716s.sh graphics/j716s-session-env.py keys/archlinuxarm-builder.asc policies/j716s-policy.py; do
    [ -f "$HERE/$path" ] || { printf 'Required integration artifact missing: %s\n' "$path" >&2; exit 1; }
done
for path in Image.gz t6050-j716s.dtb .config kernel.release modules.tar.gz build-manifest.json; do
    [ -f "$PROJECT/outputs/kernel-candidate/$path" ] || { printf 'Required kernel artifact missing: %s\n' "$path" >&2; exit 1; }
done
for path in loader/build-manifest.json loader/stage1/aura.bin loader/stage2/aura.bin; do
    [ -f "$PROJECT/outputs/boot-candidate/$path" ] || { printf 'Required loader artifact missing: %s\n' "$path" >&2; exit 1; }
done
for path in apple/tpmtfw-j716s.bin apple/centauri/j716s-ftab.bin n1-build-report.json; do
    [ -f "$FIRMWARE/$path" ] || { printf 'Required locally extracted firmware missing: %s\n' "$path" >&2; exit 1; }
done
[ "$MODE" = build ] || { printf 'Workspace prerequisites present. Image build requires a new isolated guest; no guest or disk changed.\n'; exit 0; }
case "$GUEST" in ''|*[!A-Za-z0-9_.-]*) printf 'Unsafe guest name.\n' >&2; exit 2 ;; esac
case "$SIZE" in ''|*[!0-9]*) printf 'Image size must be a positive integer GiB.\n' >&2; exit 2 ;; esac
case "$OUTPUT" in "$PROJECT"/*.img.gz) ;; *) printf 'Output must be a new .img.gz path within this project.\n' >&2; exit 2 ;; esac
case "$FIRMWARE" in "$PROJECT"/*) ;; *) printf 'Firmware must be locally extracted within this project.\n' >&2; exit 2 ;; esac
[ ! -e "$OUTPUT" ] && [ ! -e "${OUTPUT%.gz}" ] && [ ! -e "$(dirname "$OUTPUT")/root-manifest.json" ] || { printf 'Refusing existing image or root manifest; choose a fresh output directory.\n' >&2; exit 1; }
RELATIVE=${OUTPUT#"$PROJECT"/}
FIRMWARE_RELATIVE=${FIRMWARE#"$PROJECT"/}
if [ "$RUNTIME" = auto ]; then
    if [ "$(uname -s)" = Darwin ]; then RUNTIME=apple
    elif command -v docker >/dev/null; then RUNTIME=docker
    else RUNTIME=podman
    fi
fi
if [ "$RUNTIME" = apple ]; then
    [ "$(uname -s)" = Darwin ] && [ "$(uname -m)" = arm64 ] || { printf 'Apple Container requires Apple Silicon macOS.\n' >&2; exit 1; }
    ENGINE=container
    EXTRA=()
else
    [ "$(uname -s)" = Linux ] && [ "$(uname -m)" = aarch64 ] || { printf 'Optional Linux container builds require native Linux ARM64.\n' >&2; exit 1; }
    ENGINE=$RUNTIME
    EXTRA=(--security-opt seccomp=unconfined)
fi
command -v "$ENGINE" >/dev/null || { printf 'Required container runtime missing: %s\n' "$ENGINE" >&2; exit 1; }
"$ENGINE" run --detach --init --name "$GUEST" --cpus 8 --memory 16g --platform linux/arm64 \
    --cap-add CAP_SYS_ADMIN "${EXTRA[@]}" --entrypoint /usr/bin/sleep --volume "$PROJECT:/workspace" docker.io/library/fedora:43 infinity
trap '"$ENGINE" stop "$GUEST" >/dev/null 2>&1 || true' EXIT
"$ENGINE" exec "$GUEST" dnf -y install python3 bsdtar tar coreutils util-linux e2fsprogs libarchive gzip zstd curl gnupg2 findutils shadow-utils cpio file binutils
"$ENGINE" exec "$GUEST" python3 /workspace/outputs/persistent-install/build-rootfs.py \
    --guest-build J716S_OFFLINE_IMAGE --work-dir /var/tmp/j716s-root-build \
    --kernel-dir /workspace/outputs/kernel-candidate \
    --firmware-dir "/workspace/$FIRMWARE_RELATIVE" \
    --mesa-build-script /workspace/outputs/persistent-install/build-mesa-j716s.sh \
    --mesa-workspace /var/tmp/j716s-root-build/mesa-work \
    --out "/workspace/$RELATIVE" --size-gib "$SIZE"
printf 'Image and root-manifest.json built. Guest retained for reproducibility: %s\n' "$GUEST"
