# Python Image
FROM python:3.9-slim

# Working Directory
WORKDIR /app

# Copy Requirements
COPY requirements.txt .

# Install Dependencies (Gunicorn bhi install hoga ab)
RUN pip install --no-cache-dir -r requirements.txt

# Copy All Code
COPY . .

# Create uploads folder inside container
RUN mkdir -p uploads

# Expose Port
EXPOSE 5000

# --- CHANGE IS HERE ---
# Purana: CMD ["python", "app.py"]
# Naya (Production Wala):
CMD ["gunicorn", "-w", "4", "-b", "0.0.0.0:5000", "app:app"]