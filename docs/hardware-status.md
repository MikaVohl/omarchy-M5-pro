# Hardware qualification

This is a source release of a working bring-up, not a supported installer or a daily-driver certification. Persistent Omarchy boot, owner setup, and an authenticated native Hyprland session were observed. The final display mode survived a restart. Newly rebuilt artifacts must be qualified on their own target before disk deployment.

| Component | Observed result | Boundary |
| --- | --- | --- |
| Board | Mac17,8 / J716s / T6050 M5 Pro | Not J714s, K114c, M5 Max, or a generic Apple Silicon recipe |
| Firmware | macOS 27.3 beta 1, build 26C5035r; mBoot-20457.60.65.0.1 | Exact running macOS, system firmware, and stub pairing required |
| Kernel | 7.3.0-j716s-8ab10100a4c1; all 18 CPUs booted | Pinned source; newly generated module signing keys change build bytes |
| Root | Persistent Arch Linux ARM + Omarchy | Full initial image write/readback passed |
| Owner setup | Physical tty1 account/password; authenticated SDDM login | No default credentials, forced SSH, or passwordless sudo |
| GPU | Native G17S vertex/fragment work and native Vulkan/zink session | Not a claim of complete compute/API conformance |
| Panel | Native DCP, full-notch 3456×2234 at 120 Hz | Hyprland configuration verified; external outputs not qualified |
| Keyboard/trackpad | Physical interaction verified | Board-matched trackpad firmware required |
| Network | Wi-Fi association worked | No Wi-Fi performance, roaming, or Bluetooth qualification |
| Audio | Not qualified | Speakers intentionally disabled; do not remove the safety policy |
| Suspend/resume | Not qualified | Do not infer safety from a working desktop |
| External displays | Not qualified | Internal panel success does not qualify external DCP paths |
| Camera / Touch ID | Not qualified | No claim of support |
| Stability / repeated recovery | Not qualified | No broad daily-use or repeated retained-macOS recovery campaign |

The opt-in open-network Wi-Fi kernel
`7.3.0-j716s-8ab10100a4c1-open-wifi` booted successfully. Live inspection
confirmed association to an open AP and successful boot-identity validation;
a working guest-network connection was reported. This qualifies that observed
connection, not all open APs, roaming, performance, or physical WPA2/WPA3
regressions. After the successful trial, the tested payload was selected as the
persistent default; the original baseline and rescue remain preserved. No
subsequent normal-default boot has yet been observed.
See [setup](setup.md#experimental-open-network-wi-fi-candidate).

Obtain and verify a current backup before partitioning or deployment.

## Why the display configuration differs from a stock setup

The T6050 kernel uses the G17S backend. `DRM_ASAHI_M5` governs the separate T8142 backend; switching it on does not select the T6050 GPU.

The private Mesa prefix is `/opt/mesa-j716s`, built from the pinned Aurora Mesa source without replacing its UAPI header. The recorded kernel header adds optional compute flags, not changed structures or ioctl identities; the exact comparison is retained under `outputs/persistent-install/graphics/`.

SDDM filters its daemon environment. Its greeter needs the private graphics variables through `[General] GreeterEnvironment`; an ordinary systemd `Environment=` override did not work.

Full-notch mode needs both `appledrm.show_notch=1` on the kernel command line and `chosen.asahi,show-notch=1` in the Aura payload. Neither flag supplies a different board's display data.
