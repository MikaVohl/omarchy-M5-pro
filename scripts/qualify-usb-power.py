#!/usr/bin/env python3
"""Read-only J716s USB/PD/DP/Thunderbolt, charging and suspend-readiness evidence.

Default: JSON to stdout, no files or hardware state changed. --output explicitly
creates one private report; existing files are refused. --before compares an
existing snapshot from the same unit and boot. Cases record operator intent,
not physical qualification. Never changes roles, authorizes TB devices, mounts
debugfs, changes charging policy, suspends, reboots, or alters clock/PD flags.

Examples (physical attachment is a separate, attended action):
  python scripts/qualify-usb-power.py --output /tmp/j716s-before.json
  python scripts/qualify-usb-power.py --case usb3 --port left-front \
    --orientation 1 --equipment 'known USB3 device/cable' \
    --before /tmp/j716s-before.json --output /tmp/j716s-usb3.json
  python scripts/qualify-usb-power.py --case suspend-readiness
  python scripts/qualify-usb-power.py --plan
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess

SCHEMA = 1
KIND = "j716s-usb-power"
PORTS = {
    "left-front": {"atc": "ATC1", "label": "USB-C Left-front", "usb": "2aa280000.usb", "phy": "2ab000000.phy", "acio": None},
    "right": {"atc": "ATC2", "label": "USB-C Right", "usb": "382280000.usb", "phy": "383000000.phy", "acio": "381ac0000.acio"},
}
CASES = ("baseline", "usb2", "usb3", "charging", "dp", "thunderbolt", "disconnect", "suspend-readiness")
TYPEC_ATTRS = ("data_role", "power_role", "power_operation_mode", "preferred_role", "vconn_source", "usb_power_delivery_revision", "usb_typec_revision", "contaminant")
PARTNER_ATTRS = ("accessory_mode", "supports_usb_power_delivery", "usb_power_delivery_revision", "number_of_alternate_modes")
MODE_ATTRS = ("svid", "vdo", "mode", "active")
USB_ATTRS = ("idVendor", "idProduct", "manufacturer", "product", "speed", "authorized", "bConfigurationValue", "bNumInterfaces")
POWER_ATTRS = ("type", "online", "status", "health", "present", "usb_type", "voltage_now", "current_now", "power_now", "input_power_limit", "capacity", "energy_now", "charge_now", "temp", "charge_control_start_threshold", "charge_control_end_threshold", "charge_behaviour")


class Reader:
    def __init__(self, root):
        self.root = root
        self.errors = []

    def path(self, name):
        return self.root / name.lstrip("/")

    def read(self, path, optional=False):
        try:
            return path.read_bytes().decode("utf-8").rstrip("\0\n")
        except OSError as error:
            if not (optional and error.errno == 2):
                self.errors.append({"path": str(path), "errno": error.errno, "error": error.strerror})
            return None

    def attrs(self, directory, names):
        return {name: self.read(directory / name) for name in names if (directory / name).exists()}

    def entries(self, name, pattern="*"):
        directory = self.path(name)
        try:
            return sorted(directory.glob(pattern)) if directory.is_dir() else []
        except OSError as error:
            self.errors.append({"path": str(directory), "errno": error.errno, "error": error.strerror})
            return []


def timestamp():
    return datetime.now(timezone.utc).isoformat()


def command(args):
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=20, check=False)
        return {"returncode": result.returncode, "stdout": result.stdout, "stderr": result.stderr}
    except (OSError, subprocess.SubprocessError) as error:
        return {"error": str(error)}


def device(reader, name):
    path = reader.path("/sys/bus/platform/devices") / name
    if not path.exists():
        return {"present": False}
    return {"present": True, "path": str(path.resolve()),
            "driver": (path / "driver").resolve().name if (path / "driver").exists() else None,
            "power": reader.attrs(path / "power", ("runtime_status", "control", "runtime_active_time", "runtime_suspended_time", "wakeup"))}


def descendants(path, ancestor):
    return bool(ancestor) and Path(path).is_relative_to(Path(ancestor))


def collect(root, journal=True):
    reader = Reader(root)
    result = {"schema": SCHEMA, "kind": KIND, "started_utc": timestamp(), "read_only": True,
              "snapshot_atomic": False, "physically_qualified": False}
    dt = reader.path("/sys/firmware/devicetree/base")
    compatible = reader.read(dt / "compatible")
    if compatible is None or "apple,j716s" not in compatible.split("\0"):
        raise ValueError("Read-only evidence helper targets apple,j716s only")
    serial = reader.read(dt / "serial-number")
    result["identity"] = {"compatible": compatible.split("\0"), "kernel": platform.release() if root == Path("/") else "fixture",
                          "boot_id": reader.read(reader.path("/proc/sys/kernel/random/boot_id")),
                          "unit_serial_sha256": hashlib.sha256(serial.strip().encode("ascii")).hexdigest() if serial else None,
                          "cmdline": reader.read(reader.path("/proc/cmdline")),
                          "uptime": reader.read(reader.path("/proc/uptime"))}
    result["ports"] = {}
    for name, mapping in PORTS.items():
        result["ports"][name] = {"atc": mapping["atc"], "typec": None,
                                 "controller": device(reader, mapping["usb"]),
                                 "phy": device(reader, mapping["phy"]),
                                 "acio": device(reader, mapping["acio"]) if mapping["acio"] else None}
    for port in reader.entries("/sys/class/typec", "port*"):
        if not port.name.removeprefix("port").isdigit():
            continue
        label = reader.read(port / "device/of_node/connector/label", optional=True)
        matches = [name for name, mapping in PORTS.items() if label == mapping["label"]]
        if len(matches) != 1:
            reader.errors.append({"path": str(port), "error": f"Unmapped connector label {label!r}; never infer from port index"})
            continue
        target = result["ports"][matches[0]]
        if target["typec"] is not None:
            raise ValueError(f"Duplicate connector label: {label}")
        partner = port.parent / (port.name + "-partner")
        target["typec"] = {"name": port.name, "label": label, "path": str(port.resolve()),
                            "attributes": reader.attrs(port, TYPEC_ATTRS),
                            "partner_present": partner.exists(),
                            "partner": reader.attrs(partner, PARTNER_ATTRS) if partner.exists() else {},
                            "local_altmodes": {}, "partner_altmodes": {}}
        for prefix, directory in (("local", port), ("partner", partner)):
            for mode in sorted(directory.glob("*.*")) if directory.exists() else []:
                if (mode / "svid").exists():
                    target["typec"][prefix + "_altmodes"][mode.name] = reader.attrs(mode, MODE_ATTRS)
    result["usb_devices"] = {}
    for node in reader.entries("/sys/bus/usb/devices"):
        if ":" in node.name:
            continue  # Interfaces duplicate their parent device; do not count them as enumeration.
        resolved = str(node.resolve())
        port_names = [name for name, port in result["ports"].items() if descendants(resolved, port["controller"].get("path"))]
        result["usb_devices"][node.name] = {"path": resolved, "port": port_names[0] if len(port_names) == 1 else None,
                                             "root_hub": node.name.startswith("usb"),
                                             "attributes": reader.attrs(node, USB_ATTRS)}
    result["thunderbolt_devices"] = {}
    for node in reader.entries("/sys/bus/thunderbolt/devices"):
        resolved = str(node.resolve())
        port_names = [name for name, port in result["ports"].items() if port["acio"] and descendants(resolved, port["acio"].get("path"))]
        result["thunderbolt_devices"][node.name] = {"path": resolved, "port": port_names[0] if len(port_names) == 1 else None,
                                                     "attributes": reader.attrs(node, ("authorized", "device", "device_name", "vendor", "vendor_name", "generation", "security", "rx_speed", "tx_speed"))}
    result["drm_connectors"] = {}
    for node in reader.entries("/sys/class/drm", "card*-*"):
        if (node / "status").exists():
            result["drm_connectors"][node.name] = reader.attrs(node, ("status", "enabled", "modes"))
    result["power_supplies"] = {}
    for node in reader.entries("/sys/class/power_supply"):
        result["power_supplies"][node.name] = {"path": str(node.resolve()), "attributes": reader.attrs(node, POWER_ATTRS)}
    result["sensors"] = {}
    for node in reader.entries("/sys/class/hwmon"):
        name = reader.read(node / "name")
        if name not in ("macsmc_hwmon", "macsmc_battery", "macsmc_ac", "nvme"):
            continue
        readings = {}
        for label in sorted(node.glob("temp*_label")):
            key = reader.read(label)
            readings[key or label.name] = reader.read(label.with_name(label.name.replace("_label", "_input")))
        result["sensors"][node.name] = {"name": name, "temperature_mC": readings,
                                        "fans": reader.attrs(node, ("fan1_input", "fan2_input"))}
    result["iio"] = {node.name: reader.attrs(node, ("name", "in_illuminance_input", "in_colortemp_input", "in_angl_raw", "in_angl_scale"))
                     for node in reader.entries("/sys/bus/iio/devices", "iio:device*")}
    result["als_calibration"] = {}
    for path in (reader.path("/usr/lib/firmware/apple/aop-als-cal.bin"), reader.path("/lib/firmware/apple/aop-als-cal.bin")):
        if path.is_file():
            try:
                blob = path.read_bytes()
                result["als_calibration"][str(path)] = {"size": len(blob), "sha256": hashlib.sha256(blob).hexdigest(), "loaded_or_qualified": False}
            except OSError as error:
                reader.errors.append({"path": str(path), "errno": error.errno, "error": error.strerror})
    power = reader.path("/sys/power")
    result["suspend_readiness"] = {"attributes": reader.attrs(power, ("state", "mem_sleep", "pm_test", "pm_async")),
                                   "stats": reader.attrs(power / "suspend_stats", ("success", "fail", "failed_freeze", "failed_prepare", "failed_suspend", "failed_suspend_late", "failed_suspend_noirq", "failed_resume", "failed_resume_early", "failed_resume_noirq", "last_failed_dev", "last_failed_errno", "last_failed_step")),
                                   "wakeup_sources": reader.read(reader.path("/sys/kernel/debug/wakeup_sources"), optional=True),
                                   "cpu_idle": {}}
    for cpu in reader.entries("/sys/devices/system/cpu", "cpu[0-9]*"):
        if not cpu.name.removeprefix("cpu").isdigit():
            continue
        states = {node.name: reader.attrs(node, ("name", "desc", "disable", "usage", "time")) for node in sorted((cpu / "cpuidle").glob("state*"))}
        if states:
            result["suspend_readiness"]["cpu_idle"][cpu.name] = states
    result["journal"] = command(["journalctl", "-b", "-k", "--no-pager", "-o", "short-monotonic", "-g",
                                    "Apple:.*WFI|aop.*als|dwc3|typec|sn201202|thunderbolt|acio|suspend|resume|wakeup|SError|Oops"]) if journal and root == Path("/") else {"not_collected": True}
    result["inhibitors"] = command(["systemd-inhibit", "--list", "--no-pager"]) if root == Path("/") else {"not_collected": True}
    result["limits"] = ["Left-rear ATC0 is the retained debug link, omitted from normal USB qualification.",
                         "No xHCI or USB root hub with no cable is intentional in dwc3-apple, not a failure.",
                         "Unplugged device/sink role does not prove role-switch failure; do not force roles.",
                         "A bound ACIO with an empty Thunderbolt bus does not qualify a router or tunnel.",
                         "DRM connected/modes and active altmode are evidence, not external display stability or attribution.",
                         "PD online/contract is not battery charging; full/threshold-limited batteries may not charge.",
                         "TTPD is preserved verbatim; its finite firmware value has unknown semantics, no invented clamp.",
                         "Missing ALS calibration is a same-unit macOS acquisition dependency; ENODATA is not zero lux.",
                         "WFI state names/counters alone do not prove WFI execution; retain the boot quirk log.",
                         "Keep pd_ignore_unused/clk_ignore_unused; WFI is not suspend/resume qualification.",
                         "No suspend is executed, and readiness does not qualify wakeup, drain, or resume."]
    result["read_errors"] = reader.errors
    result["finished_utc"] = timestamp()
    return result


def flatten(value, prefix=""):
    if isinstance(value, dict):
        out = {}
        for key, child in value.items():
            out.update(flatten(child, prefix + "/" + key))
        return out
    return {prefix: value}


def compare(before, after):
    if before.get("schema") != SCHEMA or before.get("kind") != KIND:
        raise ValueError("Unsupported before-report schema/kind")
    for key in ("boot_id", "unit_serial_sha256"):
        value = after["identity"].get(key)
        if not value or before.get("identity", {}).get(key) != value:
            raise ValueError(f"Before-report is not from this same unit/boot ({key})")
    fields = ("ports", "usb_devices", "thunderbolt_devices", "drm_connectors", "power_supplies", "suspend_readiness")
    old = flatten({key: before.get(key) for key in fields})
    new = flatten({key: after.get(key) for key in fields})
    changes = {key: {"before": old.get(key), "after": new.get(key)} for key in sorted(old.keys() | new.keys()) if old.get(key) != new.get(key)}
    return {"before_started_utc": before.get("started_utc"), "changes": changes, "physically_qualified": False}


def observations(report, port):
    target = report["ports"].get(port)
    if target is None:
        return {}
    typec = target["typec"] or {}
    usb = {name: node["attributes"] for name, node in report["usb_devices"].items() if node["port"] == port and not node["root_hub"]}
    supply = {name: node["attributes"] for name, node in report["power_supplies"].items()
              if typec.get("path") and Path(typec["path"]).parent.parent == Path(node["path"]).parent.parent}
    return {"partner_present": typec.get("partner_present"), "typec_attributes": typec.get("attributes", {}),
            "usb_devices_on_controller": usb, "port_power_supplies": supply,
            "battery": report["power_supplies"].get("macsmc-battery", {}).get("attributes", {}),
            "thunderbolt_on_acio": {name: node for name, node in report["thunderbolt_devices"].items() if node["port"] == port},
            "qualified": False}


def plan():
    return {"schema": SCHEMA, "kind": KIND + "-plan", "read_only": True, "suspend_execution": False,
            "ports": list(PORTS), "orientations": [1, 2],
            "method": "Take baseline, attach one known device/cable to one labelled port under attendance, capture --before baseline, then physically disconnect and capture --case disconnect. Repeat both orientations and repeated reconnection; leave debug port untouched.",
            "cases": {
                "usb2": "Known USB2 input device: require host role, actual device <=480 Mbit/s, attended input function and repeated reconnection. No storage writes.",
                "usb3": "Known SuperSpeed device/cable: require actual >=5000 Mbit/s endpoint; check useful read-only function and reconnect, not just root hub.",
                "charging": "Known PD charger: capture negotiated power_operation_mode/USB type, port supply online, macsmc-ac online/input_power_limit, battery status/current/power/energy before and after an attended interval. Distinguish full/threshold limit from failure; never change thresholds.",
                "dp": "Known DP display/cable: capture partner altmode, DRM connector/modes; operator must attribute port, check real image, stable mode and hot unplug/replug. No claim based solely on connected sysfs.",
                "thunderbolt": "Known TB/USB4 device: capture router generation/link and actual tunnel function. Do not authorize devices or loosen security. A USB fallback is not TB qualification; left-front ACIO is not described here.",
                "disconnect": "Require partner and device removal and later reconnection, not force role=none; preserve any teardown errors.",
                "suspend-readiness": "Capture only: states, pm_test, errors/stats, inhibitors, CPU idle counters and WFI boot log. With backup/recovery and attended wakeup plan, a separate authorized campaign must later test suspend/resume and drain; this tool never initiates sleep."},
            "qualification": "All physical/function/repetition/orientation results remain unqualified until operator evidence exists; snapshots cannot award a pass."}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--plan", action="store_true", help="print attended campaign requirements without inspecting hardware")
    parser.add_argument("--case", choices=CASES, default="baseline")
    parser.add_argument("--port", choices=tuple(PORTS))
    parser.add_argument("--orientation", type=int, choices=(1, 2), help="operator-labelled cable orientation; repeat both")
    parser.add_argument("--equipment", help="operator's known device and cable identification")
    parser.add_argument("--before", type=Path, help="previous same-unit/same-boot JSON snapshot")
    parser.add_argument("--output", type=Path, help="explicitly create private JSON; refuses existing path")
    parser.add_argument("--no-journal", action="store_true", help="do not invoke journalctl; unreadable logs remain explicit otherwise")
    parser.add_argument("--root", type=Path, default=Path("/"), help="offline fixture root; external commands disabled")
    args = parser.parse_args()
    if args.case not in ("baseline", "suspend-readiness") and not args.plan:
        if not args.port or not args.equipment or (args.case != "disconnect" and not args.orientation):
            parser.error("attachment cases require --port, --orientation and --equipment; disconnect requires --port and --equipment")
    try:
        report = plan() if args.plan else collect(args.root.resolve(), not args.no_journal)
        if not args.plan:
            report["operator_case"] = {"case": args.case, "port": args.port, "orientation": args.orientation, "equipment": args.equipment}
            report["observations"] = observations(report, args.port)
            if args.before:
                report["comparison"] = compare(json.loads(args.before.read_text()), report)
        data = json.dumps(report, indent=2) + "\n"
        if args.output:
            descriptor = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "w") as stream:
                stream.write(data)
        else:
            print(data, end="")
    except (OSError, ValueError) as error:
        parser.exit(1, f"Evidence capture refused: {error}\n")


if __name__ == "__main__":
    main()
