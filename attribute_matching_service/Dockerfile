FROM python:3.10-slim

# Set the working directory
WORKDIR /app

# Copy requirements.txt
COPY requirements.txt /app/requirements.txt

# Install dependencies
RUN pip install --no-cache-dir -r /app/requirements.txt

# Copy the FastAPI app code
COPY . /app

# Expose the port the app runs on
EXPOSE 8000

# Command to run the app
CMD ["uvicorn", "preprocessing:app", "--host", "0.0.0.0", "--port", "8000"]