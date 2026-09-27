// LD_PRELOAD shim for mod-host: lock all of its memory, now and in future, so
// its real-time threads never take a page fault.
//
// mod-host doesn't do this itself (it locks only its JACK ringbuffers). What it
// cost: sfizz's disk-streaming threads map and unmap sample memory constantly
// (25-90k page faults per two minutes of playing), and each of those holds the
// process's mmap lock. An audio thread that faulted — even a minor fault, on a
// page that only needed mapping in — waited behind them for longer than a
// 2.67 ms cycle. On the CM5, every xrun in a fixed two-minute pedalled
// Salamander passage lined up with a fault in one of mod-host's real-time
// threads, and every such fault with an xrun (2-25 xruns a run). Locked, the
// same passage ran five times with zero of either.
//
// MCL_FUTURE populates each new mapping as it's made, so the faulting happens
// once, in whichever non-real-time thread allocated it. The price is that
// memory handed back to malloc stays resident until mod-host restarts: its
// high-water mark is what it keeps. That plateaued at ~1.65 GB with Salamander
// under sustained play, leaving ~2 GB of the 4 GB free.
//
// Built by deploy.sh and os-image/stage-pi-synth/04-pi-synth-app into
// /usr/local/lib/libmlockall.so; loaded by systemd/mod-host.service.
#include <errno.h>
#include <stdio.h>
#include <string.h>
#include <sys/mman.h>

__attribute__((constructor)) static void lock_everything(void)
{
    // LD_PRELOAD also reaches the `taskset` that execs mod-host, which is why
    // the journal shows this line twice. Harmless: taskset exits into exec.
    if (mlockall(MCL_CURRENT | MCL_FUTURE) != 0)
        fprintf(stderr, "mlockall_preload: mlockall failed: %s\n", strerror(errno));
    else
        fprintf(stderr, "mlockall_preload: all memory locked\n");
}
