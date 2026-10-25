#!/bin/sh
#
# gokaskad – Port forwarding management for OpenBSD / PF
# Analog of the Linux iptables-based script, but using pf(4) and pfctl(8)
#
# Usage: gokaskad [--restore|--save|--flush|--help]

# --- COLORS (if terminal supports) ---
if [ -t 1 ]; then
    RED='\033[0;31m'
    GREEN='\033[0;32m'
    CYAN='\033[0;36m'
    YELLOW='\033[1;33m'
    MAGENTA='\033[0;35m'
    WHITE='\033[1;37m'
    BLUE='\033[0;34m'
    NC='\033[0m'
else
    RED=''; GREEN=''; CYAN=''; YELLOW=''; MAGENTA=''; WHITE=''; BLUE=''; NC=''
fi

# --- CONFIGURATION ---
CONFIG_DIR="/etc/gokaskad"
CONFIG_FILE="$CONFIG_DIR/rules.conf"
LOG_FILE="/var/log/gokaskad.log"
PF_ANCHOR="gokaskad"                     # anchor name inside pf
TMP_PF_RULES="/tmp/gokaskad_pf_rules"    # temporary file for anchor rules

# Determine default external interface (where default route points)
EXT_IF=$(route -n get default 2>/dev/null | awk '/interface:/ {print $2}')
[ -z "$EXT_IF" ] && EXT_IF="em0"         # fallback

# --- HELPER FUNCTIONS ---

log_action() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" >> "$LOG_FILE"
}

check_root() {
    if [ "$(id -u)" -ne 0 ]; then
        echo -e "${RED}[ERROR] Run script with root privileges!${NC}"
        exit 1
    fi
}

validate_ip() {
    local ip="$1"
    case "$ip" in
        ''|*[!0-9.]*) return 1 ;;
    esac
    local IFS='.'
    set -- $ip
    [ $# -eq 4 ] && [ "$1" -le 255 ] && [ "$2" -le 255 ] && [ "$3" -le 255 ] && [ "$4" -le 255 ]
}

validate_port() {
    local port="$1"
    [ "$port" -ge 1 ] 2>/dev/null && [ "$port" -le 65535 ] 2>/dev/null
}

backup_pf() {
    local backup_file="$CONFIG_DIR/pf-backup-$(date +%Y%m%d-%H%M%S).conf"
    mkdir -p "$CONFIG_DIR"
    pfctl -sr > "$backup_file"
    log_action "Backup of main pf rules saved: $backup_file"
    echo -e "${GREEN}[OK] Backup saved: $backup_file${NC}"
}

# Reload anchor rules from current CONFIG_FILE
reload_pf_anchor() {
    # Generate pf rules from config file
    > "$TMP_PF_RULES"
    if [ -f "$CONFIG_FILE" ] && [ -s "$CONFIG_FILE" ]; then
        while IFS=':' read -r proto in_port target_ip target_port; do
            # skip comments and empty lines
            case "$proto" in ''|\#*) continue ;; esac
            # Basic sanity
            validate_ip "$target_ip" || continue
            validate_port "$in_port" || continue
            validate_port "$target_port" || continue
            # Write rule: rdr pass on $EXT_IF proto $proto from any to ($EXT_IF) port $in_port -> $target_ip port $target_port
            # Using 'rdr pass' automatically allows the traffic
            echo "rdr pass on $EXT_IF proto $proto from any to ($EXT_IF) port $in_port -> $target_ip port $target_port" >> "$TMP_PF_RULES"
        done < "$CONFIG_FILE"
    fi
    
    # Load into pf anchor (atomic replace)
    pfctl -a "$PF_ANCHOR" -f "$TMP_PF_RULES" 2>/dev/null
    if [ $? -eq 0 ]; then
        log_action "Reloaded PF anchor $PF_ANCHOR with $(grep -c '^rdr' "$TMP_PF_RULES" 2>/dev/null || echo 0) rules"
        return 0
    else
        log_action "ERROR: failed to load PF anchor rules"
        return 1
    fi
}

# Save current active anchor rules to CONFIG_FILE
save_rules_to_config() {
    mkdir -p "$CONFIG_DIR"
    > "$CONFIG_FILE"
    
    # Get rules from pf anchor
    local saved=0
    pfctl -a "$PF_ANCHOR" -sr 2>/dev/null | grep '^rdr pass on' | while read -r line; do
        # Extract protocol, in_port, target_ip, target_port
        # Typical line: rdr pass on em0 proto tcp from any to (em0) port 8080 -> 192.168.1.10 port 80
        proto=$(echo "$line" | awk '/proto/ {for(i=1;i<=NF;i++) if($i=="proto") print $(i+1)}')
        in_port=$(echo "$line" | awk '/port/ {for(i=1;i<=NF;i++) if($i=="port") print $(i+1)}')
        target_ip=$(echo "$line" | awk -F'->' '{print $2}' | awk '{print $1}')
        target_port=$(echo "$line" | awk -F'->' '{print $2}' | awk '/port/ {for(i=1;i<=NF;i++) if($i=="port") print $(i+1)}')
        if [ -n "$proto" ] && [ -n "$in_port" ] && [ -n "$target_ip" ] && [ -n "$target_port" ]; then
            echo "$proto:$in_port:$target_ip:$target_port" >> "$CONFIG_FILE"
            saved=$((saved + 1))
        fi
    done
    
    echo -e "${GREEN}[OK] Saved $saved rule(s) to $CONFIG_FILE${NC}"
    log_action "Saved $saved rules from anchor to config"
    [ $saved -eq 0 ] && echo -e "${YELLOW}[WARN] No rdr rules found in anchor${NC}"
}

# Restore rules from config file (reload anchor)
restore_rules_from_config() {
    if [ ! -f "$CONFIG_FILE" ]; then
        echo -e "${YELLOW}[WARN] Configuration file not found${NC}"
        return 1
    fi
    if [ ! -s "$CONFIG_FILE" ]; then
        echo -e "${YELLOW}[WARN] Configuration file is empty${NC}"
        return 1
    fi
    
    echo -e "${CYAN}[INFO] Restoring rules from config...${NC}"
    if reload_pf_anchor; then
        local count=$(grep -c '^[^#]' "$CONFIG_FILE" 2>/dev/null || echo 0)
        echo -e "${GREEN}[OK] Restored $count rule(s)${NC}"
        log_action "Restored $count rules from config"
    else
        echo -e "${RED}[ERROR] Failed to restore rules${NC}"
        return 1
    fi
}

# Apply a single rule (add to config and reload)
add_single_rule() {
    local proto="$1"
    local in_port="$2"
    local target_ip="$3"
    local target_port="$4"
    
    # Check if rule already exists in config
    if grep -q "^$proto:$in_port:" "$CONFIG_FILE" 2>/dev/null; then
        echo -e "${YELLOW}[WARN] Rule for $proto port $in_port already exists!${NC}"
        read -p "Overwrite? (y/n): " ov
        [ "$ov" != "y" ] && return 1
        # Remove old line
        sed -i "/^$proto:$in_port:/d" "$CONFIG_FILE"
    fi
    
    # Append new rule
    echo "$proto:$in_port:$target_ip:$target_port" >> "$CONFIG_FILE"
    # Reload anchor
    reload_pf_anchor
    log_action "Added rule: $proto:$in_port -> $target_ip:$target_port"
    echo -e "${GREEN}[SUCCESS] Rule added and activated${NC}"
    return 0
}

# Add new rule interactively
add_new_rule() {
    echo -e "\n${CYAN}--- Add new forwarding rule ---${NC}"
    echo -e "${WHITE}Create a port forwarding rule from this server to a destination server.${NC}\n"
    
    # Protocol
    while true; do
        echo -e "Select protocol (${YELLOW}tcp${NC} or ${YELLOW}udp${NC}):"
        read -p "> " PROTO
        [ "$PROTO" = "tcp" ] || [ "$PROTO" = "udp" ] && break
        echo -e "${RED}Error: enter tcp or udp!${NC}"
    done
    
    # Target IP
    while true; do
        echo -e "Enter destination IP address:"
        read -p "> " TARGET_IP
        validate_ip "$TARGET_IP" && break
        echo -e "${RED}Error: invalid IP address!${NC}"
    done
    
    # Port configuration
    echo -e "\n${YELLOW}Port configuration:${NC}"
    echo -e "  ${CYAN}1${NC}) Same port for input and output"
    echo -e "  ${CYAN}2${NC}) Different ports"
    read -p "Choose (1/2): " port_choice
    
    if [ "$port_choice" = "1" ]; then
        while true; do
            echo -e "Enter port number (same for input and output):"
            read -p "> " PORT
            validate_port "$PORT" && break
            echo -e "${RED}Error: port 1-65535!${NC}"
        done
        IN_PORT="$PORT"
        OUT_PORT="$PORT"
    else
        while true; do
            echo -e "Enter ${YELLOW}INPUT Port${NC} (on this server):"
            read -p "> " IN_PORT
            validate_port "$IN_PORT" && break
            echo -e "${RED}Error: port 1-65535!${NC}"
        done
        while true; do
            echo -e "Enter ${YELLOW}OUTPUT Port${NC} (on destination server):"
            read -p "> " OUT_PORT
            validate_port "$OUT_PORT" && break
            echo -e "${RED}Error: port 1-65535!${NC}"
        done
    fi
    
    add_single_rule "$PROTO" "$IN_PORT" "$TARGET_IP" "$OUT_PORT"
    echo -e "\n${GREEN}Rule active: $PROTO port $IN_PORT -> $TARGET_IP:$OUT_PORT${NC}"
    read -p "Press Enter to continue..."
}

# List active rules in anchor
list_active_rules() {
    echo -e "\n${CYAN}--- Active forwarding rules (PF anchor $PF_ANCHOR) ---${NC}"
    echo -e "${MAGENTA}PORT (IN)\tPROTOCOL\tDESTINATION (IP:OUT)${NC}"
    
    local found=0
    pfctl -a "$PF_ANCHOR" -sr 2>/dev/null | grep '^rdr pass on' | while read -r line; do
        proto=$(echo "$line" | awk '/proto/ {for(i=1;i<=NF;i++) if($i=="proto") print $(i+1)}')
        in_port=$(echo "$line" | awk '/port/ {for(i=1;i<=NF;i++) if($i=="port") print $(i+1)}')
        target=$(echo "$line" | sed 's/.*-> //')
        echo -e "$in_port\t\t$proto\t\t$target"
        found=1
    done
    [ $found -eq 0 ] && echo -e "${YELLOW}No active rules${NC}"
    echo ""
    read -p "Press Enter..."
}

# Delete a single rule by index (from config file)
delete_single_rule() {
    if [ ! -s "$CONFIG_FILE" ]; then
        echo -e "${YELLOW}[WARN] No rules to delete${NC}"
        read -p "Press Enter..."
        return
    fi
    
    echo -e "\n${CYAN}--- Delete rule ---${NC}"
    # Load rules into array
    RULES=""
    i=1
    while IFS=':' read -r proto in_port target_ip target_port; do
        case "$proto" in ''|\#*) continue ;; esac
        echo -e "${YELLOW}[$i]${NC} $proto:$in_port -> $target_ip:$target_port"
        eval "RULE_$i='$proto:$in_port:$target_ip:$target_port'"
        i=$((i+1))
    done < "$CONFIG_FILE"
    total=$((i-1))
    
    if [ $total -eq 0 ]; then
        echo -e "${RED}No rules found.${NC}"
        read -p "Press Enter..."
        return
    fi
    
    echo ""
    read -p "Rule number to delete (0 to cancel): " num
    if [ "$num" -eq 0 ] 2>/dev/null || [ $num -gt $total ]; then
        return
    fi
    
    # Delete the line from config
    eval "line=\$RULE_$num"
    sed -i "/^$line$/d" "$CONFIG_FILE"
    # Reload anchor
    reload_pf_anchor
    log_action "Deleted rule: $line"
    echo -e "${GREEN}[OK] Rule deleted.${NC}"
    read -p "Press Enter..."
}

# Flush all rules (clear anchor and config)
flush_rules() {
    echo -e "\n${RED}!!! WARNING !!!${NC}"
    echo "This will DELETE ALL port forwarding rules."
    read -p "Are you sure? (y/n): " confirm
    [ "$confirm" != "y" ] && return
    
    backup_pf
    # Clear anchor
    pfctl -a "$PF_ANCHOR" -F all 2>/dev/null
    # Clear config file
    > "$CONFIG_FILE"
    log_action "All port forwarding rules flushed"
    echo -e "${GREEN}[OK] All rules removed.${NC}"
    read -p "Press Enter..."
}

# System preparation: enable IP forwarding, enable pf, create directories
prepare_system() {
    # Enable IP forwarding
    if [ "$(sysctl -n net.inet.ip.forwarding)" -ne 1 ]; then
        sysctl net.inet.ip.forwarding=1
        echo "net.inet.ip.forwarding=1" >> /etc/sysctl.conf
        log_action "Enabled IP forwarding"
    fi
    
    # Enable pf if not already enabled
    if ! pfctl -si 2>/dev/null | grep -q "Status: Enabled"; then
        pfctl -e
        log_action "Enabled pf"
        # Ensure pf starts at boot
        rcctl enable pf
        rcctl set pf flags=
    fi
    
    # Create config directory
    mkdir -p "$CONFIG_DIR"
    
    # Ensure anchor is present in main pf.conf (optional but recommended)
    if ! grep -q "^anchor \"$PF_ANCHOR\"" /etc/pf.conf 2>/dev/null; then
        echo "anchor \"$PF_ANCHOR\"" >> /etc/pf.conf
        pfctl -f /etc/pf.conf
        log_action "Added anchor $PF_ANCHOR to /etc/pf.conf and reloaded"
    fi
    
    # Install global command if not already
    if [ "$0" != "/usr/local/bin/gokaskad" ] && [ -f "$0" ]; then
        cp -f "$0" "/usr/local/bin/gokaskad"
        chmod 755 "/usr/local/bin/gokaskad"
        log_action "Installed global command: gokaskad"
    fi
    
    log_action "System prepared"
}

# Install autostart (rc.d script)
install_autostart() {
    echo -e "${CYAN}[INFO] Installing autostart...${NC}"
    
    # Save current rules first
    save_rules_to_config
    
    # Create rc.d script
    cat > /etc/rc.d/gokaskad << 'EOF'
#!/bin/sh
#
# $OpenBSD: rc.d script for gokaskad
#

daemon="/usr/local/bin/gokaskad"
daemon_flags="--restore"

. /etc/rc.d/rc.subr

rc_reload=NO
rc_cmd $1
EOF
    chmod 755 /etc/rc.d/gokaskad
    
    # Enable service
    rcctl enable gokaskad
    rcctl start gokaskad
    
    log_action "Autostart installed (rc.d)"
    echo -e "${GREEN}[OK] Service gokaskad added to autostart${NC}"
    echo -e "${CYAN}[INFO] Management commands:${NC}"
    echo -e "  rcctl start gokaskad   - start service"
    echo -e "  rcctl stop gokaskad    - stop service"
    echo -e "  rcctl restart gokaskad - restart service"
    echo -e "  rcctl status gokaskad  - service status"
}

remove_autostart() {
    echo -e "${CYAN}[INFO] Removing autostart...${NC}"
    if [ -f /etc/rc.d/gokaskad ]; then
        rcctl stop gokaskad 2>/dev/null
        rcctl disable gokaskad 2>/dev/null
        rm -f /etc/rc.d/gokaskad
        echo -e "${GREEN}[OK] Autostart removed${NC}"
        log_action "Autostart removed"
    else
        echo -e "${YELLOW}[WARN] Autostart service not found${NC}"
    fi
}

# Diagnostics
show_diagnostics() {
    clear
    echo -e "${CYAN}SYSTEM DIAGNOSTICS${NC}"
    echo -e "${MAGENTA}========================================${NC}\n"
    
    # Public IP (via external service)
    echo -e "${YELLOW}External IP address:${NC}"
    ext_ip=$(curl -s ifconfig.me 2>/dev/null || echo "Not detected")
    echo -e "  ${WHITE}$ext_ip${NC}\n"
    
    # Default interface
    echo -e "${YELLOW}Default interface (used for rdr):${NC}"
    echo -e "  ${WHITE}$EXT_IF${NC}\n"
    
    # IP forwarding
    fwd=$(sysctl -n net.inet.ip.forwarding 2>/dev/null)
    echo -e "${YELLOW}IP Forwarding:${NC}"
    if [ "$fwd" = "1" ]; then
        echo -e "  ${GREEN}Enabled${NC}"
    else
        echo -e "  ${RED}Disabled${NC}"
    fi
    
    # PF status
    echo -e "${YELLOW}PF status:${NC}"
    if pfctl -si 2>/dev/null | grep -q "Status: Enabled"; then
        echo -e "  ${GREEN}Enabled${NC}"
    else
        echo -e "  ${RED}Disabled${NC}"
    fi
    
    # Active rules in anchor
    count=$(pfctl -a "$PF_ANCHOR" -sr 2>/dev/null | grep -c '^rdr')
    echo -e "${YELLOW}Active port forwarding rules:${NC}"
    echo -e "  ${WHITE}$count${NC}\n"
    
    # Config file
    if [ -f "$CONFIG_FILE" ]; then
        cfg_rules=$(grep -c '^[^#]' "$CONFIG_FILE" 2>/dev/null || echo 0)
        echo -e "${YELLOW}Saved rules in config:${NC}"
        echo -e "  ${WHITE}$cfg_rules${NC}"
        if [ $cfg_rules -gt 0 ]; then
            echo -e "${CYAN}Config content:${NC}"
            cat "$CONFIG_FILE" | sed 's/^/  /'
        fi
        echo ""
    fi
    
    # Autostart status
    if rcctl check gokaskad 2>/dev/null; then
        echo -e "${YELLOW}Autostart service:${NC}"
        echo -e "  ${GREEN}Enabled and $(rcctl status gokaskad 2>/dev/null | grep -q "running" && echo "running" || echo "stopped")${NC}"
    else
        echo -e "${YELLOW}Autostart service:${NC}"
        echo -e "  ${RED}Not installed${NC}"
    fi
    
    echo ""
    read -p "Press Enter..."
}

# --- MAIN MENU ---
show_menu() {
    while true; do
        clear
        echo -e "${CYAN}"
        echo "======================================================"
        echo "       KASKAD - Port Forwarding Tool (OpenBSD/PF)"
        echo "======================================================"
        echo -e "${NC}"
        
        echo -e "${YELLOW}Available actions:${NC}\n"
        echo -e "  ${GREEN}1${NC}) Add new forwarding rule"
        echo -e "  ${GREEN}2${NC}) List active rules"
        echo -e "  ${GREEN}3${NC}) Delete one rule"
        echo -e "  ${GREEN}4${NC}) Reset ALL rules (flush)"
        echo -e "  ${GREEN}5${NC}) Save current rules to config"
        echo -e "  ${GREEN}6${NC}) Restore rules from config"
        echo -e "  ${GREEN}7${NC}) Install autostart (rc.d)"
        echo -e "  ${GREEN}8${NC}) Remove autostart"
        echo -e "  ${GREEN}9${NC}) System diagnostics"
        echo -e "  ${GREEN}0${NC}) Exit"
        echo -e "\n${MAGENTA}======================================================${NC}"
        read -p "Your choice: " choice
        
        case "$choice" in
            1) add_new_rule ;;
            2) list_active_rules ;;
            3) delete_single_rule ;;
            4) flush_rules ;;
            5) save_rules_to_config ;;
            6) restore_rules_from_config ;;
            7) install_autostart ;;
            8) remove_autostart ;;
            9) show_diagnostics ;;
            0) 
                echo -e "${GREEN}Goodbye!${NC}"
                exit 0
                ;;
            *) 
                echo -e "${RED}Invalid choice!${NC}"
                sleep 1
                ;;
        esac
    done
}

# --- COMMAND LINE HANDLING ---
case "$1" in
    --restore)
        check_root
        prepare_system
        restore_rules_from_config
        exit 0
        ;;
    --save)
        check_root
        save_rules_to_config
        exit 0
        ;;
    --flush)
        check_root
        flush_rules "silent"  # silent not used in this version, but works
        exit 0
        ;;
    --help)
        echo "gokaskad - Port Forwarding Tool for OpenBSD/PF"
        echo ""
        echo "Usage: gokaskad [OPTION]"
        echo ""
        echo "Options:"
        echo "  --restore    Restore rules from config file"
        echo "  --save       Save current anchor rules to config file"
        echo "  --flush      Delete all port forwarding rules"
        echo "  --help       Show this help"
        echo ""
        echo "Without options - interactive menu"
        exit 0
        ;;
esac

# --- MAIN EXECUTION ---
check_root
prepare_system
show_menu
