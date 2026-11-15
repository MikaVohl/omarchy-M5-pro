"""Compile published SCO production paths from the immutable kernel archive.

Run: python3 -B scripts/test-bluetooth-sco-kernel.py
Requires source preparation and a C compiler; never HCI/audio operations.
"""
import hashlib
import tarfile
from pathlib import Path
import re
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
COMMIT = '8ab10100a4c1c944797e3fc8345589aff57777df'
ARCHIVE = ROOT / ('work/sources/kernel-' + COMMIT + '.tar.gz')
ARCHIVE_SHA256 = '06acab85a9e668010fe9e664d9fc92a15d506a986faab8b9659edd4bbcaaeac2'


def production_source():
    relative = 'drivers/bluetooth/btcentauri_t6050.c'
    with ARCHIVE.open('rb') as stream:
        if hashlib.file_digest(stream, 'sha256').hexdigest() != ARCHIVE_SHA256:
            raise ValueError('kernel source archive differs from the immutable pin')
        stream.seek(0)
        with tarfile.open(fileobj=stream, mode='r:gz') as archive:
            member = archive.getmember('linux-' + COMMIT + '/' + relative)
            if not member.isfile():
                raise ValueError('kernel transport source is not a regular member')
            baseline = archive.extractfile(member).read()
    with tempfile.TemporaryDirectory() as temporary:
        directory = Path(temporary)
        path = directory / relative
        path.parent.mkdir(parents=True)
        path.write_bytes(baseline)
        subprocess.run(['patch', '--batch', '--fuzz=0', '-p1', '-i',
                        str(ROOT / 'patches/kernel-j716s-bluetooth-sco.patch')],
                       cwd=directory, check=True)
        return path.read_text()


def function(source, name):
    match = re.search(r'^static [^\n]*\b' + name + r'\([^;]*?\n\{', source, re.MULTILINE)
    if not match:
        raise AssertionError('missing production function: ' + name)
    opener = source.index('{', match.start())
    depth = 1
    end = opener + 1
    while depth:
        depth += (source[end] == '{') - (source[end] == '}')
        end += 1
    return source[match.start():end]


PRELUDE = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <errno.h>
typedef uint8_t u8; typedef uint16_t u16; typedef uint32_t u32;
#define ARRAY_SIZE(a) (sizeof(a)/sizeof((a)[0]))
#define GFP_KERNEL 0
#define HCI_COMMAND_PKT 1
#define HCI_ACLDATA_PKT 2
#define HCI_SCODATA_PKT 3
#define HCI_COMMAND_HDR_SIZE 3U
#define HCI_ACL_HDR_SIZE 4U
#define HCI_SCO_HDR_SIZE 3U
#define HCI_OP_CREATE_CONN 0x0405
#define CEN_OP_CREATE_CONN 0xff7b
#define CEN_FOOTER 260
#define CEN_LE_FOOTER 256
#define CEN_SCO_TX_AUX 1
#define ACL_LINK 1
#define LE_LINK 4
#define READ_ONCE(x) (x)
#define dma_rmb() ((void)0)
#define hci_skb_pkt_type(s) ((s)->type)
#define hci_handle(x) ((x)&0xfff)
#define rcu_read_lock() ((void)0)
#define rcu_read_unlock() ((void)0)
struct hci_cp_create_conn { u8 bytes[13]; };
struct sk_buff { u8 data[800]; unsigned len, type; bool freed; struct sk_buff *next; };
struct sk_buff_head { struct sk_buff *head, *tail; unsigned count; };
struct hci_dev { void *driver; struct { unsigned cmd_tx, acl_tx, sco_tx, byte_tx, byte_rx, err_rx, err_tx; } stat; };
struct hci_conn { u8 type; };
struct cen_aux_spec { u16 id, slots, footer; bool receive; };
struct cen_aux_state { u32 offset; u16 head, post; bool opened; };
struct cen_bt { struct hci_dev *hdev; u8 *arena; struct cen_aux_state aux[11];
    struct sk_buff_head txq, acl_txq, le_txq, sco_txq; u16 sco_tx_head, sco_tx_complete;
    bool connection_command_supported, connection_status_pending, running, faulted;
    u16 tail[22], posted[22]; unsigned posts; int lock, io_work; };
static unsigned frees, received, scheduled, cancelled; static bool allocation_fails;
static struct sk_buff received_packet, allocated_packet; static struct hci_conn connection = {ACL_LINK};
static u16 get_unaligned_le16(const void *p) { const u8 *a=p; return a[0]|(u16)a[1]<<8; }
static u32 get_unaligned_le32(const void *p) { const u8 *a=p; return a[0]|(u32)a[1]<<8|(u32)a[2]<<16|(u32)a[3]<<24; }
static void put_unaligned_le16(u16 n, void *p) { u8 *a=p; a[0]=n; a[1]=n>>8; }
static void put_unaligned_le32(u32 n, void *p) { u8 *a=p; a[0]=n; a[1]=n>>8; a[2]=n>>16; a[3]=n>>24; }
static bool skb_queue_empty(struct sk_buff_head *q) { return !q->count; }
static unsigned skb_queue_len(struct sk_buff_head *q) { return q->count; }
static void skb_queue_tail(struct sk_buff_head *q, struct sk_buff *s) { s->next=NULL; if(q->tail)q->tail->next=s; else q->head=s; q->tail=s; q->count++; }
static struct sk_buff *skb_dequeue(struct sk_buff_head *q) { struct sk_buff *s=q->head; if(s){ q->head=s->next; q->count--; if(!q->head)q->tail=NULL; } return s; }
static void kfree_skb(struct sk_buff *s) { assert(!s->freed); s->freed=true; frees++; }
static void skb_queue_purge(struct sk_buff_head *q) { struct sk_buff *s; while((s=skb_dequeue(q)))kfree_skb(s); }
static struct sk_buff *bt_skb_alloc(unsigned n, int unused) { (void)n; (void)unused; if(allocation_fails)return NULL; memset(&allocated_packet,0,sizeof(allocated_packet)); return &allocated_packet; }
static void skb_put_data(struct sk_buff *s, const void *p, unsigned n) { memcpy(s->data,p,n); s->len=n; }
static void hci_recv_frame(struct hci_dev *h, struct sk_buff *s) { (void)h; received_packet=*s; received++; kfree_skb(s); }
static struct cen_bt *hci_get_drvdata(struct hci_dev *h) { return h->driver; }
static struct hci_conn *hci_conn_hash_lookup_handle(struct hci_dev *h, u16 handle) { (void)h; (void)handle; return &connection; }
static void mutex_lock(int *p) { assert(!*p); *p=1; }
static void mutex_unlock(int *p) { assert(*p); *p=0; }
static void schedule_work(int *p) { (void)p; scheduled++; }
static void cancel_work_sync(int *p) { (void)p; cancelled++; }
static u16 cen_tail(struct cen_bt *bt,u16 ring) { return bt->tail[ring]; }
static void cen_post(struct cen_bt *bt,u16 ring,u16 value) { bt->posted[ring]=value; bt->posts++; }
static int cen_receive_acl(struct cen_bt *bt,const u8 *p,u32 n) { (void)bt; (void)p; (void)n; return 0; }
'''

CASES = r'''
static void descriptor(u8 *d,u16 tag,u8 code,u32 n) { memset(d,0,16); put_unaligned_le32(1|(n<<8),d); put_unaligned_le16(tag,d+12); d[15]=code<<1; }
int main(void) {
    u8 arena[0x60000]={0}; struct hci_dev h={0}; struct cen_bt b={.hdev=&h,.arena=arena,.running=true}; h.driver=&b;
    b.aux[1].offset=0x1000; b.aux[5].offset=0x4000;
    u8 frame[40]={0x42,0x30,4,11,22,33,44};
    assert(cen_sco_packet_length(frame,7)==7); /* status bits retained */
    frame[7]=1; frame[8]=2; memset(frame+9,0xab,8); frame[17]=0; frame[18]=0xfe;
    assert(cen_sco_packet_length(frame,19)==7); /* stop at terminator, not audio */
    assert(cen_sco_packet_length(frame,2)==-EPROTO);
    assert(cen_sco_packet_length(frame,6)==-EPROTO);
    assert(cen_sco_packet_length(frame,16)==-EPROTO); /* short timestamp */
    frame[7]=3; assert(cen_sco_packet_length(frame,8)==-EPROTO);
    frame[1]=0xc0; assert(cen_sco_packet_length(frame,7)==-EOPNOTSUPP); frame[1]=0x30;frame[7]=0;
    u8 maximum[724]={0x42,0,255};assert(cen_sco_packet_length(maximum,258)==258);
    assert(cen_sco_packet_length(maximum,257)==-EPROTO);assert(cen_sco_packet_length(maximum,724)==258);
    u8 empty[3]={0x42,0x20,0};assert(cen_sco_packet_length(empty,3)==3);
    u8 d[16];
    for(unsigned code=0;code<8;code++) { descriptor(d,1,code,7); int result=cen_sco_descriptor_status(d,1);
        if(code==0||code==2||code==4)assert(result==0);
        else if(code==3||code==5||code==6)assert(result==-EIO);else assert(result==-EPROTO); }
    descriptor(d,2,0,7); assert(cen_sco_descriptor_status(d,1)==-EPROTO);d[0]=2;assert(cen_sco_descriptor_status(d,2)==-EPROTO);
    assert(cen_receive_sco(&b,frame,19)==0);assert(received==1);assert(received_packet.len==7);
    assert(received_packet.type==HCI_SCODATA_PKT);assert(received_packet.data[1]==0x30);assert(h.stat.byte_rx==7);
    allocation_fails=true;assert(cen_receive_sco(&b,frame,7)==0);assert(h.stat.err_rx==1);allocation_fails=false;
    /* RX ring returns one slot and reposts, including allocation failure. */
    b.aux[5].opened=true;b.aux[5].post=8;b.tail[9]=1;
    u8 *rx=arena+b.aux[5].offset;descriptor(rx,1,0,8);memcpy(rx+16,frame,8);
    assert(cen_aux_drain(&b,true)==0);assert(b.aux[5].head==1);assert(b.aux[5].post==0);assert(b.posted[9]==0);
    assert(get_unaligned_le32(arena+b.aux[5].offset+8*740)==(1|(724<<8)));
    b.tail[9]=9;assert(cen_aux_drain(&b,true)==-EPROTO);b.tail[9]=1;
    u8 *rx1=rx+740;b.tail[9]=2;descriptor(rx1,2,0,725);
    assert(cen_aux_drain(&b,true)==-EPROTO);assert(b.aux[5].head==1&&b.aux[5].post==0);
    descriptor(rx1,2,0,8);memcpy(rx1+16,frame,8);rx1[23]=3;
    assert(cen_aux_drain(&b,true)==-EPROTO);assert(b.aux[5].head==1&&b.aux[5].post==0);
    rx1[23]=0;allocation_fails=true;unsigned old_rx=received;
    assert(cen_aux_drain(&b,true)==0);assert(received==old_rx);allocation_fails=false;
    assert(b.aux[5].head==2&&b.aux[5].post==1&&b.posted[9]==1);
    u8 *rx2=rx+2*740;b.tail[9]=3;descriptor(rx2,3,3,8);memcpy(rx2+16,frame,8);
    assert(cen_aux_drain(&b,true)==-EPROTO);assert(received==old_rx&&b.aux[5].head==2);
    b.tail[9]=2;
    /* Driver rejects malformed/extended/unknown inputs without freeing caller's skb. */
    struct sk_buff s={.len=7,.type=HCI_SCODATA_PKT};memcpy(s.data,frame,7);s.data[1]=0;
    assert(cen_hci_send(&h,&s)==0);assert(b.sco_txq.count==1);assert(!s.freed);
    b.sco_tx_head=8;b.tail[3]=0;
    assert(cen_transmit(&b,&b.sco_txq,3,9,b.aux[1].offset,724,&b.sco_tx_head)==0);assert(b.sco_txq.count==1);assert(!s.freed);
    b.tail[3]=1; /* device advances while completion remains unchecked */
    assert(cen_transmit(&b,&b.sco_txq,3,9,b.aux[1].offset,724,&b.sco_tx_head)==0);
    assert(b.sco_txq.count==1&&!s.freed&&b.sco_tx_head==8);
    /* Reclaimed descriptor ownership, wrap and no overwrite while full. */
    for(unsigned slot=0;slot<8;slot++)descriptor(arena+b.aux[1].offset+slot*740,slot+1,0,7);
    b.tail[3]=1;assert(cen_sco_reclaim(&b)==0);assert(b.sco_tx_complete==1);
    unsigned before=frees;assert(cen_transmit(&b,&b.sco_txq,3,9,b.aux[1].offset,724,&b.sco_tx_head)==0);
    assert(s.freed&&frees==before+1);assert(b.sco_tx_head==0);assert(!b.sco_txq.count);
    u8 *tx=arena+b.aux[1].offset+8*740;assert(get_unaligned_le32(tx)==(1|(7<<8)));assert(!memcmp(tx+16,s.data,7));
    for(unsigned i=23;i<740;i++)assert(tx[i]==0);assert(h.stat.sco_tx==1&&h.stat.acl_tx==0&&h.stat.byte_tx==7);
    b.tail[3]=0;assert(cen_sco_reclaim(&b)==0);assert(b.sco_tx_complete==0);
    assert(cen_sco_reclaim(&b)==0); /* repeated interrupt does not reconsume */
    b.tail[3]=9;assert(cen_sco_reclaim(&b)==-EPROTO);b.tail[3]=2;b.sco_tx_head=1;assert(cen_sco_reclaim(&b)==-EPROTO);
    b.tail[3]=1;descriptor(arena+b.aux[1].offset,1,3,7);assert(cen_sco_reclaim(&b)==-EIO);assert(b.sco_tx_complete==0);
    struct sk_buff bad={.len=2,.type=HCI_SCODATA_PKT};assert(cen_hci_send(&h,&bad)==-EMSGSIZE);assert(!bad.freed);
    bad.len=7;bad.data[2]=5;assert(cen_hci_send(&h,&bad)==-EMSGSIZE);bad.data[2]=4;bad.data[1]=0x10;
    assert(cen_hci_send(&h,&bad)==-EOPNOTSUPP);assert(!bad.freed);bad.data[1]=0;bad.type=0xf3;
    assert(cen_hci_send(&h,&bad)==-EOPNOTSUPP);bad.type=HCI_SCODATA_PKT;
    b.running=false;assert(cen_hci_send(&h,&bad)==-ENETDOWN);b.running=true;b.sco_txq.count=64;
    assert(cen_hci_send(&h,&bad)==-ENOBUFS);assert(!bad.freed);b.sco_txq.count=0;
    /* ACL/command routing and byte statistics remain unchanged. */
    struct sk_buff acl={.len=4,.type=HCI_ACLDATA_PKT},cmd={.len=3,.type=HCI_COMMAND_PKT};cmd.data[0]=0x03;cmd.data[1]=0x0c;
    assert(cen_hci_send(&h,&acl)==0);assert(b.acl_txq.count==1);assert(cen_hci_send(&h,&cmd)==0);assert(b.txq.count==1);
    u16 head=0;assert(cen_transmit(&b,&b.acl_txq,2,9,0x9000,2592,&head)==0);head=0;
    assert(cen_transmit(&b,&b.txq,1,5,0xa000,260,&head)==0);assert(h.stat.acl_tx==1&&h.stat.cmd_tx==1);
    struct sk_buff largest={.len=258,.type=HCI_SCODATA_PKT};memcpy(largest.data,maximum,258);
    assert(cen_hci_send(&h,&largest)==0);assert(!largest.freed&&b.sco_txq.count==1);
    skb_queue_purge(&b.sco_txq);assert(largest.freed);
    /* Healthy close/flush purge driver-owned queued skbs once, retain DMA heads. */
    struct sk_buff p={.len=3,.type=HCI_SCODATA_PKT};assert(cen_hci_send(&h,&p)==0);before=frees;
    assert(cen_hci_close(&h)==0);assert(p.freed&&frees==before+1);assert(cancelled==1);assert(b.sco_tx_head==1);
    assert(cen_hci_open(&h)==0&&b.running);struct sk_buff q={.len=3,.type=HCI_SCODATA_PKT};assert(cen_hci_send(&h,&q)==0);
    before=frees;assert(cen_hci_flush(&h)==0);assert(q.freed&&frees==before+1);assert(cen_hci_flush(&h)==0);assert(frees==before+1);
    b.faulted=true;b.running=false;assert(cen_hci_open(&h)==-EIO&&!b.running);assert(b.lock==0);
    puts("production SCO framing, DMA ownership, credits boundary, errors and lifecycle passed");return 0;
}
'''


class KernelScoHostTests(unittest.TestCase):
    def test_production_paths_offline(self):
        source = production_source()
        specs = re.search(r'static const struct cen_aux_spec cen_aux_specs\[\] = \{.*?\n\};', source, re.DOTALL)
        self.assertIsNotNone(specs)
        names = ('cen_sco_descriptor_status', 'cen_sco_reclaim', 'cen_sco_packet_length',
                 'cen_receive_sco', 'cen_aux_rx_buffer', 'cen_aux_drain', 'cen_transmit',
                 'cen_hci_open', 'cen_hci_close', 'cen_hci_flush', 'cen_hci_send')
        body = '\n\n'.join(function(source, name) for name in names)
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            cfile, binary = directory / 'sco.c', directory / 'sco'
            cfile.write_text(PRELUDE + specs.group() + '\n' + body + CASES)
            subprocess.run(['cc', '-std=c11', '-O2', '-Wall', '-Wextra', '-Werror',
                            '-Wno-misleading-indentation', str(cfile), '-o', str(binary)], check=True,
                           capture_output=True, text=True)
            subprocess.run([str(binary)], check=True)


if __name__ == '__main__':
    unittest.main()
