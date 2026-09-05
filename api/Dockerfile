FROM python:3.14-slim@sha256:cad9a2c871761c413caa6fdd6441c783451e740a48aaeba60ae62a8b53525ef6 AS builder

WORKDIR /app

COPY requirements.txt .
# pip itself isn't needed at runtime once deps are installed - and it vendors
# its own (currently outdated) copies of msgpack/setuptools under
# pip/_vendor/, which trips the image vulnerability scan even though nothing
# in this app ever imports them. Strip pip and ensurepip's bundled wheels
# out before copying /usr/local into the distroless final stage.
RUN pip install --no-cache-dir --require-hashes -r requirements.txt \
    && pip uninstall -y setuptools wheel pip \
    && rm -rf /usr/local/lib/python3.14/ensurepip

# pydantic-core/rignore/watchfiles are Rust extension modules and need
# libgcc_s.so.1 at runtime, which distroless/base-debian13 doesn't ship
# (it only carries libc/libm) and which lives outside /usr/local, so it
# isn't picked up by the final stage's `COPY --from=builder /usr/local
# /usr/local`. Stash a copy of it inside /usr/local so that COPY carries
# it over too - see the final stage's LD_LIBRARY_PATH.
ARG TARGETARCH
RUN set -eux; \
    case "$TARGETARCH" in \
      amd64) triplet=x86_64-linux-gnu ;; \
      arm64) triplet=aarch64-linux-gnu ;; \
      *) echo "unsupported TARGETARCH: $TARGETARCH" >&2; exit 1 ;; \
    esac; \
    cp "/usr/lib/${triplet}/libgcc_s.so.1" /usr/local/lib/libgcc_s.so.1

# EXPERIMENTAL: distroless/python3-debian13 bundles Python 3.13, not the
# 3.14 this project pins - so the final stage is distroless/base-debian13
# (no Python at all) with the *entire* /usr/local from this builder stage
# copied in instead. That carries python3.14 itself, libpython3.14.so, and
# site-packages together, preserving the exact interpreter version. Both
# this builder and distroless/base-debian13 are Debian 13 (trixie), so the
# glibc/libc.so.6/libm.so.6 python3.14 links against resolve from the base
# image's system libs without needing anything else copied over.
FROM gcr.io/distroless/base-debian13:nonroot@sha256:2d7d29b504e7166f6d0c7655a18ebf5def5b37b029f8c4f8667e434ba774844f AS final

COPY --from=builder /usr/local /usr/local
ENV LD_LIBRARY_PATH=/usr/local/lib

WORKDIR /app
COPY app ./app

EXPOSE 8000

# distroless has no shell, so the `fastapi` CLI shim (which `fastapi run`
# relies on) can't be exec'd the normal way - invoke uvicorn directly
# instead. This drops a few of `fastapi run`'s prod conveniences (e.g.
# auto-detected proxy headers); revisit if those turn out to matter.
ENTRYPOINT ["python3.14", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
