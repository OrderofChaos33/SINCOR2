#!/bin/bash

# SINCOR Production Server Setup Script
# Run as root on fresh Ubuntu 22.04 server

set -e

echo "🚀 Starting SINCOR production setup..."

# Update system
apt-get update && apt-get upgrade -y

# Install Docker
curl -fsSL https://get.docker.com -o get-docker.sh
sh get-docker.sh
usermod -aG docker $USER

# Install Docker Compose
curl -L "https://github.com/docker/compose/releases/latest/download/docker-compose-$(uname -s)-$(uname -m)" -o /usr/local/bin/docker-compose
chmod +x /usr/local/bin/docker-compose

# Install essential tools
apt-get install -y \
    git \
    htop \
    nano \
    curl \
    wget \
    ufw \
    fail2ban \
    logrotate \
    certbot \
    python3-certbot-nginx

# Setup firewall
ufw default deny incoming
ufw default allow outgoing
ufw allow ssh
ufw allow 80/tcp
ufw allow 443/tcp
ufw --force enable

# Configure fail2ban
cat > /etc/fail2ban/jail.local << 'EOF'
[DEFAULT]
bantime = 1h
findtime = 10m
maxretry = 5

[sshd]
enabled = true

[nginx-http-auth]
enabled = true
EOF

systemctl enable fail2ban
systemctl start fail2ban

# Create application directory
mkdir -p /opt/sincor
cd /opt/sincor

# Create sincor user
useradd -r -s /bin/bash sincor
usermod -aG docker sincor
chown -R sincor:sincor /opt/sincor

# Setup log rotation
cat > /etc/logrotate.d/sincor << 'EOF'
/opt/sincor/logs/*.log {
    daily
    missingok
    rotate 30
    compress
    delaycompress
    notifempty
    create 644 sincor sincor
    postrotate
        docker-compose -f /opt/sincor/docker-compose.yml restart sincor-app
    endscript
}
EOF

# Setup system service
cat > /etc/systemd/system/sincor.service << 'EOF'
[Unit]
Description=SINCOR Application
Requires=docker.service
After=docker.service

[Service]
Type=oneshot
RemainAfterExit=true
WorkingDirectory=/opt/sincor
ExecStart=/usr/local/bin/docker-compose up -d
ExecStop=/usr/local/bin/docker-compose down
User=sincor
Group=sincor

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable sincor.service

echo "✅ Server setup complete!"
echo ""
echo "Next steps:"
echo "1. Clone your SINCOR repository to /opt/sincor"
echo "2. Configure /opt/sincor/config/.env"
echo "3. Setup SSL certificates"
echo "4. Start services: systemctl start sincor"
echo ""
echo "🔐 Remember to:"
echo "- Change default SSH port"
echo "- Setup SSH key authentication"
echo "- Configure automatic backups"
echo "- Setup monitoring alerts"