# Contributing

Thanks for your interest in improving warp-split.

## Project structure

```
warp-split
├── warp-split            Main CLI script (bash)
├── apply-override        Daemon script — polls and re-applies the DNS override
├── config.example        Example configuration (tracked in git)
├── config                User's actual config (gitignored)
├── README.md             User-facing documentation
├── CONTRIBUTING.md       This file
└── LICENSE               MIT
```

At runtime, `warp-split setup` generates two additional artifacts:

- `/opt/homebrew/etc/dnsmasq.d/split-dns.conf` — dnsmasq configuration, generated from the user's `config` file
- `/Library/LaunchDaemons/com.local.split-dns.plist` — LaunchDaemon that runs `apply-override` at boot

## Architecture

The solution has three moving parts:

### 1. dnsmasq (DNS forwarder)

Installed via Homebrew. Listens on `127.0.0.1:53` and conditionally forwards:
- Configured internal domains → `127.0.2.2` and `127.0.2.3` (WARP's DNS proxies)
- Everything else → public DNS servers

### 2. SupplementalMatchDomains override

A `State:/Network/Service/CustomSplitDNS/DNS` entry in the macOS dynamic store (`scutil`). This tells `mDNSResponder` to prefer our dnsmasq at order 103800 over WARP's resolver at 200000.

WARP watches and re-applies `Setup:` keys but ignores custom `State:` supplemental services — this is the key insight that makes the whole approach work.

The same daemon can also run an optional WARP DNS health check. When `WARP_DNS_HEALTHCHECK_DOMAIN` is configured, it waits for dnsmasq and the split path to answer before applying the macOS resolver override. It also queries `127.0.2.2` directly and restarts the WARP daemon after repeated failures. This catches cases where macOS and dnsmasq are healthy but WARP's local DNS proxy starts returning errors for private records.

### 3. /etc/hosts entry

`connectivity-check.warp-svc` → `127.0.2.2`. WARP's daemon resolves this synthetic hostname via `getaddrinfo()` for its connectivity health check. Since `warp-svc` is a fake TLD, `mDNSResponder` won't resolve it through DNS — it only works via `/etc/hosts`.

## How to test changes

Since this modifies system-level DNS, testing requires care:

1. **Always test on a machine you can recover** — if DNS breaks, you lose name resolution until you fix it.

2. **Quick recovery**: if something goes wrong:
   ```bash
   sudo ./warp-split disable
   sudo dscacheutil -flushcache
   ```
   This restores WARP-only DNS immediately.

3. **Verify after changes**:
   ```bash
   # Check all components are healthy
   ./warp-split status

   # Verify external DNS bypasses WARP
   dig google.com @127.0.0.1 +short

   # Verify internal DNS goes through WARP
   dig your.internal.domain @127.0.0.1 +short

   # Check WARP is still connected
   warp-cli status

   # If configured, verify WARP DNS health appears in status
   ./warp-split status

   # Tail the query log to see routing decisions
   ./warp-split logs
   ```

4. **Resilience test**: restart the WARP daemon and verify split DNS survives:
   ```bash
   sudo launchctl kickstart -k system/com.cloudflare.1dot1dot1dot1.macos.warp.daemon
   sleep 15
   warp-cli status   # should be "Connected"
   ```

## Development guidelines

- **No PII or organization-specific data** in tracked files. All org-specific config goes in `config` (gitignored).
- **Bash only** — no Python/Ruby/Node dependencies. The scripts should work on a fresh macOS with just Homebrew.
- `warp-split` uses `set -euo pipefail` — fail fast on errors.
- Test on both Apple Silicon (`/opt/homebrew`) and Intel (`/usr/local`) Homebrew paths if possible.
- The `apply-override` daemon must stay lean — it runs continuously as root.

## macOS version notes

- Tested on macOS Sonoma (14.x) and Sequoia (15.x).
- The `SupplementalMatchDomains` mechanism has been stable since at least macOS Monterey.
- `scutil --dns` is the canonical way to verify resolver state.
- `/etc/hosts` may have `schg` (system immutable) and `uchg` flags — the scripts handle this via `chflags`.

## Cloudflare WARP internals (context)

Useful background if you're modifying the override logic:

- WARP daemon: `/Applications/Cloudflare WARP.app/Contents/Resources/CloudflareWARP`
- CLI: `/usr/local/bin/warp-cli`
- Config (server-fetched): `/Library/Application Support/Cloudflare/conf.json`
- Local prefs: `/Library/Managed Preferences/com.cloudflare.warp.plist`
- Daemon log: `/Library/Application Support/Cloudflare/cfwarp_service_log.txt`
- WARP binds DNS to `127.0.2.2:53` and `127.0.2.3:53`
- WARP sets `Setup:/Network/Service/<UUID>/DNS` in the dynamic store — it re-applies this on reconnects (~1 second after changes)
- WARP does NOT touch `State:` supplemental DNS entries (this is what we exploit)
- `(network policy)` tagged settings in `warp-cli settings` are server-enforced and cannot be overridden locally
