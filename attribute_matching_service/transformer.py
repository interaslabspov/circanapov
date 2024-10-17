import os
import json
import time
import random
import base64
import pandas as pd
import vertexai
from rapidfuzz import process
from vertexai.generative_models import GenerativeModel, Part, SafetySetting
import yaml
import logging
import io
from google.cloud import storage


# Set up logging
logging.basicConfig(level='INFO', format='%(asctime)s - %(levelname)s - %(message)s')

# Load metadata from CSV file
def load_metadata(filepath):
    metadata_df = pd.read_csv(filepath)
    metadata_df['SKU'] = metadata_df['SKU'].astype(str)
    metadata_df['Outlet Model Number'] = metadata_df['Outlet Model Number'].astype(str)
    metadata_df['Outlet UPC'] = metadata_df['Outlet UPC'].fillna(0).astype(int)
    metadata_df['ITEMID'] = metadata_df['ITEMID'].astype(int)
    return metadata_df

# Generate prompt text based on category attribute
def generate_prompt_text(cat_attribute):
    return f"""This file data is product information. I want to extract specific details about this product and the attributes for those details are given below. I would like the values for each attribute to be strictly limited to the list of choices provided for that attribute. Add any more additional information as key value pairs.  All the details in the array belong to the same product. Create a single JSON response element considering all the details and attributes provided.

{json.dumps(cat_attribute)}"""

# Look up a row in the dataframe by item ID
def row_lookup_by_item_id(item_id, dataframe):
    item_row = dataframe[dataframe['ITEMID'] == item_id]
    return item_row if not item_row.empty else pd.DataFrame({})

# Wrapper function for LLM interaction
def wrapper(text1, encoded_content):
    def generate():
        vertexai.init(project="document-ai-2024", location="us-central1")
        model = GenerativeModel("gemini-1.5-flash-002")
        responses = model.generate_content([text1, document1],
                                           generation_config=generation_config,
                                           safety_settings=safety_settings,
                                           stream=True)
        return "".join([response.text for response in responses])

    document1 = Part.from_data(mime_type="text/plain", data=base64.b64decode(encoded_content))
    generation_config = {"max_output_tokens": 8192, "temperature": 1, "top_p": 0.95}
    safety_settings = [
        SafetySetting(category=SafetySetting.HarmCategory.HARM_CATEGORY_HATE_SPEECH, threshold=SafetySetting.HarmBlockThreshold.OFF),
        SafetySetting(category=SafetySetting.HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT, threshold=SafetySetting.HarmBlockThreshold.OFF),
        SafetySetting(category=SafetySetting.HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT, threshold=SafetySetting.HarmBlockThreshold.OFF),
        SafetySetting(category=SafetySetting.HarmCategory.HARM_CATEGORY_HARASSMENT, threshold=SafetySetting.HarmBlockThreshold.OFF),
    ]

    return generate()

# Retry function with exponential backoff
def retry_with_backoff(func, *args, max_retries=5, initial_delay=1, max_delay=60):
    retries, delay = 0, initial_delay
    while retries < max_retries:
        try:
            return func(*args)
        except Exception as e:
            if "ResourceExhausted" in str(e) or "429" in str(e):
                print(f"Request failed: {e}. Retrying in {delay} seconds...")
                time.sleep(delay + random.uniform(0, delay / 2))
                delay = min(delay * 2, max_delay)
                retries += 1
            else:
                raise
    raise Exception(f"Maximum retries exceeded after {max_retries} attempts.")

# Get item IDs from directory
def get_item_ids_from_directory(directory_path):
    return [os.path.splitext(file_name)[0] for file_name in os.listdir(directory_path) if file_name.endswith('.json')]

# Main function to process and run LLM prompt
def transform_by_llm(metadata_df, retailer_data_directory, output_directory):
    prompt = {}
    stat_error = []

    # Iterate through files in retailer directory
    for root, _, files in os.walk(retailer_data_directory):
        for filename in files:
            file_path = os.path.join(root, filename)

            if os.path.isfile(file_path) and filename.lower().endswith('.json'):
                print(f"Reading file: {filename}")
                with open(file_path, 'r') as file:
                    content = file.read()
                    try:
                        json_content = json.loads(content)
                    except Exception as e:
                        print(f"Error decoding JSON: {e}\nFilename :: {filename}")
                        continue

                    item_id = json_content['item_id']
                    row = row_lookup_by_item_id(item_id, metadata_df)
                    if row.empty:
                        message = f'The data not found in All tech GM :: ItemID - {item_id}'
                        print(message)
                        stat_error.append(message)
                        continue

                    cat_attribute = row['category_attribute'].values[0]
                    prompt_text = generate_prompt_text(cat_attribute)
                    prompt[item_id] = (prompt_text, content)

    # Run LLM prompt for each item
    os.makedirs(output_directory, exist_ok=True)
    for filename, value in prompt.items():
        text1 = value[0]
        item_info = json.loads(value[1])
        original_bytes = json.dumps(item_info['data']).encode('utf-8')
        encoded_content = base64.b64encode(original_bytes)
        item_id = item_info['item_id']
        output_file_path = os.path.join(output_directory, f"{item_id}.json")

        if os.path.exists(output_file_path):
            len_of_retailer = len(item_info['data'])
            print(f"Skipping LLM function | Item id already processed :: {item_id} with {len_of_retailer} retailer info")
            continue

        llm_response_text = retry_with_backoff(wrapper, text1, encoded_content)
        json_content = llm_response_text.strip('```json\n').strip('```')
        try:
            data = json.loads(json_content)
        except:
            print(f'Unable to process response:: {llm_response_text}\nAdditional info:: {output_file_path}')
            continue

        with open(output_file_path, "w") as json_file:
            json.dump(data, json_file, indent=4)


with open("config.yaml", "r") as config_file:
    config = yaml.safe_load(config_file)



def load_metadata_from_gcs(bucket_name, metadata_blob_name):
    storage_client = storage.Client()
    bucket = storage_client.get_bucket(bucket_name)

    metadata_blob = bucket.blob(metadata_blob_name)
    metadata_content = metadata_blob.download_as_text()

    metadata_df = pd.read_csv(io.StringIO(metadata_content))
    return metadata_df
