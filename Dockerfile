FROM python:3.11-slim

WORKDIR /app

# Install system deps for some pip packages
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc g++ && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN chmod +x start_all.sh

CMD ["./start_all.sh"]
