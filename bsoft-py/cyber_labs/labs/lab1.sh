#!/bin/bash 


set -e


echo "========================================" 

echo " XECOPS Lab Setup" 

echo "========================================"


if [[ $EUID -ne 0 ]]; then

    echo "Please run this script as root." 

    exit 1 

fi 


########################################### 

# Create Groups 

########################################### 


getent group soc >/dev/null || groupadd soc 

getent group redteamer >/dev/null || groupadd redteamer 

  

########################################### 

# Create Users 

########################################### 

  

id analyst >/dev/null 2>&1 || useradd -m -s /bin/bash analyst 

id pentester >/dev/null 2>&1 || useradd -m -s /bin/bash pentester 

  

########################################### 

# Set Passwords 

########################################### 

  

echo "analyst:lab123" | chpasswd 

echo "pentester:lab456" | chpasswd 

echo 'root:kP9#vX2!mL7$qZ4*' | chpasswd 

  

########################################### 

# Add Users to Groups 

########################################### 

  

usermod -aG soc analyst 

usermod -aG redteamer pentester 

  

########################################### 

# Analyst File 

########################################### 

  

mkdir -p /home/analyst 

  

touch /home/analyst/Report.txt 

echo "XECOPS{y0U_aRE_a_ANa1yst}" > /home/analyst/Report.txt 

  

chown analyst:soc /home/analyst/Report.txt 

chmod 744 /home/analyst/Report.txt 

  

########################################### 

# Pentester File 

########################################### 

  

mkdir -p /home/pentester 

  

touch /home/pentester/bug.txt 

echo "XECOPS{y0U_aRE_a_PEnteS1er}" > /home/pentester/bug.txt 

  

chown pentester:redteamer /home/pentester/bug.txt 

chmod 646 /home/pentester/bug.txt 

  

########################################### 

# Disable Bash History for ubuntu User 

########################################### 

  

BASHRC="/home/ubuntu/.bashrc" 

  

if [ -f "$BASHRC" ]; then 
    grep -q "export HISTSIZE=0" "$BASHRC" || cat <<EOF >> "$BASHRC" 
export HISTSIZE=0 
export HISTFILESIZE=0 
unset HISTFILE 
history -c 
EOF
fi 
chown ubuntu:ubuntu "$BASHRC" 

  

########################################### 

# Disable Default Cloud User 

########################################### 

  

cat <<EOF >/etc/cloud/cloud.cfg.d/99-disable-default-user.cfg
system_info:
  default_user:
    sudo: null
EOF

  

########################################### 

# Sudo Permissions 

########################################### 

  

cat <<EOF >/etc/sudoers.d/lab-user 
ubuntu ALL=(ALL) NOPASSWD: /usr/bin/systemctl, /usr/bin/chown, /usr/bin/touch, /usr/sbin/useradd, /usr/bin/passwd, /usr/bin/chmod 
EOF

  

chmod 440 /etc/sudoers.d/lab-user 

  

########################################### 

# Remove Cloud Init User Config 

########################################### 

  

cloud-init clean 

  

rm -rf /var/lib/cloud/* 

rm -f /etc/sudoers.d/90-cloud-init-users 

  

########################################### 

# Ownership Fixes 

########################################### 

  

chown -R analyst:soc /home/analyst 

chown -R pentester:redteamer /home/pentester 

  

########################################### 

# Done 

########################################### 

  

echo "" 

echo "========================================" 

echo " Lab configured successfully!" 

echo "========================================" 

echo "" 

echo "Users:" 

echo "  analyst    : lab123" 

echo "  pentester  : lab456" 

echo "  root        : kP9#vX2!mL7\$qZ4*" 

echo "" 
