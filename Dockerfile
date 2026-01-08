# Python Image
FROM python:3.9-slim

# Working Directory
WORKDIR /app

# Copy Requirements
COPY requirements.txt .

# Install Dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy All Code
COPY . .

# Create uploads folder inside container
RUN mkdir -p uploads

# Expose Port (Internally 5000, we map it later)
EXPOSE 5000

# Command to run
CMD ["python", "app.py"]