FROM python:3.12-slim

# Tesseract is the local fallback for reading screenshots.
RUN apt-get update \
    && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-rus \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1

COPY pyproject.toml README.md ./
COPY src ./src
COPY prompts ./prompts
# Editable install keeps prompts/ next to the package, where the code looks for it.
RUN pip install -e .

RUN useradd --create-home --uid 1000 bot
USER bot

CMD ["python", "-m", "intonaciya.bot"]
