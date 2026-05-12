# warp-split

Split DNS for Cloudflare WARP on macOS. Keep WARP connected for internal resources while routing all other DNS queries directly to public resolvers, bypassing Cloudflare Gateway.

## Why

When your organization deploys Cloudflare WARP in **"WARP with DoH"** mode, _every_ DNS query goes through Cloudflare Gateway — even if IP traffic is already split-tunneled. That means your employer's Gateway dashboard can see every domain you visit, and Gateway policies can block sites, even for personal browsing on a BYOD.

**warp-split** fixes this by inserting a local DNS forwarder (dnsmasq) between your system and WARP's DNS proxy. Internal domains keep going through WARP. Everything else goes straight to the public DNS of your choice.

WARP stays connected, your organization's internal resources keep working, and your non-work DNS is private.

## How it works

```
 Application (browser, CLI, …)
           │
           ▼
    mDNSResponder            ← macOS system resolver
           │
           │  Supplemental match (priority 103800)
           ▼
        dnsmasq              ← 127.0.0.1:53
        ╱      ╲
       ╱        ╲
Internal      External
domains       domains
   │              │
   ▼              ▼
WARP proxy     1.1.1.1
127.0.2.2      8.8.8.8
   │          (direct)
   │
   ▼
Cloudflare
 Gateway
(corporate)
```

1. **dnsmasq** listens on `127.0.0.1:53`. WARP's proxy lives on `127.0.2.2:53` — no port conflict.
2. A **SupplementalMatchDomains** entry in the macOS dynamic store tells `mDNSResponder` to send all queries to dnsmasq first (order 103800), before WARP's resolver (order 200000).
3. dnsmasq **forwards** queries for your internal domains to WARP's DNS proxy, and everything else to public DNS.
4. A small **LaunchDaemon** waits for the local DNS path to answer, then re-applies the override every 5 seconds in case macOS clears it (sleep/wake, network changes, WARP reconnects). If configured, it also checks WARP's DNS proxy and restarts WARP after repeated proxy failures.
5. An `/etc/hosts` entry for `connectivity-check.warp-svc` keeps WARP's internal health check working — it uses `getaddrinfo()` which can't resolve this synthetic hostname through DNS, only through `/etc/hosts`.

### Why WARP doesn't fight it

WARP monitors and re-applies its own `Setup:/Network/Service/…/DNS` key in the dynamic store. It does **not** touch custom `State:` supplemental services. So WARP keeps thinking it owns DNS, stays connected, and reports healthy.

## Requirements

- macOS (tested on Sonoma / Sequoia, Apple Silicon and Intel)
- [Homebrew](https://brew.sh)
- Cloudflare WARP installed and enrolled in a Zero Trust organization
- `sudo` access

## Quick start

```bash
git clone https://github.com/Maxim-Mazurok/cf-warp-split.git
cd cf-warp-split

# 1. Create your config
cp config.example config
$EDITOR config        # add your internal domains

# 2. Install and enable
sudo ./warp-split setup

# 3. Verify
./warp-split status
```

That's it. Your external DNS now bypasses Cloudflare Gateway.

## Commands

| Command | Description |
|---|---|
| `sudo ./warp-split setup` | Install dnsmasq, generate configs, start everything |
| `sudo ./warp-split teardown` | Stop everything, remove configs (dnsmasq left installed) |
| `sudo ./warp-split enable` | Start services (after a previous `disable`) |
| `sudo ./warp-split disable` | Stop services, restore WARP-only DNS |
| `./warp-split status` | Show state of all components |
| `sudo ./warp-split generate` | Regenerate dnsmasq config after editing `config` |
| `./warp-split logs` | Tail the dnsmasq query log |

## Configuration

All settings live in the `config` file (not tracked by git). Copy `config.example` to get started.

### Internal domains

```bash
INTERNAL_DOMAINS=(
    "internal.example.com"
    "vpn.example.com"
    "yourorg.cloudflareaccess.com"
)
```

These are forwarded to WARP's DNS proxy (`127.0.2.2`) for corporate resolution. Subdomains are matched automatically — `example.com` covers `anything.example.com`.

### Public DNS

```bash
PUBLIC_DNS_SERVERS=(
    "1.1.1.1"
    "1.0.0.1"
    "8.8.8.8"
    "8.8.4.4"
)
```

Change these to whatever you prefer (AdGuard, Quad9, your own resolver, etc.).

### Fallback TLDs

The config includes standard private-use TLDs (`.corp`, `.internal`, `.lan`, etc.) that are always routed through WARP. You generally don't need to change these.

### Logging

Set `ENABLE_LOGGING=true` in your config to log all DNS queries to `$HOMEBREW_PREFIX/var/log/dnsmasq.log`. Useful for debugging. Disable once verified.

### WARP DNS health check

Set `WARP_DNS_HEALTHCHECK_DOMAIN` to a hostname that must resolve through WARP DNS:

```bash
WARP_DNS_HEALTHCHECK_DOMAIN="known.internal.example.com"
WARP_DNS_HEALTHCHECK_INTERVAL_SECONDS=30
WARP_DNS_HEALTHCHECK_FAILURE_THRESHOLD=3
WARP_DNS_HEALTHCHECK_RECOVERY_COOLDOWN_SECONDS=$((5 * 60))
```

The override daemon waits for dnsmasq and this hostname to resolve through the split path before it points macOS at dnsmasq. It also queries WARP's local DNS proxy directly. After repeated failures it restarts the WARP daemon, flushes the macOS DNS cache, waits for the split path to recover, and reapplies the resolver override.

## Updating internal domains

1. Edit your `config` file
2. Run `sudo ./warp-split generate`

This regenerates the dnsmasq config and restarts dnsmasq.

## Troubleshooting

### WARP shows "Unable" / "Connectivity check failed"

The `connectivity-check.warp-svc` hostname needs to resolve via `/etc/hosts`. Run `sudo ./warp-split setup` again to re-add it, or check:

```bash
grep warp-svc /etc/hosts
```

### External sites not loading

Check if dnsmasq is running:

```bash
./warp-split status
```

If dnsmasq is stopped, start it:

```bash
sudo ./warp-split enable
```

### Internal sites not loading

Make sure the domain is in your `config` file and regenerate:

```bash
sudo ./warp-split generate
```

If the domain is configured but still fails, compare dnsmasq and WARP directly:

```bash
dig your.internal.domain @127.0.0.1
dig your.internal.domain @127.0.2.2
```

If dnsmasq logs `reply error is SERVFAIL` for the A query, the request reached the split resolver and WARP's DNS proxy returned an error. Configure `WARP_DNS_HEALTHCHECK_DOMAIN` so the daemon can detect repeated failures and restart WARP automatically.

If retry-proxy logs exhausted attempts for an internal `AAAA` query, it returns an empty IPv6 answer instead of dropping the request. Internal WTG hosts observed so far only return A records, and dropping the DNS packet makes macOS wait on resolver timeouts even when the A record is already available.

### Verifying the split

Use `dig` to confirm routing:

```bash
# Should go to public DNS (1.1.1.1 etc.)
dig google.com @127.0.0.1 +short

# Should go to WARP proxy (127.0.2.2)
dig your.internal.domain @127.0.0.1 +short
```

Or check the dnsmasq log:

```bash
./warp-split logs
```

## Uninstalling

```bash
sudo ./warp-split teardown
brew uninstall dnsmasq    # optional
```

## License

MIT
