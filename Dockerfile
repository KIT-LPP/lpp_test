# hadolint global ignore=DL3006,DL3008,DL3013
FROM node:20-bookworm-slim AS build_external

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
    imagemagick wget ca-certificates \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Preparation for casljs
WORKDIR /casljs
COPY casljs/package.json casljs/package-lock.json ./
RUN npm ci
COPY casljs/ ./

# Preparation for motd
WORKDIR /motd
COPY docker/mk_motd.sh docker/aqua.png ./
RUN bash mk_motd.sh aqua.png

# Starship
ARG TARGETARCH
WORKDIR /starship
# starship publishes no aarch64 gnu build; the musl one is static, so it runs on Ubuntu too
RUN case "${TARGETARCH}" in \
    amd64) starship_target=x86_64-unknown-linux-gnu ;; \
    arm64) starship_target=aarch64-unknown-linux-musl ;; \
    *) echo "unsupported TARGETARCH: ${TARGETARCH}" >&2; exit 1 ;; \
    esac \
    && wget "https://github.com/starship/starship/releases/download/v1.19.0/starship-${starship_target}.tar.gz" \
    && tar xvf "starship-${starship_target}.tar.gz"

################################################################################
FROM ubuntu:22.04 AS build_collector
WORKDIR /app
ARG LPP_PYTHON_BASE=.
ENV UV_LINK_MODE=copy

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

COPY ${LPP_PYTHON_BASE}/ ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --locked

RUN rm -f ./dist/*.whl && uv build

################################################################################
# 演習室は Ubuntu 22.04 なので
FROM ubuntu:22.04 AS user_env

# install essential packages
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
    ca-certificates curl gnupg \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# install latest (20.x) node.js
RUN curl -fsSL https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key | gpg --dearmor -o /etc/apt/nodesource.gpg \
    && echo "deb [signed-by=/etc/apt/nodesource.gpg] https://deb.nodesource.com/node_20.x nodistro main" > /etc/apt/sources.list.d/nodesource.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends nodejs \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/* \
    && rm -rf /usr/lib/node_modules/npm/ /usr/bin/npm

# install essential packages
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
    ca-certificates curl gnupg gdb make \
    python3-pip tmux \
    vim less cmake g++ bash-completion whiptail \
    doxygen graphviz texlive-latex-extra texlive-lang-japanese texlive-fonts-extra xdvik-ja \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# essential files for doxygen
WORKDIR /doxygen
COPY docker/doxygen/ /doxygen/
COPY docker/doxygen/genPDF.sh /usr/local/bin/lppdoxygen
RUN chmod +x /usr/local/bin/lppdoxygen

WORKDIR /lpp/test

# Copy shell related files
COPY --from=build_external /etc/motd /etc/motd
COPY --from=build_external /starship/starship /usr/local/bin/starship
COPY ./docker/pytest /usr/local/bin/pytest
COPY ./docker/bashrc /root/.bashrc
COPY ./docker/issue /etc/issue
# COPY ./docker/lpptest /usr/local/bin/lpptest
COPY ./docker/starship.toml /root/.config/starship.toml
# COPY ./docker/lpptest_completion /etc/bash_completion.d/lpptest_completion

RUN touch /.dockerenv

COPY --from=build_external /casljs /casljs

RUN echo '[ ! -z "$TERM" -a -r /etc/motd ] && cat /etc/motd && cat /etc/issue ' >> /etc/bash.bashrc

COPY --from=build_collector /app/dist/*.whl /tmp/

RUN --mount=type=cache,mode=0755,target=/root/.cache/pip pip install /tmp/*.whl \
    && rm -rf /tmp/*.whl \
    && python3 -c 'from lpp_collector.mklink import main; main()'

VOLUME [ "/workspaces" ]
WORKDIR /workspaces
