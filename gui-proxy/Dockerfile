FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update \
    && apt-get install -y --no-install-recommends squid socat \
    && rm -rf /var/lib/apt/lists/*

COPY entrypoint.sh /usr/local/bin/checkpoint-gui-proxy
RUN sed -i 's/\r$//' /usr/local/bin/checkpoint-gui-proxy \
    && chmod 0555 /usr/local/bin/checkpoint-gui-proxy

EXPOSE 3128 16080 13001
USER proxy
ENTRYPOINT ["bash", "/usr/local/bin/checkpoint-gui-proxy"]
