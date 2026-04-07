#!/bin/bash

# --- COLORS ---
RED='\033[0;31m'
GREEN='\033[0;32m'
CYAN='\033[0;36m'
YELLOW='\033[1;33m'
MAGENTA='\033[0;35m'
WHITE='\033[1;37m'
BLUE='\033[0;34m'
NC='\033[0m'

# --- CONFIGURATION ---
CONFIG_DIR="/etc/gokaskad"
CONFIG_FILE="$CONFIG_DIR/rules.conf"
SERVICE_FILE="/etc/systemd/system/gokaskad.service"
LOG_FILE="/var/log/gokaskad.log"

# --- HELPER FUNCTIONS ---

log_action() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" >> "$LOG_FILE"
}

check_root() {
    if [ "$EUID" -ne 0 ]; then
        echo -e "${RED}[ERROR] Run script with root privileges!${NC}"
        exit 1
    fi
}

validate_ip() {
    local ip=$1
    if [[ $ip =~ ^[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}$ ]]; then
        local IFS='.'
        read -ra octets <<< "$ip"
        for octet in "${octets[@]}"; do
            if [ "$octet" -gt 255 ]; then
                return 1
            fi
        done
        return 0
    fi
    return 1
}

validate_port() {
    local port=$1
    if [[ "$port" =~ ^[0-9]+$ ]] && [ "$port" -ge 1 ] && [ "$port" -le 65535 ]; then
        return 0
    fi
    return 1
}

backup_iptables() {
    local backup_file="$CONFIG_DIR/iptables-backup-$(date +%Y%m%d-%H%M%S).txt"
    mkdir -p "$CONFIG_DIR"
    iptables-save > "$backup_file"
    log_action "Backup saved: $backup_file"
    echo -e "${GREEN}[OK] Backup saved: $backup_file${NC}"
}

save_rules_to_config() {
    mkdir -p "$CONFIG_DIR"
    > "$CONFIG_FILE"
    
    echo -e "${CYAN}[INFO] Saving rules to config...${NC}"
    
    local saved_count=0
    
    # Parse from iptables-save
    while IFS= read -r line; do
        if echo "$line" | grep -q "DNAT" && echo "$line" | grep -q "PREROUTING"; then
            # Extract protocol
            local proto=""
            if echo "$line" | grep -q " -p tcp "; then
                proto="tcp"
            elif echo "$line" | grep -q " -p udp "; then
                proto="udp"
            fi
            
            # Extract input port
            local in_port=""
            if echo "$line" | grep -q " --dport "; then
                in_port=$(echo "$line" | sed 's/.*--dport //' | awk '{print $1}')
            fi
            
            # Extract destination
            local dest=""
            if echo "$line" | grep -q " --to-destination "; then
                dest=$(echo "$line" | sed 's/.*--to-destination //' | awk '{print $1}')
            fi
            
            if [[ -n "$proto" && -n "$in_port" && -n "$dest" ]]; then
                local target_ip="${dest%:*}"
                local target_port="${dest#*:}"
                echo "$proto:$in_port:$target_ip:$target_port" >> "$CONFIG_FILE"
                log_action "Saved rule: $proto:$in_port -> $target_ip:$target_port"
                saved_count=$((saved_count + 1))
            fi
        fi
    done < <(iptables-save -t nat 2>/dev/null)
    
    # Show result
    if [ $saved_count -gt 0 ]; then
        echo -e "${GREEN}[OK] Saved $saved_count rule(s) to $CONFIG_FILE${NC}"
        echo -e "${CYAN}Saved rules:${NC}"
        cat "$CONFIG_FILE"
    else
        echo -e "${YELLOW}[WARN] No DNAT rules found to save${NC}"
        echo -e "${YELLOW}Do you want to manually enter rules? (y/n)${NC}"
        read -p "> " manual_save
        if [[ "$manual_save" == "y" ]]; then
            manual_save_rules
        fi
    fi
}

manual_save_rules() {
    echo -e "${CYAN}Manual rule entry${NC}"
    echo -e "Format: protocol:input_port:target_ip:target_port"
    echo -e "Example: tcp:8080:1.2.3.4:8080"
    echo -e "Enter 'done' to finish"
    
    while true; do
        read -p "Rule: " rule
        [[ "$rule" == "done" ]] && break
        if [[ "$rule" =~ ^(tcp|udp):[0-9]+:[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+:[0-9]+$ ]]; then
            echo "$rule" >> "$CONFIG_FILE"
            echo -e "${GREEN}[OK] Rule added${NC}"
        else
            echo -e "${RED}[ERROR] Invalid format. Use: protocol:port:ip:port${NC}"
        fi
    done
}

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
    
    local restored_count=0
    
    while IFS=':' read -r proto in_port target_ip target_port; do
        # Skip comments and empty lines
        [[ "$proto" =~ ^#.*$ ]] && continue
        [[ -z "$proto" ]] && continue
        
        echo -e "  Restoring: $proto:$in_port -> $target_ip:$target_port"
        apply_iptables_rules "$proto" "$in_port" "$target_port" "$target_ip" "Restored" "silent"
        log_action "Restored rule: $proto:$in_port -> $target_ip:$target_port"
        restored_count=$((restored_count + 1))
    done < "$CONFIG_FILE"
    
    echo -e "${GREEN}[OK] Restored $restored_count rule(s) from configuration${NC}"
    
    # Show active rules after restore
    echo -e "\n${CYAN}Active rules after restore:${NC}"
    list_active_rules
}

# --- SYSTEM PREPARATION ---
prepare_system() {
    # Create global command gokaskad
    if [ "$0" != "/usr/local/bin/gokaskad" ]; then
        cp -f "$0" "/usr/local/bin/gokaskad"
        chmod +x "/usr/local/bin/gokaskad"
        log_action "Installed global command: gokaskad"
    fi
    
    # Enable IP Forwarding
    if ! grep -q "net.ipv4.ip_forward=1" /etc/sysctl.conf; then
        echo "net.ipv4.ip_forward=1" >> /etc/sysctl.conf
    else
        sed -i 's/#net.ipv4.ip_forward=1/net.ipv4.ip_forward=1/' /etc/sysctl.conf
    fi
    
    # Enable Google BBR
    if ! grep -q "net.core.default_qdisc=fq" /etc/sysctl.conf; then
        echo "net.core.default_qdisc=fq" >> /etc/sysctl.conf
    fi
    if ! grep -q "net.ipv4.tcp_congestion_control=bbr" /etc/sysctl.conf; then
        echo "net.ipv4.tcp_congestion_control=bbr" >> /etc/sysctl.conf
    fi
    sysctl -p > /dev/null 2>&1
    
    # Create config directory
    mkdir -p "$CONFIG_DIR"
    
    # Detect package manager and install dependencies
    if command -v apt-get &> /dev/null; then
        export DEBIAN_FRONTEND=noninteractive
        if ! command -v iptables &> /dev/null; then
            apt-get update -y > /dev/null 2>&1
            apt-get install -y iptables > /dev/null 2>&1
        fi
    elif command -v dnf &> /dev/null; then
        if ! command -v iptables &> /dev/null; then
            dnf install -y iptables > /dev/null 2>&1
        fi
    elif command -v yum &> /dev/null; then
        if ! command -v iptables &> /dev/null; then
            yum install -y iptables > /dev/null 2>&1
        fi
    fi
    
    log_action "System prepared"
}

# --- SERVICE MANAGEMENT ---
install_autostart() {
    echo -e "${CYAN}[INFO] Installing autostart...${NC}"
    
    # Save current rules first
    save_rules_to_config
    
    # Detect OS
    if [ -f /etc/os-release ]; then
        . /etc/os-release
        OS=$ID
    else
        echo -e "${RED}Failed to detect OS${NC}"
        return 1
    fi
    
    # Create systemd service
    cat > "$SERVICE_FILE" << EOF
[Unit]
Description=Gokaskad Port Forwarding Service
After=network.target

[Service]
Type=oneshot
ExecStart=/usr/local/bin/gokaskad --restore
RemainAfterExit=yes
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF
    
    systemctl daemon-reload
    systemctl enable gokaskad.service
    systemctl start gokaskad.service
    
    # Save iptables rules persistently
    if command -v iptables-save &> /dev/null; then
        if [ -d /etc/iptables ]; then
            iptables-save > /etc/iptables/rules.v4
        elif [ -d /etc/sysconfig ]; then
            iptables-save > /etc/sysconfig/iptables
        fi
    fi
    
    log_action "Autostart installed"
    echo -e "${GREEN}[OK] Service gokaskad added to autostart${NC}"
    echo -e "${CYAN}[INFO] Management commands:${NC}"
    echo -e "  systemctl status gokaskad  - service status"
    echo -e "  systemctl restart gokaskad - restart service"
    echo -e "  systemctl stop gokaskad     - stop service"
    echo -e "  gokaskad --restore          - restore rules"
}

remove_autostart() {
    echo -e "${CYAN}[INFO] Removing autostart...${NC}"
    
    if [ -f "$SERVICE_FILE" ]; then
        systemctl stop gokaskad.service 2>/dev/null
        systemctl disable gokaskad.service 2>/dev/null
        rm -f "$SERVICE_FILE"
        systemctl daemon-reload
        echo -e "${GREEN}[OK] Autostart removed${NC}"
        log_action "Autostart removed"
    else
        echo -e "${YELLOW}[WARN] Autostart service not found${NC}"
    fi
}

# --- APPLY IPTABLES RULES ---
apply_iptables_rules() {
    local PROTO=$1
    local IN_PORT=$2
    local OUT_PORT=$3
    local TARGET_IP=$4
    local NAME=$5
    local SILENT=$6
    
    # Check for existing rule
    if iptables -t nat -C PREROUTING -p "$PROTO" --dport "$IN_PORT" -j DNAT --to-destination "$TARGET_IP:$OUT_PORT" 2>/dev/null; then
        if [[ "$SILENT" != "silent" ]]; then
            echo -e "${YELLOW}[WARN] Rule for port $IN_PORT already exists!${NC}"
            read -p "Overwrite? (y/n): " overwrite
            [[ "$overwrite" != "y" ]] && return
        fi
        # Delete old rule
        iptables -t nat -D PREROUTING -p "$PROTO" --dport "$IN_PORT" -j DNAT --to-destination "$TARGET_IP:$OUT_PORT" 2>/dev/null
    fi
    
    IFACE=$(ip route show default | awk '/default/ {print $5}' | head -1)
    if [[ -z "$IFACE" ]]; then
        echo -e "${RED}[ERROR] Failed to determine interface!${NC}"
        log_action "ERROR: Failed to determine interface"
        return 1
    fi
    
    # Delete old rules
    iptables -t nat -D PREROUTING -p "$PROTO" --dport "$IN_PORT" -j DNAT --to-destination "$TARGET_IP:$OUT_PORT" 2>/dev/null
    iptables -D INPUT -p "$PROTO" --dport "$IN_PORT" -j ACCEPT 2>/dev/null
    iptables -D FORWARD -p "$PROTO" -d "$TARGET_IP" --dport "$OUT_PORT" -m state --state NEW,ESTABLISHED,RELATED -j ACCEPT 2>/dev/null
    iptables -D FORWARD -p "$PROTO" -s "$TARGET_IP" --sport "$OUT_PORT" -m state --state ESTABLISHED,RELATED -j ACCEPT 2>/dev/null
    
    # Add new rules
    iptables -A INPUT -p "$PROTO" --dport "$IN_PORT" -j ACCEPT
    iptables -t nat -A PREROUTING -p "$PROTO" --dport "$IN_PORT" -j DNAT --to-destination "$TARGET_IP:$OUT_PORT"
    
    if ! iptables -t nat -C POSTROUTING -o "$IFACE" -j MASQUERADE 2>/dev/null; then
        iptables -t nat -A POSTROUTING -o "$IFACE" -j MASQUERADE
    fi
    
    iptables -A FORWARD -p "$PROTO" -d "$TARGET_IP" --dport "$OUT_PORT" -m state --state NEW,ESTABLISHED,RELATED -j ACCEPT
    iptables -A FORWARD -p "$PROTO" -s "$TARGET_IP" --sport "$OUT_PORT" -m state --state ESTABLISHED,RELATED -j ACCEPT
    
    # Configure UFW if active
    if command -v ufw &> /dev/null && ufw status | grep -q "Status: active"; then
        ufw allow "$IN_PORT"/"$PROTO" >/dev/null 2>&1
        sed -i 's/DEFAULT_FORWARD_POLICY="DROP"/DEFAULT_FORWARD_POLICY="ACCEPT"/' /etc/default/ufw 2>/dev/null
        ufw reload >/dev/null 2>&1
    fi
    
    # Save rules persistently
    if command -v netfilter-persistent &> /dev/null; then
        netfilter-persistent save > /dev/null 2>&1
    elif command -v iptables-save &> /dev/null; then
        if [ -d /etc/iptables ]; then
            iptables-save > /etc/iptables/rules.v4
        elif [ -d /etc/sysconfig ]; then
            iptables-save > /etc/sysconfig/iptables
        fi
    fi
    
    # Save to config
    save_rules_to_config
    
    if [[ "$SILENT" != "silent" ]]; then
        echo -e "${GREEN}[SUCCESS] $NAME configured!${NC}"
        echo -e "$PROTO: Input $IN_PORT -> Output $TARGET_IP:$OUT_PORT"
        log_action "Rule added: $PROTO:$IN_PORT -> $TARGET_IP:$OUT_PORT"
        read -p "Press Enter to return to menu..."
    fi
}

# --- ADD NEW RULE (UNIFIED) ---
add_new_rule() {
    echo -e "\n${CYAN}--- Add new forwarding rule ---${NC}"
    echo -e "${WHITE}Create a port forwarding rule from this server to a destination server.${NC}\n"
    
    while true; do
        echo -e "Select protocol (${YELLOW}tcp${NC} or ${YELLOW}udp${NC}):"
        read -p "> " PROTO
        if [[ "$PROTO" == "tcp" || "$PROTO" == "udp" ]]; then
            break
        else
            echo -e "${RED}Error: enter tcp or udp!${NC}"
        fi
    done
    
    while true; do
        echo -e "Enter destination IP address (where to send traffic):"
        read -p "> " TARGET_IP
        if validate_ip "$TARGET_IP"; then
            break
        else
            echo -e "${RED}Error: invalid IP address!${NC}"
        fi
    done
    
    echo -e "\n${YELLOW}Port configuration:${NC}"
    echo -e "  ${CYAN}1${NC}) Same port for input and output"
    echo -e "  ${CYAN}2${NC}) Different ports (input port != output port)"
    read -p "Choose (1/2): " port_choice
    
    if [[ "$port_choice" == "1" ]]; then
        while true; do
            echo -e "Enter port number (same for input and output):"
            read -p "> " PORT
            if validate_port "$PORT"; then
                IN_PORT=$PORT
                OUT_PORT=$PORT
                break
            else
                echo -e "${RED}Error: port must be number from 1 to 65535!${NC}"
            fi
        done
    else
        while true; do
            echo -e "Enter ${YELLOW}INPUT Port${NC} (on this server):"
            read -p "> " IN_PORT
            if validate_port "$IN_PORT"; then
                break
            else
                echo -e "${RED}Error: port must be number from 1 to 65535!${NC}"
            fi
        done
        
        while true; do
            echo -e "Enter ${YELLOW}OUTPUT Port${NC} (on destination server):"
            read -p "> " OUT_PORT
            if validate_port "$OUT_PORT"; then
                break
            else
                echo -e "${RED}Error: port must be number from 1 to 65535!${NC}"
            fi
        done
    fi
    
    apply_iptables_rules "$PROTO" "$IN_PORT" "$OUT_PORT" "$TARGET_IP" "Forwarding rule"
}

# --- LIST ACTIVE RULES ---
list_active_rules() {
    echo -e "\n${CYAN}--- Active forwarding rules ---${NC}"
    echo -e "${MAGENTA}PORT (IN)\tPROTOCOL\tDESTINATION (IP:OUT)${NC}"
    
    local found=0
    
    # Parse from iptables-save
    while IFS= read -r line; do
        if echo "$line" | grep -q "DNAT" && echo "$line" | grep -q "PREROUTING"; then
            local in_port=$(echo "$line" | sed 's/.*--dport //' | awk '{print $1}')
            local proto=""
            if echo "$line" | grep -q " -p tcp "; then
                proto="tcp"
            elif echo "$line" | grep -q " -p udp "; then
                proto="udp"
            fi
            local dest=$(echo "$line" | sed 's/.*--to-destination //' | awk '{print $1}')
            
            if [[ -n "$in_port" && -n "$proto" && -n "$dest" ]]; then
                echo -e "$in_port\t\t$proto\t\t$dest"
                found=1
            fi
        fi
    done < <(iptables-save -t nat 2>/dev/null)
    
    if [ $found -eq 0 ]; then
        echo -e "${YELLOW}No active rules${NC}"
    fi
    
    echo ""
    read -p "Press Enter..."
}

# --- DELETE SINGLE RULE ---
delete_single_rule() {
    echo -e "\n${CYAN}--- Delete rule ---${NC}"
    declare -a RULES_LIST
    local i=1
    
    while IFS= read -r line; do
        if echo "$line" | grep -q "DNAT" && echo "$line" | grep -q "PREROUTING"; then
            local in_port=$(echo "$line" | sed 's/.*--dport //' | awk '{print $1}')
            local proto=""
            if echo "$line" | grep -q " -p tcp "; then
                proto="tcp"
            elif echo "$line" | grep -q " -p udp "; then
                proto="udp"
            fi
            local dest=$(echo "$line" | sed 's/.*--to-destination //' | awk '{print $1}')
            
            if [[ -n "$in_port" && -n "$proto" && -n "$dest" ]]; then
                RULES_LIST[$i]="$in_port:$proto:$dest"
                echo -e "${YELLOW}[$i]${NC} Input: $in_port ($proto) -> Output: $dest"
                ((i++))
            fi
        fi
    done < <(iptables-save -t nat 2>/dev/null)
    
    if [ ${#RULES_LIST[@]} -eq 0 ]; then
        echo -e "${RED}No active rules.${NC}"
        read -p "Press Enter..."
        return
    fi
    
    echo ""
    read -p "Rule number to delete (0 to cancel): " rule_num
    if [[ "$rule_num" == "0" || -z "${RULES_LIST[$rule_num]}" ]]; then
        return
    fi
    
    # Parse string
    IFS=':' read -r d_port d_proto d_dest <<< "${RULES_LIST[$rule_num]}"
    local target_ip="${d_dest%:*}"
    local target_port="${d_dest#*:}"
    
    iptables -t nat -D PREROUTING -p "$d_proto" --dport "$d_port" -j DNAT --to-destination "$d_dest" 2>/dev/null
    iptables -D INPUT -p "$d_proto" --dport "$d_port" -j ACCEPT 2>/dev/null
    iptables -D FORWARD -p "$d_proto" -d "$target_ip" --dport "$target_port" -m state --state NEW,ESTABLISHED,RELATED -j ACCEPT 2>/dev/null
    iptables -D FORWARD -p "$d_proto" -s "$target_ip" --sport "$target_port" -m state --state ESTABLISHED,RELATED -j ACCEPT 2>/dev/null
    
    # Save changes
    if command -v netfilter-persistent &> /dev/null; then
        netfilter-persistent save > /dev/null 2>&1
    elif command -v iptables-save &> /dev/null; then
        if [ -d /etc/iptables ]; then
            iptables-save > /etc/iptables/rules.v4
        elif [ -d /etc/sysconfig ]; then
            iptables-save > /etc/sysconfig/iptables
        fi
    fi
    
    # Update config
    save_rules_to_config
    
    log_action "Rule deleted: $d_port:$d_proto -> $d_dest"
    echo -e "${GREEN}[OK] Rule deleted.${NC}"
    read -p "Press Enter..."
}

# --- FLUSH ALL RULES ---
flush_rules() {
    local SILENT=$1
    
    if [[ "$SILENT" != "silent" ]]; then
        echo -e "\n${RED}!!! WARNING !!!${NC}"
        echo "Reset ALL iptables settings."
        read -p "Are you sure? (y/n): " confirm
        if [[ "$confirm" != "y" ]]; then
            return
        fi
    fi
    
    backup_iptables
    
    iptables -P INPUT ACCEPT
    iptables -P FORWARD ACCEPT
    iptables -P OUTPUT ACCEPT
    iptables -t nat -F
    iptables -t mangle -F
    iptables -F
    iptables -X
    
    if command -v netfilter-persistent &> /dev/null; then
        netfilter-persistent save > /dev/null 2>&1
    elif command -v iptables-save &> /dev/null; then
        if [ -d /etc/iptables ]; then
            iptables-save > /etc/iptables/rules.v4
        elif [ -d /etc/sysconfig ]; then
            iptables-save > /etc/sysconfig/iptables
        fi
    fi
    
    # Clear config
    > "$CONFIG_FILE"
    
    log_action "All rules flushed"
    
    if [[ "$SILENT" != "silent" ]]; then
        echo -e "${GREEN}[OK] Flushed.${NC}"
        read -p "Press Enter..."
    fi
}

# --- DIAGNOSTICS ---
show_diagnostics() {
    clear
    echo -e "${CYAN}SYSTEM DIAGNOSTICS${NC}"
    echo -e "${MAGENTA}========================================${NC}\n"
    
    # IP address
    echo -e "${YELLOW}Server IP address:${NC}"
    echo -e "  ${WHITE}$(curl -s ifconfig.me 2>/dev/null || echo "Not detected")${NC}\n"
    
    # Main interface
    IFACE=$(ip route show default | awk '/default/ {print $5}' | head -1)
    echo -e "${YELLOW}Main interface:${NC}"
    echo -e "  ${WHITE}$IFACE${NC}\n"
    
    # BBR status
    BBR=$(sysctl -n net.ipv4.tcp_congestion_control 2>/dev/null)
    echo -e "${YELLOW}Google BBR:${NC}"
    if [[ "$BBR" == "bbr" ]]; then
        echo -e "  ${GREEN}Active ($BBR)${NC}"
    else
        echo -e "  ${RED}Inactive ($BBR)${NC}"
    fi
    
    # IP Forwarding
    IP_FWD=$(sysctl -n net.ipv4.ip_forward 2>/dev/null)
    echo -e "${YELLOW}IP Forwarding:${NC}"
    if [[ "$IP_FWD" == "1" ]]; then
        echo -e "  ${GREEN}Enabled${NC}"
    else
        echo -e "  ${RED}Disabled${NC}"
    fi
    
    # Active rules
    RULES_COUNT=$(iptables-save -t nat 2>/dev/null | grep -c "DNAT")
    echo -e "${YELLOW}Active rules in iptables:${NC}"
    echo -e "  ${WHITE}$RULES_COUNT${NC}\n"
    
    # Config file status
    if [ -f "$CONFIG_FILE" ]; then
        CONFIG_RULES=$(grep -c -v '^$' "$CONFIG_FILE" 2>/dev/null || echo "0")
        echo -e "${YELLOW}Saved rules in config:${NC}"
        echo -e "  ${WHITE}$CONFIG_RULES${NC}"
        if [ $CONFIG_RULES -gt 0 ]; then
            echo -e "${CYAN}Config content:${NC}"
            cat "$CONFIG_FILE" | sed 's/^/  /'
        fi
        echo ""
    fi
    
    # Service status
    if systemctl is-active --quiet gokaskad.service 2>/dev/null; then
        echo -e "${YELLOW}Autostart service:${NC}"
        echo -e "  ${GREEN}Active${NC}"
    elif [ -f "$SERVICE_FILE" ]; then
        echo -e "${YELLOW}Autostart service:${NC}"
        echo -e "  ${YELLOW}Installed but not active${NC}"
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
        echo "       GOKASKAD - Port Forwarding Tool"
        echo "======================================================"
        echo -e "${NC}"
        
        echo -e "${YELLOW}Available actions:${NC}\n"
        
        echo -e "  ${GREEN}1${NC}) Add new forwarding rule"
        echo -e "  ${GREEN}2${NC}) List active rules"
        echo -e "  ${GREEN}3${NC}) Delete one rule"
        echo -e "  ${GREEN}4${NC}) Reset ALL settings"
        echo -e "  ${GREEN}5${NC}) Save configuration"
        echo -e "  ${GREEN}6${NC}) Restore configuration"
        echo -e "  ${GREEN}7${NC}) Install autostart"
        echo -e "  ${GREEN}8${NC}) Remove autostart"
        echo -e "  ${GREEN}9${NC}) System diagnostics"
        echo -e "  ${GREEN}0${NC}) Exit"
        echo -e "\n${MAGENTA}======================================================${NC}"
        read -p "Your choice: " choice
        
        case $choice in
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

# --- COMMAND LINE ARGUMENTS HANDLING ---
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
        flush_rules "silent"
        exit 0
        ;;
    --help)
        echo "Gokaskad Port Forwarding Tool"
        echo ""
        echo "Usage: gokaskad [OPTION]"
        echo ""
        echo "Options:"
        echo "  --restore    Restore rules from config"
        echo "  --save       Save current rules to config"
        echo "  --flush      Flush all iptables rules"
        echo "  --help       Show this help message"
        echo ""
        echo "Without options - interactive menu"
        exit 0
        ;;
esac

# --- MAIN EXECUTION ---
check_root
prepare_system
show_menu
