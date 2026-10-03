FROM python:3.11-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /srv
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY app ./app
COPY db ./db
COPY scripts ./scripts
COPY evals/results.mock.json evals/results.real.jso[n] ./evals/
RUN useradd --system --uid 10001 clinicalgate && chown -R clinicalgate /srv
USER clinicalgate
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s CMD python -c "import urllib.request,os;urllib.request.urlopen('http://localhost:%s/health'%os.environ.get('PORT','8000'))"
ENTRYPOINT ["./scripts/entrypoint.sh"]
