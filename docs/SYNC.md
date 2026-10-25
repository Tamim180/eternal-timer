# Sync: how it works, and what can go wrong

Sync is optional and off by default. This document explains what it does, what it protects, and — just as
important — the ways it can slow down or stop, so nothing about it is a surprise.

- [The idea](#the-idea)
- [Setting it up](#setting-it-up)
- [What the relay can and cannot see](#what-the-relay-can-and-cannot-see)
- [When sync can slow down or stop](#when-sync-can-slow-down-or-stop)
- [What keeps working when sync doesn't](#what-keeps-working-when-sync-doesnt)
- [Self-hosting the relay](#self-hosting-the-relay)
- [Troubleshooting](#troubleshooting)
- [How merging works](#how-merging-works)
- [Protocol reference](#protocol-reference)

## The idea

A timer is just a handful of absolute timestamps ("started at 14:00:00, runs for 25 minutes"). Once a second
device has a copy, it can keep counting and ring **without any further help**. So sync doesn't have to be
running when the power goes out; it only has to deliver *changes* (new timer, pause, delete, rename) while both
devices happen to be online.

```
PC ──(HTTPS)──►  relay (ntfy)  ◄──(HTTPS)── phone
```

The devices never connect to each other, so they don't need to be on the same Wi-Fi, no router ports need
opening, and the phone can be anywhere with internet. The relay is a plain publish/subscribe service
([ntfy](https://ntfy.sh)): each device publishes its changes to a private topic and listens on it for the
other's.

## Setting it up

1. **PC:** click the cloud button in the title bar → *Create a pairing code* (or run `etimer sync setup`).
2. **Phone:** open the Android app → cloud button → paste the code → *Join*.
3. The devices exchange their timers. The cloud button shows a dot: green = connected, amber = connecting,
   red = offline (it retries by itself), grey = off.

To use a different relay, enter its URL in the *Relay server* box before creating or joining
(or `etimer sync setup <url>` / `etimer sync join <code> <url>`). Both devices must use the same relay.

To stop: *Turn sync off* on each device (or `etimer sync off`). Your timers stay where they are.

## What the relay can and cannot see

**Cannot see:** the contents of anything — timer names, durations, states, history. Every message is encrypted
and authenticated with AES-256-GCM using a key derived from your pairing code. The relay topic name is also
derived from the code (a long hash), so it can't be guessed or found by browsing.

**Can see (metadata):** your IP address, the fact that you use the service, when messages are sent, and their
approximate size. It also holds recent messages for a short while (see below), but only as ciphertext.

**Important:** the pairing code is the only secret. Anyone who has it can read and change your timers. If it
leaks, turn sync off everywhere and pair again — that creates a new code, topic and key. There is no way to
remove a single device. There is no forward secrecy. See [SECURITY.md](../SECURITY.md).

## When sync can slow down or stop

The default relay is a **free public service run by a third party**. This project is not affiliated with it and
cannot promise anything on its behalf. Here is everything that can make sync slow, delayed or stop:

### The relay itself
- **Outages or changes.** ntfy.sh can go down, change its limits, change its terms, or in the worst case shut
  down. Its own documentation recommends self-hosting for anyone who needs guaranteed availability.
- **Rate limits (per IP address).** ntfy limits how fast each *IP address* may send requests. The default
  server configuration allows a burst of about 60 requests and then refills at roughly one request every 5–10
  seconds, and it limits how many live subscriptions one IP may hold (30 by default). The values ntfy.sh uses
  today may differ and can change. When you hit a limit, the relay rejects requests for a while.
  *What the apps do:* they keep the live connection, wait, and retry with a growing delay (10 s, doubling up
  to 2 minutes). Nothing is lost; changes are just delivered later.
- **Shared IP addresses share the limit.** Mobile data (carrier-grade NAT), dorms, offices and some home
  providers put many people behind one address. You may be rate-limited because of strangers' traffic.
- **Message size.** A relay message is limited to about 4 KB. Each timer is sent as one message and very long
  histories are trimmed (oldest events dropped first, the start event kept) to fit. Nothing else is affected.
- **Short memory.** Public ntfy servers keep messages for only a short time (12 hours by default; ntfy.sh may
  differ). If a device was offline for longer, it doesn't replay the missed messages. Instead it says "hello" when
  it reconnects and the other device answers with a full snapshot, so state still converges — as long as the
  two devices are online at the same time at some point.

### Using many devices or many timers
Sync is **designed and tested for two devices** (a PC and a phone). A few more work, but traffic grows:

- Every device keeps one live connection open, and every device that is already online answers a newcomer's
  "hello" with a full snapshot (at most once per 30 seconds per device).
- A snapshot is **one message per timer**. With *N* timers and *D* devices, one reconnect can cause up to
  roughly *N × (D − 1)* messages. A snapshot larger than the burst allowance (~60) will be throttled and
  delivered gradually.

Practical guidance on the public relay: **up to about 3–4 devices and a few dozen timers is reasonable.**
Beyond that — or if you're automating timers with scripts — please run [your own relay](#self-hosting-the-relay),
and don't point bulk automation at the public one. ntfy's terms of use restrict abusive traffic and working around its rate limits (check their current terms).

### On your side
- **No internet / blocked network.** A firewall, captive portal, or corporate proxy that blocks the relay
  stops sync (the dot turns red). Timers keep working.
- **Android background limits.** Some phone makers aggressively kill background services. If you turn off
  "Keep sync running in the background", the phone syncs when you open the app instead. **Alarms are scheduled
  with the system and ring regardless.**
- **Wrong clocks.** Conflicts are resolved by timestamp, so a device whose clock is badly wrong can overwrite
  newer changes. Keep automatic time on.
- **Different pairing codes or relays.** Both devices must have the same code and the same relay URL.

## What keeps working when sync doesn't

Everything local. Each device stores its own copy of every timer. Timers keep counting, alarms keep ringing,
history keeps recording, and you can still add, pause and delete — all on the device in your hand. When the
relay is reachable again, the devices merge what happened in the meantime (see below). The worst case of a dead
relay is that the two devices stop *copying* to each other.

## Self-hosting the relay

Any ntfy server works. For example, with Docker:

```bash
docker run -d --name ntfy -p 8080:80 -v ntfy-cache:/var/cache/ntfy binwiederhier/ntfy serve \
  --cache-file /var/cache/ntfy/cache.db --cache-duration 720h --behind-proxy
```

(or `pacman -S ntfy-sh` on Arch). Use HTTPS (put it behind a reverse proxy) unless it's only on a trusted LAN.
Then enter its URL as the *Relay server* on both devices.

Notes:
- `--cache-duration 720h` keeps messages for 30 days, which makes "offline for a day" a non-issue.
- The apps do **not support ntfy authentication** (no access tokens). Run the server open on a private network
  (for example behind a VPN such as WireGuard/Tailscale), or rely on the unguessable topic plus end-to-end
  encryption. Don't enable `auth-default-access: deny-all`, or the apps won't be able to connect.
- A relay on your home network is only reachable while that machine is powered — so for the power-cut use case,
  host it somewhere that stays up (a VPS, or a device on a UPS).

## Troubleshooting

| Symptom | Likely cause / fix |
|---|---|
| Dot is red | No internet, relay down, or blocked. The app retries by itself (backing off up to a minute). |
| Dot is green but a change doesn't arrive | You may be rate-limited; it is retried automatically. Wait a minute. `etimer sync now` forces a pull and push. |
| "That doesn't look like a valid pairing code" | The code has 8 groups of 4 characters, using only the letters A–Z and the digits 2–7 (the digits 0, 1, 8 and 9 never appear). Check for typos. |
| Joined, but nothing appears | Check both devices use the **same relay URL**. Then `etimer sync now` on the PC and reopen the phone app. |
| A timer appears on one device but never the other | One device is offline or its clock is far off. Fix and let them reconnect; the next hello triggers a full snapshot. |
| Want to reset everything | Turn sync off on both devices, then create a fresh code. |
| Sync unavailable on the PC | `python-cryptography` isn't installed (`sudo pacman -S python-cryptography`). |

`etimer sync` prints the current status, relay and device id.

## How merging works

- Every timer has a **label** and a **state** (start time, accumulated time, running/paused, target). Each part
  carries its own timestamp, and for each part the **latest writer wins**. So renaming a timer on one device and
  pausing it on the other, at the same moment, keeps *both* changes.
- Deleting a timer leaves a small **tombstone** so the deletion propagates. A change made *after* a deletion
  brings the timer back; a change made before it doesn't.
- **Each device rings for itself.** "Already rang" is tracked per device and is never synced, so the PC and the
  phone both alert you. A device that learns about an *already finished* timer stays silent for it.
- **Offline gaps stay local.** The PC's history records "power cut from 14:02 to 14:40" for its own copy; the
  phone's copy never stopped, so that gap is deliberately not sent.
- History events (starts, pauses, resumes, restarts, finishes) are merged as a de-duplicated union.

## Protocol reference

For contributors; also see [ARCHITECTURE.md](ARCHITECTURE.md).

```
secret   = 160 random bits, shown as 8 groups of 4 base32 characters (A-Z, 2-7)
topic    = "etimer-" + hex(HMAC-SHA256(secret, "eternal-timer/topic/v1"))[:40]
key      = HMAC-SHA256(secret, "eternal-timer/key/v1")                     (32 bytes)
message  = base64url_nopad( nonce(12) || AES-256-GCM(key, nonce, json, aad="eternal-timer-v1") )

publish  : POST   {server}/{topic}                      body = message
poll     : GET    {server}/{topic}/json?poll=1&since={id|all}
live     : GET    {server}/{topic}/sse?since={id}       (Server-Sent Events)

payloads (before encryption), all with "v":1 and "dev":"<device id>":
  {"t":"item","item":{id,label,mode,start_ts,accumulated,target_seconds,running,created_at,
                      rev,updated_at,updated_by,state_at,label_at,history:[...]}}
  {"t":"tomb","tomb":{id,rev,updated_at,updated_by}}
  {"t":"hello"}                                    # "please resend everything"
```

`tests/vectors.json` pins the key derivation and ciphertext format; both implementations must reproduce it
byte for byte.
