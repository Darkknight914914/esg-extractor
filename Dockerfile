FROM python:3.12-slim
# libgl/libglib are needed by OpenCV, which RapidOCR uses
RUN apt-get update && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
RUN python samples/make_samples.py
ENV PORT=8000
EXPOSE 8000
# one worker: runs execute in background threads inside the process
CMD gunicorn -w 1 --threads 8 --timeout 300 -b 0.0.0.0:$PORT app.server:app
