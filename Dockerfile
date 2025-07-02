# Use the official Rasa SDK image as a base
FROM rasa/rasa-sdk:3.6.2

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt