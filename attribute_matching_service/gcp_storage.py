from google.cloud import storage
import io
import pandas as pd
import json

def load_data(bucket_name, metadata_blob_name):
    storage_client = storage.Client()
    bucket = storage_client.get_bucket(bucket_name)

    metadata_blob = bucket.blob(metadata_blob_name)
    metadata_content = metadata_blob.download_as_text()

    metadata_df = pd.read_csv(io.StringIO(metadata_content))
    return metadata_df

# Upload file to Google Cloud Storage
def upload_json_to_gcp(bucket_name, json_data, destination_blob_name):
    # Create a JSON file from the data
    json_string = json.dumps(json_data)
    
    # Initialize the Google Cloud Storage client
    storage_client = storage.Client()
    bucket = storage_client.bucket(bucket_name)
    blob = bucket.blob(destination_blob_name)
    
    # Upload the JSON string to Google Cloud Storage
    blob.upload_from_string(json_string, content_type='application/json')
    print(f"JSON data uploaded to {destination_blob_name}.")

def upload_csv_to_gcp(bucket_name, csv_file_path, destination_blob_name):
    # Initialize the Google Cloud Storage client
    storage_client = storage.Client()
    bucket = storage_client.bucket(bucket_name)
    blob = bucket.blob(destination_blob_name)
    
    # Upload the CSV file to Google Cloud Storage
    blob.upload_from_filename(csv_file_path)
    print(f"CSV file {csv_file_path} uploaded to {destination_blob_name}.")