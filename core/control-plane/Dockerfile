FROM docker:27.5.1-cli

RUN apk add --no-cache bash python3 py3-pip

WORKDIR /workspace
ENV PYTHONPATH=/workspace/cli/src

COPY core/control-plane/entrypoint.sh /usr/local/bin/clawos-control-plane-entrypoint
RUN chmod +x /usr/local/bin/clawos-control-plane-entrypoint

ENTRYPOINT ["/usr/local/bin/clawos-control-plane-entrypoint"]
