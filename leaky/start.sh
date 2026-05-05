#!/bin/bash
set -e

# Allow external connections
sed -i 's/^bind-address.*/bind-address = 0.0.0.0/' /etc/mysql/mariadb.conf.d/50-server.cnf

mkdir -p /run/mysqld
chown -R mysql:mysql /run/mysqld
chown -R mysql:mysql /var/lib/mysql

service mariadb start

until mysqladmin ping -uroot --silent; do
    echo "Waiting for MariaDB to start..."
    sleep 2
done

if [ ! -f /var/lib/mysql/.db_initialized ]; then
    echo "Initializing database..."

    mysql -uroot <<EOF
ALTER USER 'root'@'localhost' IDENTIFIED BY 'root';
CREATE USER IF NOT EXISTS 'root'@'%' IDENTIFIED BY 'root';
GRANT ALL PRIVILEGES ON *.* TO 'root'@'%' WITH GRANT OPTION;
FLUSH PRIVILEGES;
EOF

    mysql -uroot -proot < /init.sql
    touch /var/lib/mysql/.db_initialized
fi

exec apache2-foreground
