#!/usr/bin/env python3
"""Execute patched kernel ceiling/start-admission bodies offline; no hardware."""
import hashlib
from pathlib import Path
import re
import subprocess
import tarfile
import tempfile

ROOT = Path(__file__).resolve().parents[1]
COMMIT = "8ab10100a4c1c944797e3fc8345589aff57777df"
ARCHIVE = ROOT / ("work/sources/kernel-" + COMMIT + ".tar.gz")
PIN = "06acab85a9e668010fe9e664d9fc92a15d506a986faab8b9659edd4bbcaaeac2"


def function(source, name):
    match = re.search(r"^static [A-Za-z_][^\n]*\b" + name + r"\([^;]*?\)\n\{", source, re.M)
    if not match:
        raise ValueError("missing kernel body: " + name)
    end, depth = match.end(), 1
    while depth:
        depth += (source[end] == "{") - (source[end] == "}")
        end += 1
    return source[match.start():end]


BOUNDARY = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdio.h>
#include <string.h>
#include <errno.h>
#define READ_ONCE(x) (x)
#define smp_load_acquire(p) (*(p))
#define min(a,b) ((a)<(b)?(a):(b))
#define max(a,b) ((a)>(b)?(a):(b))
#define MACAUDIO_SPK_IFACE_UNLOCK 0
#define MACAUDIO_SPK_IFACE_LEASE 1
#define AMP_TAS5770 1
#define AMP_SN012776 2
#define AMP_MAX98360A 3
struct macaudio_platform_cfg { int max_vol, safe_vol, amp; };
struct macaudio_snd_data {
    const struct macaudio_platform_cfg *cfg;
    bool v2_unity_gain, spk_granted, spk_floor_failed;
    int spk_iface;
    long spk_lease_end;
};
static struct macaudio_platform_cfg macaudio_j716_cfg = {.max_vol=-30,.safe_vol=-30,.amp=AMP_SN012776};
static struct macaudio_platform_cfg other_cfg = {.max_vol=-20,.safe_vol=-30,.amp=AMP_SN012776};
static int speaker_max_db;
static bool exact_board;
static bool of_machine_is_compatible(const char *name) { return exact_board && !strcmp(name,"apple,j716s"); }
static long ktime_get(void) { return 100; }
static bool ktime_before(long a,long b) { return a<b; }
'''

CASES = r'''
int main(void) {
    struct macaudio_snd_data ma={.cfg=&macaudio_j716_cfg,.spk_lease_end=200};
    struct macaudio_snd_data *data=&ma;
    for(int board=0;board<2;board++) {
        exact_board=board;
        data->cfg=&macaudio_j716_cfg;
        CACHED_BOARD_SELECTION
        assert(data->v2_unity_gain==(bool)board);
        data->cfg=&other_cfg;
        CACHED_BOARD_SELECTION
        assert(!data->v2_unity_gain);
    }
    data->cfg=&macaudio_j716_cfg; exact_board=true;
    CACHED_BOARD_SELECTION
    assert(macaudio_max_vol(&ma)==-30);
    assert(macaudio_spk_gain(&ma,true)==SN012776_DB(-30));
    ma.spk_iface=MACAUDIO_SPK_IFACE_LEASE;
    assert(macaudio_max_vol(&ma)==0);
    assert(macaudio_spk_gain(&ma,true)==SN012776_DB(0));
    assert(macaudio_spk_gain(&ma,false)==0); /* safe floor unchanged */
    assert(!macaudio_spk_admitted(&ma)); /* a ceiling is NOT a grant */
    ma.spk_granted=true; assert(macaudio_spk_admitted(&ma));
    ma.spk_lease_end=100; assert(!macaudio_spk_admitted(&ma));
    ma.spk_lease_end=200; ma.spk_floor_failed=true; assert(!macaudio_spk_admitted(&ma));
    ma.spk_floor_failed=false;
    speaker_max_db=-40;
    assert(macaudio_max_vol(&ma)==-40);
    ma.spk_iface=MACAUDIO_SPK_IFACE_UNLOCK; speaker_max_db=0;
    assert(macaudio_max_vol(&ma)==-30); /* legacy never inherits unity */
    ma.spk_iface=MACAUDIO_SPK_IFACE_LEASE; ma.v2_unity_gain=false;
    assert(macaudio_max_vol(&ma)==-30); /* no other board promotion */
    puts("PASS: exact-board v2 ceiling, legacy/global caps, floor and lease/start admission");
    return 0;
}
'''


def main():
    with ARCHIVE.open("rb") as stream:
        if hashlib.file_digest(stream,"sha256").hexdigest()!=PIN:
            raise ValueError("kernel archive pin differs")
        stream.seek(0)
        with tarfile.open(fileobj=stream,mode="r:gz") as archive:
            baseline=archive.extractfile("linux-"+COMMIT+"/sound/soc/apple/macaudio.c").read()
    with tempfile.TemporaryDirectory(prefix="j716s-ceiling-") as tmp:
        root=Path(tmp)
        source=root/"sound/soc/apple/macaudio.c"
        source.parent.mkdir(parents=True)
        source.write_bytes(baseline)
        subprocess.run(["patch","--batch","--fuzz=0","-p1","-i",str(ROOT/"patches/kernel-j716s-v2-operating-ceiling.patch")],cwd=root,check=True)
        text=source.read_text()
        names=("macaudio_spk_leased","macaudio_max_vol","macaudio_spk_gain","macaudio_spk_admitted")
        bodies="\n".join(function(text,name) for name in names)
        defines="\n".join(line for line in text.splitlines() if re.match(r"#define (SN012776_DB|TAS5770_DB|MAX98360A_DB|SN012776_0DB|MAX98360A_0DB)(?:\(|\s)",line))
        select=re.search(r"\tdata->v2_unity_gain = .*?;",text,re.S).group()
        cfile=root/"ceiling.c"
        cfile.write_text(BOUNDARY+defines+"\n"+bodies+CASES.replace("CACHED_BOARD_SELECTION",select))
        exe=root/"ceiling"
        subprocess.run(["cc","-std=c11","-Wall","-Wextra","-Werror","-fsanitize=address,undefined",str(cfile),"-o",str(exe)],check=True)
        subprocess.run([str(exe)],check=True)


if __name__=="__main__":
    main()
