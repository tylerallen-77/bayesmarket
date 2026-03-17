FROM python:3.11-slim

WORKDIR /app

# Install dependencies
COPY bayesmarket/requirements.txt requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Copy source
COPY bayesmarket/ bayesmarket/
COPY tests/ tests/

# Entry point
CMD ["python", "-m", "bayesmarket"]
