FROM mcr.microsoft.com/playwright:v1.56.1-noble

ENV DEBIAN_FRONTEND=noninteractive
ENV PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1
WORKDIR /opt/gui-runner

RUN apt-get update \
    && apt-get install -y --no-install-recommends xvfb x11vnc novnc websockify fluxbox \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --uid 10001 --create-home --shell /usr/sbin/nologin runner

COPY package.json package-lock.json ./
RUN npm ci --omit=dev --no-audit --no-fund

COPY --chown=root:root entrypoint.sh runner.js reporter.cjs playwright.config.cjs ./
COPY --chown=root:root gui-preview.html /usr/share/novnc/gui-preview.html
RUN sed -i 's/\r$//' /opt/gui-runner/entrypoint.sh \
    && chmod 0555 /opt/gui-runner/entrypoint.sh \
    && mkdir /opt/gui-runner/tests \
    && chmod 0555 /opt/gui-runner/tests

ENV DISPLAY=:99 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    NODE_ENV=production

USER runner
ENTRYPOINT ["/usr/bin/env", "bash", "/opt/gui-runner/entrypoint.sh"]
