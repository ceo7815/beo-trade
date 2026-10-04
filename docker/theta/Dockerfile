FROM eclipse-temurin:21-jre-jammy

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 --shell /usr/sbin/nologin theta

WORKDIR /opt/theta
ADD https://download-stable.thetadata.us/ThetaTerminalv3.jar /opt/theta/ThetaTerminalv3.jar
COPY config.toml /opt/theta/config.toml
COPY start.sh /opt/theta/start.sh
RUN chmod 755 /opt/theta/start.sh \
    && mkdir -p /opt/theta/logs \
    && chown -R theta:theta /opt/theta

USER theta
EXPOSE 25503
ENTRYPOINT ["/opt/theta/start.sh"]
