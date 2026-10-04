# Asterisk 18 LTS из исходников (Debian Bullseye) — с поддержкой AudioSocket
# Production-ready: стабильная база, зафиксированная версия Asterisk

FROM debian:bullseye-slim AS builder

ARG ASTERISK_VERSION=18.26.4
ARG ASTERISK_URL=https://downloads.asterisk.org/pub/telephony/asterisk/asterisk-${ASTERISK_VERSION}.tar.gz

# Зависимости для сборки (уменьшенный набор — PJSIP bundled)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    pkg-config \
    wget \
    ca-certificates \
    libedit-dev \
    libjansson-dev \
    libsqlite3-dev \
    uuid-dev \
    libxml2-dev \
    libssl-dev \
    libncurses-dev \
    libnewt-dev \
    libspeex-dev \
    libspeexdsp-dev \
    libogg-dev \
    libvorbis-dev \
    libcurl4-openssl-dev \
    bzip2 \
    patch \
    unixodbc-dev \
    liburiparser-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /usr/src
RUN wget -q -O asterisk.tar.gz "${ASTERISK_URL}" \
    && tar xzf asterisk.tar.gz \
    && rm asterisk.tar.gz \
    && mv asterisk-* asterisk
WORKDIR /usr/src/asterisk

# Сборка bundled pjproject (для PJSIP)
RUN contrib/scripts/get_mp3_source.sh 2>/dev/null || true

RUN ./configure \
    --prefix=/usr \
    --sysconfdir=/etc \
    --localstatedir=/var \
    --with-pjproject-bundled \
    --with-jansson-bundled \
    && make menuselect.makeopts \
    && menuselect/menuselect \
        --enable app_audiosocket \
        --enable res_audiosocket \
        --enable chan_pjsip \
        menuselect.makeopts \
    && make -j$(nproc) \
    && make install \
    && make config \
    && make samples

# Копируем libs Asterisk для финального образа
RUN mkdir -p /asterisk-libs && cp -a /usr/lib/libasterisk*.so* /asterisk-libs/

# --- Финальный образ ---
FROM debian:bullseye-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    gettext-base \
    libedit2 \
    libjansson4 \
    libsqlite3-0 \
    libuuid1 \
    libxml2 \
    libssl1.1 \
    libnewt0.52 \
    libspeex1 \
    libogg0 \
    libvorbis0a \
    libcurl4 \
    libodbc1 \
    liburiparser1 \
    && rm -rf /var/lib/apt/lists/*

# Копируем установленный Asterisk из builder (include не нужен для runtime)
COPY --from=builder /usr/sbin/asterisk /usr/sbin/
# Системные библиотеки Asterisk (libasteriskssl, libasteriskpj)
COPY --from=builder /asterisk-libs/ /usr/lib/
COPY --from=builder /usr/lib/asterisk /usr/lib/asterisk
COPY --from=builder /etc/asterisk /etc/asterisk
COPY --from=builder /var/lib/asterisk /var/lib/asterisk
COPY --from=builder /var/spool/asterisk /var/spool/asterisk

# Asterisk ожидает эти каталоги
RUN mkdir -p /var/run/asterisk /var/log/asterisk \
    && groupadd -r -g 999 asterisk 2>/dev/null || true \
    && useradd -r -u 999 -g asterisk -d /var/lib/asterisk asterisk 2>/dev/null || true \
    && chown -R asterisk:asterisk /var/run/asterisk /var/log/asterisk /var/lib/asterisk /var/spool/asterisk

# Наши шаблоны конфигов (перезаписывают дефолтные)
COPY conf/ /etc/asterisk-templates/

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 5060/udp 5060/tcp
EXPOSE 10000-10100/udp

ENTRYPOINT ["/entrypoint.sh"]
