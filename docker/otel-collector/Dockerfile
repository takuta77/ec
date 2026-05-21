FROM otel/opentelemetry-collector-contrib:0.111.0

COPY config.yaml /etc/otelcol/config.yaml

# Base image already runs as 10001:10001; declare explicitly so static scanners
# (semgrep dockerfile.security.missing-user.missing-user) see it.
USER 10001

CMD ["--config=/etc/otelcol/config.yaml"]
