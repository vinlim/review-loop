# review-loop with the toolchain the registered repositories need. The same image runs on the Mac
# (Docker Desktop or OrbStack) and on a Linux VPS; only the mounted state directory differs.
FROM php:8.4-cli-bookworm

RUN apt-get update && apt-get install -y --no-install-recommends \
        git curl ca-certificates unzip sqlite3 libzip-dev libpq-dev libicu-dev libonig-dev python3 python3-venv python3-pip \
    && docker-php-ext-install -j"$(nproc)" pdo_pgsql pdo_sqlite zip intl bcmath pcntl \
    && rm -rf /var/lib/apt/lists/*

# gh, Node 22, composer, and the two agent CLIs
RUN curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg -o /usr/share/keyrings/githubcli-archive-keyring.gpg \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" > /etc/apt/sources.list.d/github-cli.list \
    && curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get update && apt-get install -y --no-install-recommends gh nodejs && rm -rf /var/lib/apt/lists/* \
    && curl -fsSL https://getcomposer.org/installer | php -- --install-dir=/usr/local/bin --filename=composer \
    && npm install -g @openai/codex @anthropic-ai/claude-code

WORKDIR /opt/review-loop
COPY pyproject.toml ./
COPY review_loop ./review_loop
RUN python3 -m venv /opt/venv && /opt/venv/bin/pip install --no-cache-dir . && ln -s /opt/venv/bin/review-loop /usr/local/bin/review-loop

# State, config and worktrees live on a mounted volume; auth comes from the environment or mounted CLI stores.
ENV REVIEW_LOOP_HOME=/state
VOLUME ["/state"]
ENTRYPOINT ["review-loop"]
CMD ["doctor"]
