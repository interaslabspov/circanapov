import os
import shutil
import json
import logging
import pandas as pd
import numpy as np
from collections import defaultdict
from fastapi import FastAPI
import uvicorn
from google.cloud import storage
import io
import yaml
from transformer import transform_by_llm
from gcp_storage import load_data
from post_process import process_llm_output

# Load configuration from config.yaml
with open("config.yaml", "r") as config_file:
    config = yaml.safe_load(config_file)

# Set up logging
logging.basicConfig(level='INFO', format='%(asctime)s - %(levelname)s - %(message)s')

bucket_name = config['bucket_name']
input_directory_blob_prefix = config['input_directory_blob_prefix']
step_1_process_dir = config['step_1_process_dir']
step_2_process_dir = config['step_2_process_dir']
step_3_process_dir = config['step_3_process_dir']
metadata_blob_name = config['metadata_blob_name']
mapping_blob_name = config['mapping_blob_name']
meta_attribute_value_blob_name = config['meta_attribute_value_blob_name']

app = FastAPI()

# Function to count JSON files in a directory
def get_json_files_count(directory, deep=False):
    json_files_count = defaultdict(int)
    total_count = 0
    for root, _, files in os.walk(directory):
        count = sum(1 for file in files if file.endswith('.json'))
        if count > 0:
            json_files_count[root] = count
            total_count += count

    if deep:
        for root, count in json_files_count.items():
            print(f"{root} : {count}")

    print(f"Total JSON files: {total_count}")

# Function to flatten JSON files from Google Cloud Storage
def flatten_json_files_from_gcs(bucket_name, blob_prefix, output_directory):
    storage_client = storage.Client()
    bucket = storage_client.get_bucket(bucket_name)

    if not os.path.exists(output_directory):
        os.makedirs(output_directory)

    processed_count = 0
    skipped_count = 0
    duplicate_files = []
    duplicate_directories = defaultdict(list)

    blobs = bucket.list_blobs(prefix=blob_prefix)
    for blob in blobs:
        if blob.name.endswith('.json'):
            directory_name = os.path.basename(os.path.dirname(blob.name))
            cat_name = os.path.basename(os.path.dirname(os.path.dirname(blob.name)))
            new_file_name = f"{os.path.splitext(os.path.basename(blob.name))[0]}_${directory_name}_£{cat_name}.json"
            new_file_path = os.path.join(output_directory, new_file_name)

            if os.path.exists(new_file_path):
                duplicate_files.append(new_file_path)
                duplicate_directories[new_file_path].append(blob.name)
            else:
                blob.download_to_filename(new_file_path)
                processed_count += 1
        else:
            skipped_count += 1

    print(f"Processed JSON files: {processed_count}")
    print(f"Skipped files: {skipped_count}")
    if duplicate_files:
        print(f"Files already exists (not copied): {len(duplicate_files)}")
        for duplicate_file in duplicate_files:
            print(f"{duplicate_file} found in blobs: {', '.join(duplicate_directories[duplicate_file])}")

# Function to load metadata information from Google Cloud Storage
def load_metadata_from_gcs(bucket_name, metadata_blob_name, mapping_blob_name):
    storage_client = storage.Client()
    bucket = storage_client.get_bucket(bucket_name)

    metadata_blob = bucket.blob(metadata_blob_name)
    mapping_blob = bucket.blob(mapping_blob_name)

    metadata_content = metadata_blob.download_as_text()
    mapping_content = mapping_blob.download_as_text()

    metadata_df = pd.read_csv(io.StringIO(metadata_content))
    metadata_df['SKU'] = metadata_df['SKU'].astype(str)
    metadata_df['Outlet Model Number'] = metadata_df['Outlet Model Number'].astype(str)

    additional_mapping = pd.read_csv(io.StringIO(mapping_content))
    additional_mapping_dict = dict(zip(additional_mapping['Filename'], additional_mapping['ItemID']))
    return metadata_df, additional_mapping_dict

# Function to determine if unique ID is SKU, UPC, or model number
def get_unique_id_type(unique_id, file_name, metadata_df, additional_mapping_dict):
    unique_id_variants = [unique_id, unique_id.lower(), unique_id.upper(), str(unique_id), str(unique_id).zfill(12)]
    for uid in unique_id_variants:
        item_row = metadata_df[(metadata_df['SKU'].astype(str).str.contains(uid, case=False)) |
                               (metadata_df['Outlet UPC'].astype(str).str.contains(uid, case=False)) |
                               (metadata_df['Outlet Model Number'].astype(str).str.contains(uid, case=False)) |
                               (metadata_df['MODELNUM'].astype(str).str.contains(uid, case=False)) |
                               (metadata_df['ITEMID'].astype(str).str.contains(uid, case=False))]
        if not item_row.empty:
            return item_row
        try:
            uid_int = int(uid)
            item_row = metadata_df[(metadata_df['SKU'] == uid_int) |
                                   (metadata_df['Outlet UPC'] == uid_int) |
                                   (metadata_df['Outlet Model Number'] == uid_int) |
                                   (metadata_df['MODELNUM'] == uid_int) |
                                   (metadata_df['ITEMID'] == uid_int)]
            if not item_row.empty:
                return item_row
        except ValueError:
            continue

    try:
        item_id = additional_mapping_dict[file_name]
    except KeyError:
        logging.warning(f'Advanced mapping not found filename :: {file_name}')
        return pd.DataFrame({})

    item_id_row = get_unique_id_by_item_id(item_id, file_name, metadata_df)
    if item_id_row is not None:
        return item_id_row

    logging.warning(f'Item Not found filename :: {file_name}, item id :: {item_id}')
    return pd.DataFrame({})

# Function to get unique ID by item ID
def get_unique_id_by_item_id(item_id, file_name, metadata_df):
    try:
        item_row = metadata_df[(metadata_df['ITEMID'].astype(int) == item_id)]
        if not item_row.empty:
            return item_row
        else:
            logging.warning(f'Could not determine Item by ItemId: {item_id}')
            return pd.DataFrame({})
    except:
        logging.warning(f'Some issue with Product Name: {item_id}, {file_name}')

# Function to process JSON files
def process_json_files(step_1_process_dir, metadata_df, additional_mapping_dict):
    item_data = {}
    unprocessed_files = []
    processing_counter = 0
    duplicate = 0

    if os.path.isdir(step_1_process_dir):
        for file_name in os.listdir(step_1_process_dir):
            if file_name.endswith('.json'):
                processing_counter += 1
                try:
                    unique_id, product_name = file_name.split('_', 1)
                    product_name = product_name.replace('.json', '')
                    cluster = product_name.split('_£')[1]
                    item_id_row = get_unique_id_type(unique_id, file_name, metadata_df, additional_mapping_dict)
                    if item_id_row.empty:
                        logging.warning(f'FINALLY: Item ID not found for All Tech GM file: {file_name}')
                        unprocessed_files.append(os.path.join(step_1_process_dir, file_name))
                        continue

                    item_id = item_id_row['ItemID'].values[0]
                    retailer_name = (file_name.split('_$')[1]).split('_£')[0]
                    if isinstance(item_id, (np.int64, np.int32)):
                        item_id = int(item_id)

                    if item_id not in item_data:
                        item_data[item_id] = {
                            'cluster': [cluster],
                            'retailers': [retailer_name],
                            'data': []
                        }
                    else:
                        item_data[item_id]['retailers'].append(retailer_name)
                        item_data[item_id]['cluster'].append(cluster)
                        duplicate += 1

                    with open(os.path.join(step_1_process_dir, file_name), 'r') as json_file:
                        json_data = json.load(json_file)
                        item_data[item_id]['data'].append(json_data)

                except ValueError as e:
                    logging.warning(f'Error processing file {file_name}: {e}')
                    unprocessed_files.append(os.path.join(step_1_process_dir, file_name))

    if unprocessed_files:
        logging.warning(f'Processed files: {processing_counter}')
        logging.warning(f'Unprocessed files: {unprocessed_files}')
        logging.warning(f'Length of unprocessed files: {len(unprocessed_files)}')
        logging.warning(f'Duplicate files: {duplicate}')

    return item_data

# Function to save merged files
def save_merged_files(item_data, output_directory):
    os.makedirs(output_directory, exist_ok=True)
    for item_id, item_info in item_data.items():
        retailers = list(set(item_info['retailers']))
        cluster = list(set(item_info['cluster']))
        output_file_name = f'{item_id}_{retailers}_£{cluster}.json'
        output_file_path = os.path.join(output_directory, output_file_name)

        merged_data = {
            'item_id': item_id,
            'retailers': retailers,
            'cluster': cluster,
            'data': item_info['data']
        }

        with open(output_file_path, 'w') as output_file:
            json.dump(merged_data, output_file, indent=4)

@app.get("/start_process")
def start_process():
    # Step 1: Flatten JSON files from Google Cloud Storage
    flatten_json_files_from_gcs(bucket_name, input_directory_blob_prefix, step_1_process_dir)
    print(f"Flattened JSON files are saved in: {step_1_process_dir}")

    # Step 2: Load metadata information from Google Cloud Storage
    metadata_df_raw, additional_mapping_dict = load_metadata_from_gcs(bucket_name, metadata_blob_name, mapping_blob_name)

    # Step 3: Process JSON files
    item_data = process_json_files(step_1_process_dir, metadata_df_raw, additional_mapping_dict)

    # Step 4: Save merged files
    save_merged_files(item_data, step_2_process_dir)
    get_json_files_count(step_2_process_dir)

    metadata_df = load_data(bucket_name, meta_attribute_value_blob_name)
    transform_by_llm(metadata_df, step_2_process_dir, step_3_process_dir)

    process_llm_output(metadata_df, metadata_df_raw)
    return {"message": "Processing completed", "output_directory": step_3_process_dir}

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)