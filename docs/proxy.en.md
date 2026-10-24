# Install with an existing nginx

Use the one-command installer from the README. It detects a host nginx or Docker
nginx already serving HTTPS and asks only for the panel domain and admin password.
Point the domain's DNS to this VPS first; public TCP ports 80/443 must be reachable.

The installer adds an nginx virtual host. TLS SNI selects its certificate, then
nginx routes requests to the matching domain. Existing sites keep their virtual
hosts. A separate HAProxy is not installed. MASQUE installation is outside this mode.

## Requirements

- Host nginx must include `/etc/nginx/conf.d/*.conf` and listen on ports 80/443.
- Docker nginx must have the same include, bind-mounted `conf.d` directory or a
  single loaded `.conf`, an existing ACME webroot, a bind mount of
  `/etc/letsencrypt:/etc/letsencrypt`, published TCP 80/443 and a custom Docker network.
- Both host and Docker nginx must have a system CA bundle at
  `/etc/ssl/certs/ca-certificates.crt` for verified upstream TLS.

Unknown proxies, conflicting domains, ambiguous layouts and regex server names
stop with an explanation. An existing HAProxy in front of nginx is not detected
automatically. Use `INSTALL_MODE=standalone` for a separate panel port.

## Certificates and isolation

Certbot runs on the **host**, so existing deploy hooks can access their projects
and Docker. To avoid extra prompts, the installer accepts the Let's Encrypt terms
and registers new accounts without email. HTTP-01 validates your domain publicly.
An older certificate's container-only renewal webroot is corrected when needed.
The existing Certbot timer is reused; otherwise `certbot.timer` is enabled.

A dedicated deploy hook copies the domain certificate to the panel's TLS directory
and reloads both nginx instances. Upstream TLS verifies the domain and CA chain;
it does not use `--insecure` or disable certificate verification.
`proxy_ssl_verify_depth 3` supports chains with multiple intermediate CAs.

Only the Gokaskad nginx joins the existing website's Docker network. The panel's
host port binds to loopback, while the API remains in the internal `control`
network. SQLite has no network port. TCP ports 80/443 are reserved in forwarding rules.

## Failure recovery and updates

Installer, helper and uninstall subprocesses do not inherit the script input stream:
Docker cannot consume the rest of a `curl | bash` or heredoc script. In manual
update heredocs, redirect non-interactive Docker commands with `</dev/null`.

The installer backs up the proxy configuration and adds a marked block. Existing
single-file bind mounts are edited in place, preserving the inode. Failed nginx
validation/reload restores the previous file. TLS/authentication checks retry
while asynchronous reload completes, including a transient old certificate.

If DNS or certificate issuance fails, correct the cause and resume without
resetting the password or database:

```bash
curl --proto '=https' --tlsv1.2 -fsSL https://raw.githubusercontent.com/naum-mm/go-kaskad/main/install.sh | sudo bash -s -- --resume-domain
```

To add a domain to an existing local installation, first update the project, then:

```bash
curl --proto '=https' --tlsv1.2 -fsSL https://raw.githubusercontent.com/naum-mm/go-kaskad/main/install.sh | sudo bash -s -- --configure-domain
```

Only the domain is requested. Existing TCP forwarding rules on ports 80/443 must
be disabled first. A domain already connected manually does not need this command;
the installer will not replace its existing virtual host.

Integration state and Compose overrides live in `/opt/go-kaskad/config`.
`COMPOSE_FILE` in `.env` references the persistent override. Preserve `.env` when
updating releases, and refresh the helper used by certificate hooks:

```bash
# Run after downloading the new release, before switching current:
# install -m 600 "$release/tools/proxy_setup.py" /opt/go-kaskad/config/proxy_setup.py
```

Uninstallation removes only the project's marked nginx block and renewal hook.
Later unrelated nginx edits, shared networks and the Certbot timer remain.
A newly created certificate is removed only if no other nginx block references it;
pre-existing certificates are retained. A manually modified managed block stops
uninstallation for review. These guarantees apply to automatic integration;
older manual connections have no integration manifest.

For automation: `INSTALL_MODE=auto|standalone|proxy`, `PANEL_DOMAIN`,
`ADMIN_PASSWORD`, and optional `MANAGEMENT_PORT`. Manual loopback installation
without automatic proxy changes is still available:

```bash
curl --proto '=https' --tlsv1.2 -fsSL https://raw.githubusercontent.com/naum-mm/go-kaskad/main/install.sh -o /tmp/gokaskad-install.sh
sudo env INSTALL_MODE=standalone MANAGEMENT_BIND=127.0.0.1 MANAGEMENT_PORT=10443 RESERVED_TCP_PORTS=80,443 bash /tmp/gokaskad-install.sh
```
