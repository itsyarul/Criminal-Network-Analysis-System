FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt third_party/en_core_web_sm-3.7.1-py3-none-any.whl ./
RUN pip install --no-cache-dir -r requirements.txt
# Vendored spaCy model — offline install, no network needed at build time
# (matches spacy==3.7.4 in requirements.txt). Runtime keeps its graceful
# regex/canonical fallback if the model is ever absent.
RUN pip install --no-cache-dir --no-index ./en_core_web_sm-3.7.1-py3-none-any.whl \
  && python -c "import en_core_web_sm; print('spaCy model vendored:', en_core_web_sm.__version__)" \
  && rm ./en_core_web_sm-3.7.1-py3-none-any.whl

COPY . .

EXPOSE 8000

CMD ["uvicorn", "backend.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
