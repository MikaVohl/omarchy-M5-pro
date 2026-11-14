#!/usr/bin/env python3
"""Read-only J716s GPU/scanout discovery. Enumeration is not display qualification."""
import argparse
import ctypes as C
import fcntl
import json
import os
from pathlib import Path
import shlex
import stat
import struct
import sys

PREFIX = Path('/opt/mesa-j716s')
GPU_DRIVERS = {'asahi', 'aurora'}
DCP_DRIVERS = {'apple-drm', 'apple', 'appledrm'}
SIMPLE_DRIVERS = {'simple-framebuffer', 'simpledrm'}


def private_environment():
    icds = sorted((PREFIX / 'share/vulkan/icd.d').glob('asahi_icd*.json'))
    if len(icds) != 1:
        raise RuntimeError('Exactly one private native Asahi Vulkan ICD is required')
    icd_library = Path(json.loads(icds[0].read_text())['ICD']['library_path'])
    if not icd_library.is_absolute() or not icd_library.is_relative_to(PREFIX) or not icd_library.exists():
        raise RuntimeError('Vulkan ICD does not point to a real private-prefix library')
    return {
        'LD_LIBRARY_PATH': str(PREFIX / 'lib'),
        '__EGL_VENDOR_LIBRARY_FILENAMES': str(PREFIX / 'share/glvnd/egl_vendor.d/50_j716s.json'),
        '__GLX_VENDOR_LIBRARY_NAME': 'j716s',
        'LIBGL_DRIVERS_PATH': str(PREFIX / 'lib/dri'),
        'GBM_BACKENDS_PATH': str(PREFIX / 'lib/gbm'),
        'VK_DRIVER_FILES': str(icds[0]),
        'VK_ICD_FILENAMES': str(icds[0]),
        'ASAHI_M5_BRINGUP': '1',
        'MESA_LOADER_DRIVER_OVERRIDE': 'zink',
    }


def bound_driver(node):
    driver = node / 'device/driver'
    return driver.resolve(strict=True).name if driver.exists() else ''


def discover():
    nodes = []; gpu = []; dcp = []; simple = []
    for node in sorted(Path('/sys/class/drm').iterdir()):
        name = node.name
        if not ((name.startswith('card') and name[4:].isdigit()) or
                (name.startswith('renderD') and name[7:].isdigit())):
            continue
        device = Path('/dev/dri') / name
        if not device.exists() or not stat.S_ISCHR(device.stat().st_mode):
            continue
        driver = bound_driver(node)
        entry = {'node': str(device), 'driver': driver, 'device': str((node / 'device').resolve())}
        nodes.append(entry)
        if name.startswith('renderD'):
            if driver in GPU_DRIVERS:
                gpu.append(entry)
            continue
        connectors = []
        for connector in node.parent.glob(name + '-*'):
            status = connector / 'status'; modes = connector / 'modes'
            if status.exists() and status.read_text().strip() == 'connected' and modes.exists() and modes.read_text().strip():
                connectors.append(connector.name[len(name) + 1:])
        entry['connected_connectors'] = connectors
        if connectors:
            if driver in DCP_DRIVERS:
                dcp.append(entry)
            elif driver in SIMPLE_DRIVERS:
                simple.append(entry)
    if len(gpu) != 1:
        raise RuntimeError('Exactly one actual asahi/aurora GPU render node is required; software-only mode is not a GPU session')
    # Aquamarine's split render/KMS pairing must not choose an unrelated render node.
    if len([n for n in nodes if Path(n['node']).name.startswith('renderD')]) != 1:
        raise RuntimeError('Multiple render nodes: refusing ambiguous compositor GPU pairing')
    return nodes, gpu[0], dcp, simple


def kernel_identity(render):
    params = C.create_string_buffer(4096)
    request = struct.pack('IIQQ', 0, 0, C.addressof(params), C.sizeof(params))
    with open(render, 'rb', buffering=0) as fd:
        # _IOW('d', DRM_COMMAND_BASE + DRM_ASAHI_GET_PARAMS, 24-byte request).
        fcntl.ioctl(fd, 0x40186440, request)
    features, generation, variant, revision, chip = struct.unpack_from('QIIII', params.raw)
    if generation != 17 or chip != 0x6050:
        raise RuntimeError(f'Kernel GET_PARAMS is not T6050/G17 M5 Pro: chip={chip:#x}, generation={generation}')
    return {'gpu_generation': generation, 'gpu_variant': chr(variant), 'gpu_revision': revision,
            'chip_id': hex(chip), 'features': hex(features)}


class Application(C.Structure):
    _fields_ = [('sType', C.c_uint32), ('pNext', C.c_void_p), ('pApplicationName', C.c_char_p),
                ('applicationVersion', C.c_uint32), ('pEngineName', C.c_char_p),
                ('engineVersion', C.c_uint32), ('apiVersion', C.c_uint32)]


class InstanceCreate(C.Structure):
    _fields_ = [('sType', C.c_uint32), ('pNext', C.c_void_p), ('flags', C.c_uint32),
                ('pApplicationInfo', C.POINTER(Application)), ('layerCount', C.c_uint32),
                ('layers', C.c_void_p), ('extensionCount', C.c_uint32), ('extensions', C.c_void_p)]


class DriverProperties(C.Structure):
    _fields_ = [('sType', C.c_uint32), ('pNext', C.c_void_p), ('driverID', C.c_uint32),
                ('driverName', C.c_char * 256), ('driverInfo', C.c_char * 256),
                ('conformanceVersion', C.c_ubyte * 4)]


class DrmProperties(C.Structure):
    _fields_ = [('sType', C.c_uint32), ('pNext', C.c_void_p), ('hasPrimary', C.c_uint32),
                ('hasRender', C.c_uint32), ('primaryMajor', C.c_int64), ('primaryMinor', C.c_int64),
                ('renderMajor', C.c_int64), ('renderMinor', C.c_int64)]


class Properties2(C.Structure):
    # Oversized, 8-byte-aligned storage for VkPhysicalDeviceProperties; no limits are interpreted.
    _fields_ = [('sType', C.c_uint32), ('pNext', C.c_void_p), ('properties', C.c_uint64 * 128)]


class Extension(C.Structure):
    _fields_ = [('extensionName', C.c_char * 256), ('specVersion', C.c_uint32)]


def vk_check(result, operation):
    if result != 0:
        raise RuntimeError(f'{operation} failed with VkResult {result}')


def vulkan_identity(render):
    vk = C.CDLL('libvulkan.so.1')
    vk.vkCreateInstance.argtypes = [C.POINTER(InstanceCreate), C.c_void_p, C.POINTER(C.c_void_p)]
    vk.vkEnumeratePhysicalDevices.argtypes = [C.c_void_p, C.POINTER(C.c_uint32), C.c_void_p]
    vk.vkEnumerateDeviceExtensionProperties.argtypes = [C.c_void_p, C.c_char_p, C.POINTER(C.c_uint32), C.c_void_p]
    vk.vkGetPhysicalDeviceProperties2.argtypes = [C.c_void_p, C.POINTER(Properties2)]
    vk.vkDestroyInstance.argtypes = [C.c_void_p, C.c_void_p]
    vk.vkGetPhysicalDeviceProperties2.restype = None
    vk.vkDestroyInstance.restype = None
    app = Application(sType=0, pApplicationName=b'j716s-gpu-preflight', apiVersion=(1 << 22) | (2 << 12))
    create = InstanceCreate(sType=1, pApplicationInfo=C.pointer(app))
    instance = C.c_void_p()
    vk_check(vk.vkCreateInstance(C.byref(create), None, C.byref(instance)), 'vkCreateInstance')
    try:
        count = C.c_uint32()
        vk_check(vk.vkEnumeratePhysicalDevices(instance, C.byref(count), None), 'vkEnumeratePhysicalDevices')
        if count.value == 0:
            raise RuntimeError('Native ICD enumerated no physical GPU')
        devices = (C.c_void_p * count.value)()
        vk_check(vk.vkEnumeratePhysicalDevices(instance, C.byref(count), devices), 'vkEnumeratePhysicalDevices')
        actual = os.stat(render).st_rdev
        matches = []
        for device in devices[:count.value]:
            n = C.c_uint32()
            vk_check(vk.vkEnumerateDeviceExtensionProperties(device, None, C.byref(n), None), 'device extensions')
            extensions = (Extension * n.value)()
            vk_check(vk.vkEnumerateDeviceExtensionProperties(device, None, C.byref(n), extensions), 'device extensions')
            if b'VK_EXT_physical_device_drm' not in {e.extensionName for e in extensions[:n.value]}:
                continue
            drm = DrmProperties(sType=1000353000)
            driver = DriverProperties(sType=1000196000, pNext=C.addressof(drm))
            properties = Properties2(sType=1000059001, pNext=C.addressof(driver))
            vk.vkGetPhysicalDeviceProperties2(device, C.byref(properties))
            raw = bytes(properties.properties)
            api, version, vendor, chip, kind = struct.unpack_from('IIIII', raw)
            name = raw[20:276].split(b'\0', 1)[0].decode('utf-8', 'replace')
            driver_name = driver.driverName.decode('utf-8', 'replace')
            if (kind == 1 and chip == 0x6050 and 'Apple M5 Pro' in name and
                    driver_name in {'Borealis', 'Honeykrisp'} and drm.hasRender and
                    (drm.renderMajor, drm.renderMinor) == (os.major(actual), os.minor(actual))):
                matches.append({'device_name': name, 'device_type': 'integrated GPU', 'device_id': hex(chip),
                                'vendor_id': hex(vendor), 'driver_name': driver_name,
                                'driver_info': driver.driverInfo.decode('utf-8', 'replace'),
                                'render_major': drm.renderMajor, 'render_minor': drm.renderMinor})
        if len(matches) != 1:
            raise RuntimeError('Vulkan did not identify one Apple M5 Pro native renderer on the kernel GPU render node; CPU/llvmpipe is rejected')
        return matches[0]
    finally:
        vk.vkDestroyInstance(instance, None)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--session-env', action='store_true', help='emit shell exports only after actual GPU and KMS preflight')
    parser.add_argument('--allow-dcp', action='store_true', help='explicit per-invocation DCP opt-in (does not qualify the display)')
    args = parser.parse_args()
    if os.environ.get('LD_PRELOAD'):
        raise RuntimeError('LD_PRELOAD is not allowed in a production GPU preflight (no DRM shim)')
    compat = Path('/proc/device-tree/compatible').read_bytes().split(b'\0')
    if b'apple,j716s' not in compat:
        raise RuntimeError('This preflight requires actual apple,j716s device-tree identity')
    environment = private_environment()
    # glibc caches LD_LIBRARY_PATH at process startup: re-exec before any ICD load.
    sanitized = dict(os.environ)
    sanitized.update(environment)
    for key in ('LIBGL_ALWAYS_SOFTWARE', 'GALLIUM_DRIVER', 'MESA_VK_DEVICE_SELECT', 'DRI_PRIME', 'VK_INSTANCE_LAYERS'):
        sanitized.pop(key, None)
    if sanitized != dict(os.environ):
        os.execve(sys.executable, [sys.executable, str(Path(__file__).resolve()), *sys.argv[1:]], sanitized)
    nodes, render, dcp, simple = discover()
    kernel = kernel_identity(render['node'])
    vulkan = vulkan_identity(render['node'])
    optin = Path('/etc/j716s/dcp-session-opt-in')
    allow_dcp = args.allow_dcp or (optin.is_file() and optin.read_text().strip() == 'enabled')
    candidates = dcp if allow_dcp else simple
    if len(candidates) != 1:
        raise RuntimeError('No unambiguous connected ' + ('native DCP' if allow_dcp else 'simpledrm') + ' KMS card; no scanout fallback is permitted')
    kms = candidates[0]
    report = {'schema': 'j716s-gpu-preflight-v1', 'kernel_release': os.uname().release,
              'drm_nodes': nodes, 'gpu_render': render, 'kernel_gpu': kernel, 'vulkan': vulkan,
              'kms': kms, 'scanout_path': 'DCP' if kms['driver'] in DCP_DRIVERS else 'simpledrm CPU-copy scanout',
              'dcp_opt_in': allow_dcp, 'display_qualified': False,
              'evidence_scope': 'real kernel and Vulkan renderer enumeration only; not frame rendering, display stability, CTS or physical DCP qualification'}
    if args.session_env:
        environment['AQ_DRM_DEVICES'] = kms['node']
        for key in ('LIBGL_ALWAYS_SOFTWARE', 'GALLIUM_DRIVER', 'MESA_VK_DEVICE_SELECT', 'DRI_PRIME', 'VK_INSTANCE_LAYERS'):
            print('unset ' + key)
        for key, value in environment.items():
            print('export ' + key + '=' + shlex.quote(value))
        print('# ' + report['scanout_path'] + '; physical display qualification is still separate')
    else:
        print(json.dumps(report, indent=2))


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, RuntimeError, KeyError) as error:
        print('j716s-gpu-preflight: ' + str(error), file=sys.stderr)
        sys.exit(1)
