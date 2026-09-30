FROM python:3.11-slim

# Install system dependencies for ping and traceroute
RUN apt-get update && apt-get install -y iputils-ping traceroute && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Run the Flask app with gunicorn
CMD ["sh", "-c", "gunicorn app:app --bind 0.0.0.0:$PORT"]
