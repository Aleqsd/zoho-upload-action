FROM python:3.11-slim

# Install deps
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

# Copy script
COPY upload_zoho.py /usr/local/bin/upload_zoho.py
RUN chmod +x /usr/local/bin/upload_zoho.py

ENTRYPOINT ["python", "/usr/local/bin/upload_zoho.py"]
