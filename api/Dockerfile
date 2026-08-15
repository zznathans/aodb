FROM python:3.14-slim@sha256:ce40764625a4ff50df3548277632e7f96c4e77fe75fa848aae9885476e7df5a4 AS base

RUN useradd --uid 1000 --create-home --shell /usr/sbin/nologin app

WORKDIR /app

COPY requirements.txt .
# pip itself isn't needed at runtime once deps are installed - and it vendors
# its own (currently outdated) copies of msgpack/setuptools under
# pip/_vendor/, which trips the image vulnerability scan even though nothing
# in this app ever imports them. Strip pip and ensurepip's bundled wheels
# out of the final image rather than waiting on an upstream pip release.
RUN pip install --no-cache-dir --require-hashes -r requirements.txt \
    && pip uninstall -y setuptools wheel pip \
    && rm -rf /usr/local/lib/python3.14/ensurepip

# None of these are ever exercised by this app at runtime (no mount/login/
# perl/gzip/tar invocations) - they're just part of python:3.14-slim's
# default package set. Purging them cut the image's Trivy-reported
# vulnerability count roughly in half (149 -> 75) and eliminated every
# CRITICAL/HIGH finding except libacl1/libtinfo6/libncursesw6, which
# can't be removed without also removing coreutils/sed/tar or bash
# themselves (verified via `apt-get remove -s`) - not worth that.
RUN apt-get remove -y --purge --allow-remove-essential \
        gzip login ncurses-base ncurses-bin perl-base util-linux \
        bsdutils mount libblkid1 libmount1 libsmartcols1 libuuid1 \
    && apt-get autoremove -y --purge \
    && rm -rf /var/lib/apt/lists/*

COPY app ./app

USER 1000

EXPOSE 8000

CMD ["fastapi", "run", "app/main.py", "--port", "8000"]
