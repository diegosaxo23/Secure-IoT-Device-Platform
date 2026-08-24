FROM eclipse-mosquitto:2

USER root

COPY --chown=mosquitto:mosquitto broker/mosquitto.conf /mosquitto/config/mosquitto.conf
COPY --chown=mosquitto:mosquitto broker/acl /mosquitto/config/acl
COPY broker/docker-entrypoint-platform.sh /usr/local/bin/docker-entrypoint-platform.sh

RUN chmod 0644 /mosquitto/config/mosquitto.conf \
    && chmod 0600 /mosquitto/config/acl \
    && chmod 0755 /usr/local/bin/docker-entrypoint-platform.sh \
    && mkdir -p /mosquitto/certs/ca /mosquitto/certs/broker /mosquitto/certs/control /mosquitto/certs/healthcheck /mosquitto/certs/crl \
    && chown -R mosquitto:mosquitto /mosquitto/certs /mosquitto/config /mosquitto/data /mosquitto/log

ENTRYPOINT ["/usr/local/bin/docker-entrypoint-platform.sh"]
CMD ["mosquitto", "-c", "/mosquitto/config/mosquitto.conf"]
