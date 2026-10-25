# Security

## What is protected
Sync messages are encrypted and authenticated with AES-256-GCM. The key and the relay topic are derived from
the pairing code with HMAC-SHA256, and the code has 160 bits of randomness. Someone who does not have the code
cannot find your topic, read your timers, or inject changes that your devices will accept.

## What is not
- Anyone **with** the pairing code can read and change your timers. Treat it like a password. There is no way to
  revoke a single device; to rotate, turn sync off on every device and pair again with a new code.
- No forward secrecy: if the code leaks later, previously captured traffic (which the relay may have cached)
  could be decrypted.
- The relay (and anyone observing the network) can see metadata: your IP address, when messages are sent, and
  their approximate size.
- The pairing code is stored in plain text in `~/.local/share/eternal-timer/settings.json` (permissions 0600)
  on the PC, and in the app's private preferences on Android.
- Timestamps are trusted: a device with a badly wrong clock can win conflicts it shouldn't.

## Reporting a vulnerability
Please use GitHub's **private vulnerability reporting** (the repository's *Security* tab → *Report a
vulnerability*) rather than a public issue. Include steps to reproduce and the affected version.
