FROM php:8.2-apache

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y \
    mariadb-server \
    mariadb-client \
    && docker-php-ext-install mysqli \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

COPY app/ /var/www/html/
COPY init.sql /init.sql
COPY start.sh /start.sh

RUN chmod +x /start.sh

EXPOSE 80 3306

CMD ["/start.sh"]
