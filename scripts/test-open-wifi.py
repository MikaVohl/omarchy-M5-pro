#!/usr/bin/env python3
"""Exercise the patched kernel's real join builder on the host, without Wi-Fi access."""
import argparse
from pathlib import Path
import subprocess
import tempfile

PRELUDE = r"""
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>
#include <stdio.h>
#include <string.h>
#include <errno.h>
typedef uint8_t u8;
typedef uint16_t u16;
typedef uint32_t u32;
typedef uint32_t __le32;
#define BIT(n) (1U << (n))
#define ETH_ALEN 6
#define IEEE80211_MAX_SSID_LEN 32
#define SAE_PASSWORD_MAX_LEN 128
#define WLAN_AKM_SUITE_PSK 0x000fac02
#define WLAN_AKM_SUITE_SAE 0x000fac08
#define WLAN_CIPHER_SUITE_CCMP 0x000fac04
#define WLAN_EID_RSN 48
#define WLAN_EID_SSID 0
#define WLAN_CAPABILITY_ESS 1
#define WLAN_CAPABILITY_PRIVACY 16
#define IEEE80211_BSS_TYPE_ESS 1
#define IEEE80211_PRIVACY_OFF 0
#define IEEE80211_PRIVACY_ON 1
#define IEEE80211_PRIVACY_ANY 2
#define CFG80211_BSS_FTYPE_UNKNOWN 0
#define NL80211_BAND_2GHZ 0
#define NL80211_BAND_5GHZ 1
#define NL80211_BAND_6GHZ 2
#define NL80211_AUTHTYPE_OPEN_SYSTEM 0
#define NL80211_AUTHTYPE_AUTOMATIC 8
#define NL80211_MFP_REQUIRED 1
#define GFP_KERNEL 0
#define READ_ONCE(x) (x)
#define WRITE_ONCE(x, v) ((x) = (v))
#define cpu_to_le32(x) (x)
struct ieee80211_channel { int band, center_freq; };
struct cfg80211_bss { u8 bssid[6]; struct ieee80211_channel *channel; };
struct cfg80211_crypto_settings {
    unsigned n_akm_suites, n_ciphers_pairwise, sae_pwd_len, wpa_versions;
    u32 akm_suites[4], ciphers_pairwise[4], cipher_group;
    const u8 *psk, *sae_pwd;
};
struct cfg80211_connect_params {
    const u8 *ssid, *bssid, *bssid_hint, *ie;
    size_t ssid_len, ie_len;
    struct ieee80211_channel *channel, *channel_hint;
    struct cfg80211_crypto_settings crypto;
    bool privacy;
    unsigned auth_type, mfp, key_len;
};
struct net_device { int unused; };
struct centauri_t6050_wifi {
    bool stopping, configured, connecting, connected, scan_request, disconnect_pending;
    bool connect_abort, connect_event, disconnect_event;
    u16 connect_status;
    struct ieee80211_channel *connect_channel;
    u8 bssid[6], connect_payload[1024], request_ie[256], ssid[32];
    size_t connect_size, request_ie_size, ssid_len;
    int lock, connect_work;
    struct wiphy *wiphy;
    bool connect_open;
};
struct wiphy { struct centauri_t6050_wifi *wifi; };
static void *wiphy_priv(struct wiphy *w) { return &w->wifi; }
static void mutex_lock(int *p) { (void)p; }
static void mutex_unlock(int *p) { (void)p; }
static void schedule_work(int *p) { (void)p; }
static void memzero_explicit(void *p, size_t n) { memset(p, 0, n); }
static u16 get_unaligned_le16(const void *v) { const u8 *p=v; return p[0] | p[1]<<8; }
static u32 get_unaligned_be32(const void *v) { const u8 *p=v; return (u32)p[0]<<24 | (u32)p[1]<<16 | p[2]<<8 | p[3]; }
static void put_unaligned_le16(u16 n, void *v) { u8 *p=v; p[0]=n; p[1]=n>>8; }
static void put_unaligned_be32(u32 n, void *v) { u8 *p=v; p[0]=n>>24; p[1]=n>>16; p[2]=n>>8; p[3]=n; }
static int firmware_band(int band) { return band; }
static int ieee80211_frequency_to_channel(int freq) { return (freq-2407)/5; }
static const u8 *cfg80211_find_ie(u8 id, const u8 *p, size_t n) {
    while (n >= 2 && (size_t)p[1]+2 <= n) {
        if (p[0] == id) return p;
        size_t len = p[1]+2; p += len; n -= len;
    }
    return NULL;
}
static int lookup_privacy, reported_capability;
static struct cfg80211_bss ap;
static struct cfg80211_bss *cfg80211_get_bss(struct wiphy *w,
    struct ieee80211_channel *c, const u8 *b, const u8 *s, size_t n, int t, int privacy) {
    (void)w; (void)c; (void)b; (void)s; (void)n; (void)t;
    lookup_privacy = privacy;
    return privacy == IEEE80211_PRIVACY_ANY ? NULL : &ap;
}
static void cfg80211_put_bss(struct wiphy *w, struct cfg80211_bss *b) { (void)w; (void)b; }
static struct cfg80211_bss *cfg80211_inform_bss(struct wiphy *w,
    struct ieee80211_channel *c, int type, const u8 *b, unsigned long tsf,
    int capability, int interval, const u8 *ie, size_t n, int signal, int gfp) {
    (void)w; (void)c; (void)type; (void)b; (void)tsf; (void)interval;
    (void)ie; (void)n; (void)signal; (void)gfp;
    reported_capability = capability;
    return &ap;
}
"""

TESTS = r"""
static struct ieee80211_channel channel = {NL80211_BAND_2GHZ, 2417};
static struct centauri_t6050_wifi wifi;
static struct wiphy wiphy = {&wifi};
static struct cfg80211_connect_params params;
static const u8 rsn[] = {48,20,1,0,0,15,172,4,1,0,0,15,172,4,1,0,0,15,172,2,0,0};
static const u8 pmk[32] = {1,2,3,4};
static void reset(void) {
    memset(&wifi, 0, sizeof(wifi));
    memset(&params, 0, sizeof(params));
    wifi.configured = true; wifi.wiphy = &wiphy;
    params.ssid = (const u8 *)"guest"; params.ssid_len = 5;
    params.auth_type = NL80211_AUTHTYPE_AUTOMATIC;
    ap.channel = &channel; channel.band = NL80211_BAND_2GHZ;
}
static const u8 *tlv(unsigned tag) {
    for (size_t i=130; i+4 <= wifi.connect_size;) {
        unsigned n=get_unaligned_le16(wifi.connect_payload+i+2);
        assert(i+4+n <= wifi.connect_size);
        if (get_unaligned_le16(wifi.connect_payload+i)==tag) return wifi.connect_payload+i;
        i += 4+n;
    }
    return NULL;
}
int main(void) {
    reset();
    assert(wifi_connect(&wiphy, NULL, &params)==0);
    assert(wifi.connecting && lookup_privacy==IEEE80211_PRIVACY_OFF);
    assert(wifi.connect_payload[0x46]==0 && wifi.connect_payload[0x47]==0);
    assert(get_unaligned_le16(wifi.connect_payload+0x48)==0);
    assert(get_unaligned_le16(wifi.connect_payload+0x4a)==0);
    assert(!tlv(WLAN_EID_RSN) && !tlv(313));
    assert(tlv(342) && memcmp(tlv(342)+7,"guest",5)==0);
    connected_bss(&wifi);
    assert(!(reported_capability & WLAN_CAPABILITY_PRIVACY));
    puts("PASS: open join has no key, cipher, RSN or synthetic privacy flag");
    reset(); params.privacy=true;
    assert(wifi_connect(&wiphy,NULL,&params)<0 && !wifi.connecting);
    reset(); params.crypto.n_akm_suites=1; params.crypto.akm_suites[0]=WLAN_AKM_SUITE_PSK;
    assert(wifi_connect(&wiphy,NULL,&params)<0 && !wifi.connecting);
    reset(); params.ie=rsn; params.ie_len=sizeof(rsn);
    assert(wifi_connect(&wiphy,NULL,&params)<0 && !wifi.connecting);
    reset(); params.crypto.n_ciphers_pairwise=1; params.crypto.ciphers_pairwise[0]=1;
    assert(wifi_connect(&wiphy,NULL,&params)<0 && !wifi.connecting);
    reset(); channel.band=NL80211_BAND_6GHZ;
    assert(wifi_connect(&wiphy,NULL,&params)==-EOPNOTSUPP);
    puts("PASS: secured requests cannot downgrade and open 6 GHz is rejected");
    reset(); params.privacy=true; params.ie=rsn; params.ie_len=sizeof(rsn);
    params.crypto.n_akm_suites=1; params.crypto.akm_suites[0]=WLAN_AKM_SUITE_PSK;
    params.crypto.n_ciphers_pairwise=1; params.crypto.ciphers_pairwise[0]=WLAN_CIPHER_SUITE_CCMP;
    params.crypto.cipher_group=WLAN_CIPHER_SUITE_CCMP; params.crypto.psk=pmk;
    assert(wifi_connect(&wiphy,NULL,&params)==0);
    assert(lookup_privacy==IEEE80211_PRIVACY_ON);
    assert(wifi.connect_payload[0x47]==4 && get_unaligned_le16(wifi.connect_payload+0x48)==4);
    assert(get_unaligned_le16(wifi.connect_payload+0x4a)==32);
    assert(memcmp(wifi.connect_payload+0x4c,pmk,32)==0);
    assert(tlv(WLAN_EID_RSN));
    connected_bss(&wifi); assert(reported_capability & WLAN_CAPABILITY_PRIVACY);
    puts("PASS: WPA2 still carries PMK, CCMP and RSN");
    reset();
    u8 sae_rsn[sizeof(rsn)]; memcpy(sae_rsn, rsn, sizeof(rsn));
    sae_rsn[19]=8; sae_rsn[20]=128;
    params.privacy=true; params.ie=sae_rsn; params.ie_len=sizeof(sae_rsn);
    params.crypto.n_akm_suites=1; params.crypto.akm_suites[0]=WLAN_AKM_SUITE_SAE;
    params.crypto.n_ciphers_pairwise=1; params.crypto.ciphers_pairwise[0]=WLAN_CIPHER_SUITE_CCMP;
    params.crypto.cipher_group=WLAN_CIPHER_SUITE_CCMP;
    params.crypto.sae_pwd=(const u8 *)"password"; params.crypto.sae_pwd_len=8;
    channel.band=NL80211_BAND_6GHZ;
    assert(wifi_connect(&wiphy,NULL,&params)==0);
    assert(wifi.connect_payload[0x46]==3 && wifi.connect_payload[0x47]==8);
    assert(get_unaligned_le16(wifi.connect_payload+0x4a)==0);
    assert(tlv(313) && get_unaligned_le16(tlv(313)+4)==8);
    assert(memcmp(tlv(313)+6,"password",8)==0);
    puts("PASS: WPA3 SAE still carries its password and permits protected 6 GHz");
    reset();
    const u8 extra_ie[]={127,1,0};
    params.ie=extra_ie; params.ie_len=sizeof(extra_ie);
    params.bssid=ap.bssid; params.channel=&channel;
    assert(wifi_connect(&wiphy,NULL,&params)==0);
    assert(wifi.request_ie_size==sizeof(extra_ie));
    assert(memcmp(wifi.request_ie,extra_ie,sizeof(extra_ie))==0);
    reset(); params.key_len=5;
    assert(wifi_connect(&wiphy,NULL,&params)<0);
    reset(); params.mfp=NL80211_MFP_REQUIRED;
    assert(wifi_connect(&wiphy,NULL,&params)<0);
    puts("PASS: open association elements survive; WEP and mandatory PMF cannot downgrade");
    return 0;
}
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True, help="patched kernel source tree")
    parser.add_argument("--cc", default="cc")
    args = parser.parse_args()
    source = (args.source / "drivers/net/wireless/apple/centauri/t6050/centauri_wifi.c").read_text()
    start = source.index("static int rsn_caps_offset(")
    end = source.index("\n/*\n * Without the host power state", start)
    with tempfile.TemporaryDirectory(prefix="j716s-open-wifi-") as directory:
        unit = Path(directory) / "join.c"
        unit.write_text(PRELUDE + source[start:end] + TESTS)
        binary = Path(directory) / "join"
        subprocess.run([args.cc, "-std=gnu11", "-O2", "-Wall", "-Wextra", "-Werror",
                        "-Wno-unused-function", "-Wno-unused-parameter", "-Wno-sign-compare",
                        str(unit), "-o", str(binary)], check=True)
        subprocess.run([str(binary)], check=True, timeout=10)


if __name__ == "__main__":
    main()
