# Gokaskad - Port Forwarding Tool

## Description

Gokaskad is a comprehensive bash script for configuring port forwarding (cascading/proxy) on Linux servers. It creates network bridges between clients and destination servers using iptables NAT rules. The script is designed for system administrators who need to set up transparent proxying or traffic redirection through a middle server.

## How It Works

The script configures iptables rules to redirect incoming traffic from specified ports on the local server to destination servers. When a client connects to the local server on a configured port, the traffic is transparently forwarded to the target server using network address translation (NAT).

### Key iptables Features Used:

1. **DNAT (Destination NAT)** - `iptables -t nat -A PREROUTING`
   - Redirects incoming packets to different destination IP addresses and ports
   - Used in the PREROUTING chain to modify packets before routing decisions

2. **MASQUERADE** - `iptables -t nat -A POSTROUTING`
   - Dynamically changes source IP addresses of outgoing packets
   - Essential for proper return traffic routing through the forwarding server

3. **FORWARD Chain Rules**
   - `-A FORWARD -d TARGET_IP --dport PORT -j ACCEPT` - Allows forwarding to destination
   - `-A FORWARD -s TARGET_IP --sport PORT -j ACCEPT` - Allows return traffic from destination
   - Uses connection tracking (`-m state --state NEW,ESTABLISHED,RELATED`) for stateful inspection

4. **INPUT Chain Rules**
   - Opens specified ports on the local server to accept incoming connections

### Technical Workflow:

1. **Packet Reception**: Client connects to local server on configured input port
2. **DNAT Processing**: PREROUTING chain changes destination IP/port to target server
3. **Routing Decision**: Kernel determines packet needs forwarding (different destination)
4. **FORWARD Chain**: Packet passes through FORWARD chain with state tracking
5. **MASQUERADE**: POSTROUTING chain changes source IP to local server's interface IP
6. **Return Traffic**: Response packets go through reverse process with state tracking

### Example iptables Rules Generated:

```bash
# Accept incoming connections on port 8080
iptables -A INPUT -p tcp --dport 8080 -j ACCEPT

# Redirect traffic to destination server
iptables -t nat -A PREROUTING -p tcp --dport 8080 -j DNAT --to-destination 192.168.1.100:8080

# Enable forwarding with state tracking
iptables -A FORWARD -p tcp -d 192.168.1.100 --dport 8080 -m state --state NEW,ESTABLISHED,RELATED -j ACCEPT
iptables -A FORWARD -p tcp -s 192.168.1.100 --sport 8080 -m state --state ESTABLISHED,RELATED -j ACCEPT

# Source NAT for return traffic
iptables -t nat -A POSTROUTING -o eth0 -j MASQUERADE
```

## Features

- **Unified rule creation** - Single interface for all protocols (TCP/UDP)
- **Flexible port configuration** - Same or different input/output ports
- **Configuration persistence** - Save/restore rules to/from `/etc/gokaskad/rules.conf`
- **Autostart support** - Systemd service for automatic rule restoration on boot
- **Rule management** - List, delete, and flush rules
- **System diagnostics** - Check BBR status, IP forwarding, active rules
- **Backup functionality** - Automatic backup before flushing rules
- **Logging** - All actions logged to `/var/log/gokaskad.log`
- **Color output** - Clear visual feedback (can be disabled if needed)

## Supported Linux Distributions

### Fully Tested and Supported:

| Distribution | Versions | Package Manager | Persistence Method |
|--------------|----------|----------------|-------------------|
| **Ubuntu** | 18.04, 20.04, 22.04, 24.04 | apt-get | netfilter-persistent |
| **Debian** | 10, 11, 12 | apt-get | netfilter-persistent |
| **CentOS** | 7, 8, 9 | yum/dnf | iptables-services |
| **RHEL** | 7, 8, 9 | yum/dnf | iptables-services |
| **Rocky Linux** | 8, 9 | dnf | iptables-services |
| **AlmaLinux** | 8, 9 | dnf | iptables-services |
| **Fedora** | 35+ | dnf | iptables-services |

### Requirements:

- Linux kernel 3.10+ (for BBR support)
- iptables (nf_tables or legacy)
- systemd (for autostart feature)
- bash 4.0+
- root privileges

### Optional Dependencies:

- `ufw` - Will auto-configure if detected
- `curl` - For external IP detection in diagnostics
- `netfilter-persistent` (Debian/Ubuntu) - For rule persistence
- `iptables-services` (RHEL/CentOS) - For rule persistence

## Installation

```bash
# Download or create the script
sudo nano /usr/local/bin/gokaskad

# Make executable
sudo chmod +x /usr/local/bin/gokaskad

# Run
sudo gokaskad
```

## Command Line Options

```bash
gokaskad           # Interactive menu mode
gokaskad --save    # Save current iptables rules to config
gokaskad --restore # Restore rules from config file
gokaskad --flush   # Flush all iptables rules (silent mode)
gokaskad --help    # Show help message
```

## Use Cases

1. **VPN Cascading** - Forward WireGuard/OpenVPN traffic through a middle server
2. **Proxy Chains** - Create multi-hop proxy configurations
3. **Port Redirection** - Redirect services to different internal servers
4. **Geo-bypassing** - Route traffic through servers in different locations
5. **Load Distribution** - Forward different ports to different backend servers
6. **SSH Tunneling** - Create SSH jump hosts with port forwarding

## Network Requirements

- IPv4 forwarding must be enabled (script enables it automatically)
- No conflicting iptables rules on the same ports
- Destination server must be reachable from the local server
- Return traffic must be able to route back through the local server

## Notes

- The script automatically enables Google BBR congestion control
- IP forwarding is enabled in `/etc/sysctl.conf`
- Rules persist across reboots when autostart is installed
- Configuration is stored in `/etc/gokaskad/rules.conf`
- All actions are logged to `/var/log/gokaskad.log`
- The script creates a global command `gokaskad` in `/usr/local/bin/`

## Troubleshooting

1. **Rules not saving**: Check `/etc/gokaskad/rules.conf` exists and is writable
2. **Forwarding not working**: Verify `net.ipv4.ip_forward=1` with `sysctl net.ipv4.ip_forward`
3. **No internet after rules**: Check MASQUERADE rule exists for your interface
4. **Service not starting**: Run `journalctl -u gokaskad` to see errors
5. **Docker conflicts**: Docker manipulates iptables, may need additional configuration

## Security Considerations

- Opening ports on your server increases attack surface
- Use firewall (UFW/iptables) to restrict source IPs if needed
- Consider rate limiting for public-facing ports
- Regularly audit forwarding rules with `gokaskad` menu option 2
- Keep the script and system updated

## License

Free to use and modify for personal and commercial use.
