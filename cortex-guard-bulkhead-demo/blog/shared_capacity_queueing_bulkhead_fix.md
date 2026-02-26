# Your ML Service Looks Fine — Until a Burst Kills It

You have an ML security gateway. It scans text for policy violations.
Two kinds of callers depend on it:

- **Non-blocking** callers don't need the answer right now. They fire a request
  and poll later. They're high-volume and patient.
- **Blocking** callers need the answer *before they can continue*. An API gateway
  waiting on a policy verdict before letting a response through. Low-volume, but
  latency-sensitive.

Steady state? Everything's fine. Your service handles 200 requests per second,
CPU sits at 40%, dashboards are green.

Then 200 blocking calls hit in two seconds. And everything falls over.

This post explains why — and how to fix it.

---

## Blocking vs Non-Blocking (Practically)

Forget the textbook definitions. Here's what matters:

- **Blocking** = the caller is *waiting on the line*. Every millisecond you take
  is a millisecond their user is staring at a spinner. If you're too slow, they
  time out, and the whole upstream request fails.

- **Non-blocking** = the caller drops off a job and walks away. They'll check
  back later. You can take a reasonable amount of time — they don't care.

The critical insight: these two callers have fundamentally different latency
needs. Keep that in mind.

---

## The Failure Mode: Shared-Capacity Queueing

**Shared-capacity queueing** means both request types compete for the same pool
of workers (or connections, or threads, or semaphore slots). Whoever gets there
first, gets served. Everyone else waits.

Here's what the baseline architecture looks like:

```
                    ┌──────────────────────────────────┐
  Non-blocking ────►│                                  │
                    │      Single API Service           │
  Blocking ────────►│      Capacity: 24 slots           │
                    │                                  │
                    │   ┌──┬──┬──┬──┬──┬──┬──┬──┐      │
                    │   │B │B │B │B │B │B │NB│NB│ ...  │
                    │   └──┴──┴──┴──┴──┴──┴──┴──┘      │
                    │          ▲                        │
                    │   Blocking fills all slots.       │
                    │   Non-blocking waits. And waits.  │
                    │   Client timeout → 504.           │
                    └──────────────────────────────────┘
```

This is called **head-of-line blocking**: a slow request at the front of the
line delays everything behind it, even the fast stuff.

At low traffic, you'll never notice. The queue drains faster than it fills. But
traffic doesn't arrive in neat, even streams.

> **If two request types have different latency needs, they must not share a queue.**

### Why averages lie

Your average load might be 50 requests/second against a capacity of 200. Looks
like headroom. But traffic is bursty. A single retry storm, a batch job kicking
off, or a deployment surge can send 200 blocking requests in two seconds. Now
all 24 slots are occupied by slow work. The non-blocking requests — ten times
more frequent — pile up behind them and time out.

The dashboard says "average latency: 180ms." Your p95 says 4,800ms. Your
callers say "it's broken."

**p95 latency** (the 95th percentile) is the response time that 95% of requests
beat. It tells you how bad the worst-case feels for real users. Averages hide
bursts; p95 exposes them.

---

## Why ML Makes This Worse

General-purpose API services have this problem too. But ML services make it
significantly worse for three reasons:

**1. Cold model loads are brutal.**
If the model isn't pre-loaded in memory, each request pays a startup tax:
downloading weights, initializing the runtime, allocating GPU/CPU memory. That
can be 5-8 seconds *per request*. This is the "cold start per request" pattern —
and it turns a brief burst into minutes of total blockage.

**Cold model load:** every request initializes the model from scratch (seconds).
**Warm model load:** the model loads once at startup and stays in memory. Per-request
scans are fast (under 250ms).

Even warm, ML inference has more latency *variance* than a database query. A
scan might take 80ms or 250ms depending on input size, model complexity, or
garbage collection. That variance multiplies queueing delays under load.

**2. Scans are CPU/memory-heavy.**
An ML scan isn't a lightweight database lookup. It holds real resources for the
full duration. Fewer requests can run concurrently, so the capacity pool is
smaller and fills up faster.

**3. Burst sensitivity is higher.**
Small capacity pool + high per-request cost + bursty traffic = a recipe for
queueing collapse.

---

## The Fix: Three Pillars

The solution isn't "add more capacity." It's *structural*. You change how
traffic flows so that the failure mode becomes controlled rejection instead of
cascading timeouts.

### Pillar 1 — Bulkhead Isolation

**Bulkhead isolation** means giving each traffic type its own capacity pool.
Named after the watertight compartments in a ship's hull — if one floods, the
others stay dry.

```
                    ┌────────────────────────────────┐
  Non-blocking ────►│  Non-Blocking Service           │
                    │  Accept immediately (202)       │
                    │  Background queue → workers     │
                    │  Own capacity. Own fate.        │
                    └────────────────────────────────┘

                    ┌────────────────────────────────┐
  Blocking ────────►│  Blocking Service               │
                    │  Own semaphore (24 slots)       │
                    │  Admission timeout: 100ms       │
                    │  Fast reject (429) if full      │
                    │  Own capacity. Own fate.        │
                    └────────────────────────────────┘
```

Now a burst of blocking calls can't touch non-blocking throughput. The ship
stays afloat.

### Pillar 2 — Admission Control

**Admission control** means you don't let requests in if you can't serve them
promptly. Instead of letting them queue for 5 seconds and then time out, you
reject them in 100 milliseconds with a clear signal: `429 Too Many Requests`.

> **Timeouts are a bad failure mode. Controlled rejection is a good failure mode.**

Why? Because a timeout wastes resources on *both sides* — the server held the
connection, did partial work, and the client waited the full duration for
nothing. A fast 429 frees the server immediately and lets the client retry,
fall back, or fail gracefully without burning seconds.

**Backpressure** is the related idea for queue-based systems: if the queue is
full, reject new work at the door. The non-blocking service does this — if
its queue exceeds a depth limit, it returns `503 Service Unavailable` with a
`queue_full` reason. Instantly. No waiting.

### Pillar 3 — Accept-Fast Non-Blocking

If the caller doesn't need the answer right now, *don't make them wait for it*.

The fixed non-blocking service returns `202 Accepted` immediately. The request
goes into an internal queue. Background workers drain it. The caller polls
`GET /scan/status/{id}` later.

```
Client            Non-Blocking Service
  │                     │
  │─── POST /scan ─────►│
  │◄── 202 Accepted ────│  (instant — <5ms)
  │                     │
  │    ... time passes ...
  │                     │
  │─── GET /status ────►│
  │◄── {"status":"done"}│
```

The caller's perceived latency for the initial request drops from "whatever
the scan takes" to "however long it takes to enqueue." That's effectively zero.

---

## See It Yourself

This repo includes a runnable demo with simulated ML scans and load tests.

**Baseline (shared capacity, shows the failure):**

```bash
./scripts/run_baseline.sh          # start combined service on :8000
./scripts/run_locust_baseline.sh   # open localhost:8089, start test
```

**Fixed (bulkhead + admission control, shows the fix):**

```bash
./scripts/run_fixed.sh             # nonblocking on :8001, blocking on :8002
./scripts/run_locust_fixed.sh      # open localhost:8089, start test
```

The Locust tests use a 10:1 traffic mix (non-blocking to blocking) and include
a burst shape that ramps to 200 users. Toggle `MODEL_MODE=cold` to see the
cold-start amplification effect.

Under baseline, non-blocking p95 climbs into seconds and failures spike.
Under fixed, non-blocking stays sub-10ms and blocking rejects cleanly with 429.

---

## What to Do in Production

A practical checklist for any service with mixed traffic types and
variable-latency work:

- **Pre-load models at startup.** Pay the cold-start cost once, not per request.
  Use warm mode. Persist model files so restarts are fast.

- **Separate deployments for blocking and non-blocking.** Different scaling
  profiles, different capacity pools, different failure domains. This is the
  bulkhead.

- **Cap concurrency and set deadlines.** Every blocking endpoint should have a
  semaphore (or equivalent) and a hard timeout. Know your limits explicitly
  rather than discovering them in an incident.

- **Fail fast at the door.** Return 429/503 within milliseconds when you're
  at capacity. Don't let requests queue silently.

- **Monitor what matters.** Track: inflight requests (saturation), rejection
  rate, queue depth, p95/p99 latency. If you only have one metric, make it p95.

---

## FAQ

### "Why not just add more replicas?"

Scaling out helps with sustained load. It doesn't help with *structure*. If
both traffic types still share capacity, a burst still causes head-of-line
blocking — just across more replicas. You'll burn more money and still have
the same failure mode at a higher traffic threshold. Fix the architecture first,
*then* scale.

### "Why return 429 instead of letting the request time out?"

A timeout wastes the client's entire deadline window (say, 5 seconds) and still
fails. A 429 in 100ms tells the client immediately: "I can't help right now."
The client can retry, try a different backend, degrade gracefully, or show an
honest error — all within that same 5-second budget. The server also frees the
slot instantly instead of holding it for the full timeout.

### "Non-blocking callers are patient. Why do they still need protection?"

Because queues aren't infinite. Without a depth limit, an unbound queue
consumes memory until the process crashes — taking *everyone's* in-flight work
with it. Backpressure (rejecting when the queue is full) keeps the system
healthy even when ingest outpaces processing. The caller gets a clear 503
and can retry. The system stays alive.
