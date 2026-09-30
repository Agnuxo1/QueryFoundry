FROM postgres:17-bookworm@sha256:639ab7ceb90e13123085b741fb31ef493fba25463002f6da665352e7b534b652
RUN apt-get update && apt-get install -y --no-install-recommends openssh-server python3 time procps \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --shell /bin/bash qf \
    && mkdir -p /run/sshd /home/qf/.ssh \
    && chown -R qf:qf /home/qf/.ssh && chmod 700 /home/qf/.ssh
RUN printf 'PermitRootLogin no\nPasswordAuthentication no\nPubkeyAuthentication yes\nAllowUsers qf\n' > /etc/ssh/sshd_config.d/queryfoundry.conf
COPY infra/start.sh /usr/local/bin/queryfoundry-start
COPY infra/init-role.sh /docker-entrypoint-initdb.d/01-queryfoundry-role.sh
RUN chmod +x /usr/local/bin/queryfoundry-start /docker-entrypoint-initdb.d/01-queryfoundry-role.sh
ENTRYPOINT ["/usr/local/bin/queryfoundry-start"]
CMD ["postgres"]
