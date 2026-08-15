FROM python:3.14-slim AS base

RUN useradd --uid 1000 --create-home --shell /usr/sbin/nologin app

WORKDIR /app

COPY requirements.txt .
# pip itself isn't needed at runtime once deps are installed - and it vendors
# its own (currently outdated) copies of msgpack/setuptools under
# pip/_vendor/, which trips the image vulnerability scan even though nothing
# in this app ever imports them. Strip pip and ensurepip's bundled wheels
# out of the final image rather than waiting on an upstream pip release.
RUN pip install --no-cache-dir -r requirements.txt \
    && pip uninstall -y setuptools wheel pip \
    && rm -rf /usr/local/lib/python3.14/ensurepip

COPY app ./app

USER 1000

EXPOSE 8000

CMD ["fastapi", "run", "app/main.py", "--port", "8000"]
