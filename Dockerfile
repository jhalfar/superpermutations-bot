FROM python:3.12.7-slim

# gcc builds the checkers and the generator from gens/; xz-utils is for debugging inside the container (python reads xz itself)
RUN apt-get update \
 && apt-get install -y --no-install-recommends gcc libc6-dev xz-utils \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY LICENSE ./
COPY bot/ bot/
COPY checker/ checker/
COPY gens/ gens/
COPY scripts/docker-entrypoint.sh ./
RUN python -I checker/build.py

RUN useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin bot \
 && mkdir -p /data /work \
 && chown bot:bot /data /work \
 && chmod 755 docker-entrypoint.sh

ENV SUPERPERM_RECORDS=/data/records.json \
    TMPDIR=/work \
    PYTHONDONTWRITEBYTECODE=1
VOLUME ["/data", "/work"]
USER 10001

ENTRYPOINT ["/app/docker-entrypoint.sh"]
