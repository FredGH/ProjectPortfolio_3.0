FROM python:3.11-slim

WORKDIR /app

# ffmpeg is required by pydub for MP3 encoding/decoding
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p logs output

# Gradio must bind to 0.0.0.0 to accept connections from outside the container.
# Port 7860 is the required port for Hugging Face Spaces Docker SDK.
ENV GRADIO_SERVER_NAME=0.0.0.0
ENV GRADIO_SERVER_PORT=7860

EXPOSE 7860

CMD ["python", "app.py"]
