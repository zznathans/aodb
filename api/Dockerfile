FROM python:3.14-slim


WORKDIR /app

COPY requirements.txt .
# pip itself isn't needed at runtime once deps are installed - and it vendors
# its own (currently outdated) copies of msgpack/setuptools under
# pip/_vendor/, which trips the image vulnerability scan even though nothing
# in this app ever imports them. Strip pip and ensurepip's bundled wheels
# out before copying /usr/local into the distroless final stage.
RUN pip install --no-cache-dir --require-hashes -r requirements.txt
WORKDIR /app
COPY app ./app

EXPOSE 8000

# distroless has no shell, so the `fastapi` CLI shim (which `fastapi run`
# relies on) can't be exec'd the normal way - invoke uvicorn directly
# instead. This drops a few of `fastapi run`'s prod conveniences (e.g.
# auto-detected proxy headers); revisit if those turn out to matter.
ENTRYPOINT ["fastapi", "run"]
