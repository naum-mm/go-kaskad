# Installer menu, modes and certificates

Run one command for installation, updates, migrations and removal:

```bash
curl --proto '=https' --tlsv1.2 -fsSL https://raw.githubusercontent.com/naum-mm/go-kaskad/main/install.sh | sudo bash
```

Before changing anything, the menu shows the existing installation, current mode
and domain when recorded, running web services, Docker containers and occupied
TCP/UDP ports. Exiting the menu does not install dependencies or download code.

## Menu

1. **Separate port:** install on a free HTTPS port. Choose self-signed or Certbot.
   The default port is 8443 for self-signed, 443 for Certbot. A standalone Certbot
   installation requires a domain and free, publicly reachable TCP 80 for HTTP-01.
2. **Existing nginx / SNI:** add a domain on the shared public TCP 443. Choose
   self-signed or Certbot. Both require a domain for SNI routing. The internal
   panel port is selected automatically and binds only to loopback.
3. **Existing installation:** update code, reinstall/recreate containers while
   preserving data, or update and change mode, port and certificate type.
4. **Remove:** keep settings and rules for restoration, or fully remove the project.
   Full removal requires typing `DELETE`.

Only a fresh installation requests a password. Existing passwords and rules are
preserved during updates, migration and restoration. Port conflicts—including
active forwarding rules—are rejected rather than stopping other services.

## Existing nginx requirements

Supported: host nginx with `include /etc/nginx/conf.d/*.conf`, or Docker nginx
with the same include, bind-mounted `conf.d` directory or one loaded `.conf`,
bind-mounted ACME webroot and `/etc/letsencrypt:/etc/letsencrypt`, published ports
80/443 and a custom network. Unknown layouts, other reverse proxies and conflicting
virtual hosts are not overwritten. External HAProxy detection is not supported;
HAProxy is not installed by this menu. MASQUE is a separate service.

Only the Gokaskad nginx joins the existing website network. API stays in `control`
and SQLite has no network port. The existing nginx selects a certificate using
TLS SNI, then routes requests by domain. Original sites keep their configuration.

## Certificates

- **Self-signed:** verify the printed fingerprint and explicitly trust it in the
  browser. In SNI mode the outer nginx trusts that specific certificate for the
  upstream, with hostname verification enabled. Certificates last 365 days;
  use menu option 3 / mode change to rotate a certificate nearing expiration.
- **Certbot:** correct public DNS A/AAAA and reachable TCP 80 are required.
  Certbot runs on the host, so deploy hooks can access the filesystem and Docker.
  Terms are accepted and new accounts registered without email to avoid extra
  prompts. Existing host renewal timers are reused; otherwise `certbot.timer`
  is enabled. A project deploy hook refreshes the panel certificate and reloads
  nginx. Other hooks and existing certificates are retained.

Separate-port Certbot uses the standalone HTTP-01 plugin. SNI Certbot uses the
existing nginx webroot. A plugin/webroot left over from a different mode is
reconfigured and checked before reporting success.

Upstream TLS verifies the hostname and CA chain. `proxy_ssl_verify_depth 3`
supports multiple intermediate CAs. Self-signed upstream certificates are pinned
as trust anchors; verification is never disabled to hide a 502 error.

## Updates, migration and failure recovery

The mode-change action upgrades old code automatically, then changes settings.
The menu can migrate a standalone self-signed panel on 8443 to an SNI domain on
443 with Certbot, or move it back to a free standalone port. A public port already
owned by another website is never taken over.

Before update/migration, configuration, password hash, certificates and a
consistent SQLite snapshot are backed up under `/root/gokaskad-backups`.
Database volumes keep their names. Failed updates rebuild the previous release;
failed mode changes attempt to restore the previous configuration. A failed
rollback is reported explicitly; the backup remains available. Brief forwarding
interruptions are possible while containers restart.

Only the project's marked nginx block is changed. Single-file bind mounts are
edited in place, preserving their inode. Syntax validation precedes reload;
HTTPS/authentication checks retry while asynchronous reload completes. Child
commands have isolated stdin so Docker cannot consume piped installer commands.

Older manual proxy connections have no ownership manifest. Ordinary updates
preserve them, but automated mode switching stops for manual review.

## Removal and restoration

**Keep settings:** stops/removes containers and project firewall chains, detaches
the domain and renewal hook, but keeps `/opt/go-kaskad/config`, saved settings,
certificates and database volumes. A portable SQLite snapshot is also saved.
Install again from menu option 1 or 2 to restore the password and rules; the chosen
mode can differ. Do not prune the retained database volume before restoration.

**Full removal:** removes project containers, volumes, config/code, owned domain
blocks/hooks, unused project-created certificates and introduced unused packages.
Certificates referenced by other nginx blocks remain. Shared Docker and packages
used by other projects remain. Older installs without package manifests do not
trigger guessed package removal. Backups in `/root/gokaskad-backups` are retained
separately and can be removed manually when no longer needed.

## Automation and legacy commands

The same script accepts `--update`, `--reinstall`, `--change-mode`, `--keep-config`,
`--purge` and `--menu`. `--change-mode` asks only for the new mode/certificate/domain
or port. Deletion still requests confirmation; `GOKASKAD_CONFIRM=YES` supplies it
explicitly for automation.

For unattended new installations set `GOKASKAD_ACTION=install`,
`INSTALL_MODE=standalone|proxy`, `CERT_TYPE=selfsigned|certbot`, `PANEL_DOMAIN`
when required, `MANAGEMENT_PORT` if needed and `ADMIN_PASSWORD`. The password is
handed to the child installer using a temporary root-only file, never argv or `.env`.

Legacy `--configure-domain` and `--resume-domain` commands remain available for
existing installations and unfinished older domain setup. They use Certbot.
The menu's mode-change action replaces the old manual update-then-configure flow.
