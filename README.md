# Fracture

[![Release](https://img.shields.io/github/v/release/mborjian/Fracture?label=GitHub%20Release)](https://github.com/mborjian/Fracture/releases) [![Stars](https://img.shields.io/github/stars/mborjian/Fracture?style=social)](https://github.com/mborjian/Fracture/stargazers) [![Issues](https://img.shields.io/github/issues/mborjian/Fracture)](https://github.com/mborjian/Fracture/issues) [![License](https://img.shields.io/github/license/mborjian/Fracture)](LICENSE)

English | [فارسی](README.fa.md)

Fracture is a Windows desktop control panel designed for easy management of `sing-box` profiles, local proxy runtime, and Cloudflare listener settings.

It brings the full workflow into one app, with profile import, runtime control, diagnostics, and local configuration stored safely on your machine.

## What Fracture Does

- Import proxy profiles from subscription links, URI strings, or text files
- Organize profiles with rename, reorder, export, delete, and active-profile selection
- Run ping and speed tests before connecting to verify performance
- Edit Cloudflare listener JSON directly inside the UI
- Control local runtime status, proxy ports, routing mode, LAN sharing, and startup behavior
- Monitor live status, logs, egress information, and traffic counters in real time

## Supported Profile Types

Fracture supports the profile formats used by its parser and runtime pipeline, including:

- `vless://`
- `vmess://`
- `trojan://`
- `ss://`
- `socks://`
- `http://`
- `hysteria2://`
- `tuic://`
- `wireguard://`
- `naive+https://`
- `naive+quic://`
- `anytls://`

## Quick Start

1. Open Fracture.
2. Import or paste your profiles.
3. Optionally run ping or speed tests.
4. Select an active profile.
5. Start the runtime from the Dashboard.

## Project Structure

- `apps/desktop` — Tauri + React desktop application
- `apps/backend` — FastAPI service for profile storage, runtime control, and realtime updates
- `sing-box/` — bundled `sing-box` runtime assets and helper files
- `configs/` — generated runtime folders and log output
- `data/` — local settings, profile storage, and Cloudflare listener state

## Local Data

Fracture stores runtime state and configuration locally in files such as:

- `data/profiles.json`
- `data/cloudflare-config.json`
- `data/app-settings.json`
- `configs/`

These files remain on the local machine and are not shared by default.

## Development

Run the backend and desktop app together during development:

```powershell
npm run run
```

Build a packaged release for distribution:

```powershell
npm run build:release
```

Prepare the next GitHub release from the latest `v*` tag:

```powershell
npm run release:prepare
```

That command:
- infers the next semantic version from commits since the latest version tag
- updates the app version across the tracked manifest/config files
- creates `.github/releases/<version>.md` as the curated release notes file
- creates `.release-tmp/release-notes-input-<version>.md` with a hardcoded AI prompt, commit history, and full diff context

After you paste the final release notes into the generated `.github/releases/<version>.md`, finalize and publish:

```powershell
npm run release:finalize
```

That command creates the release commit, tags `v<version>`, pushes the branch and tag, and lets the GitHub release workflow publish the release assets.

## Support And References

- GitHub repository: [mborjian/Fracture](https://github.com/mborjian/Fracture)
- Releases: [Download stable builds](https://github.com/mborjian/Fracture/releases)
- Issues: [Report a problem](https://github.com/mborjian/Fracture/issues/new/choose)
- Star: [Show support](https://github.com/mborjian/Fracture/stargazers)
- Fork: [Contribute](https://github.com/mborjian/Fracture/fork)
- Watch: [Follow updates](https://github.com/mborjian/Fracture/subscription)

Donation Wallets to Support the Developer:

- TON: `UQATECPeh89wITfWeFkUuO0o30Gup5QhmDlx9KWYNz54VCjN`
- USDT TRC20: `TF24QUZmpznKqrnjN6GhawW45Nx1DBALtR`
- TRX TRC20: `TF24QUZmpznKqrnjN6GhawW45Nx1DBALtR`
- SOL Solana: `5dGZcqQGECrczAtqfhrMn4A8VHLKR3qNx5Jaq8vAamyr`

## Acknowledgements

Fracture builds on ideas and open-source work from the community.

- [g3ntrix/Cloak](https://github.com/g3ntrix/Cloak)
- [patterniha/SNI-Spoofing](https://github.com/patterniha/SNI-Spoofing)

## License

This project is licensed under the terms of the [LICENSE](LICENSE) file.
