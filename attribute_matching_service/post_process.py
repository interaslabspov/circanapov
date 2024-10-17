import pandas as pd
import os
import json
import logging
import yaml
from fuzzywuzzy import process
import json
import os
from datetime import datetime
from google.cloud import storage
from gcp_storage import upload_json_to_gcp, upload_csv_to_gcp

# Load configuration from config.yaml
with open("config.yaml", "r") as config_file:
    config = yaml.safe_load(config_file)

# Set up logging
logging.basicConfig(level='INFO', format='%(asctime)s - %(levelname)s - %(message)s')
step_3_process_dir = config['step_3_process_dir']
output_directory = config['output_directory']
bucket_name = config['bucket_name']

# Flatten JSON content
def flatten_json_content(json_content):
    flattened_content = {}
    if isinstance(json_content, dict):
        for key, value in json_content.items():
            if isinstance(value, list):
                for index, item in enumerate(value):
                    flattened_content[f"{key}{index + 1}"] = item
            else:
                flattened_content[key] = value
    elif isinstance(json_content, list) and len(json_content) > 1:
        logging.warning("Unexpected JSON structure")
        return None
    return flattened_content

# Recursive JSON flattening
def flatten_json(y):
    out = {}
    def flatten(x, name=''):
        if isinstance(x, dict):
            for a in x:
                flatten(x[a], name + a + '_')
        elif isinstance(x, list):
            for i, a in enumerate(x):
                flatten(a, name + str(i) + '_')
        else:
            out[name[:-1]] = x
    flatten(y)
    return out

# Fuzzy match attributes
def fuzzy_match_attributes(flat_json, attributes):
    matched_attributes = {}
    unmatched_attributes = []
    for attribute in attributes:
        best_match, score = process.extractOne(attribute, flat_json.keys())
        if score > 50:
            matched_attributes[attribute] = flat_json.pop(best_match)
        else:
            unmatched_attributes.append(attribute)
    combined_attributes = {**matched_attributes, **flat_json}
    return matched_attributes, combined_attributes

# Count "Not Applicable" values
def get_count_not_applicable(product_details_flat, attribute_names):
    return sum(1 for attribute_name in attribute_names if product_details_flat.get(attribute_name) == "Not Applicable")

# Define retailer sites dictionary
retailer_sites = {
    "ABC Warehouse": "abcwarehouse.com",
    "adorama_camera": "adorama.com",
    # More retailers as needed
}

# Main function
def process_llm_output(all_item_data_sorted, df_sorted):
    
    # Add new columns if not exist
    for col in ['count_not_applicable', 'total_attributes', 'attribute_hit_percentage', 'attribute_names', 'value_data', 'keyval']:
        if col not in df_sorted.columns:
            df_sorted[col] = None

    count = 0

    date_str = datetime.now().strftime('%Y-%m-%d')
    os.makedirs(date_str, exist_ok=True)

    for index, row in df_sorted.iterrows():
        item_id = str(row['ItemID'])
        json_file_name = f"{item_id}.json"
        json_file_path = os.path.join(step_3_process_dir, json_file_name)

        if os.path.exists(json_file_path):
            with open(json_file_path, 'r') as json_file:
                retailer_name = row.get('Retailer Name', '')
                attribute_values = all_item_data_sorted.loc[index]['category_attribute']
                attribute_dict = json.loads(attribute_values)
                n_of_attribute = len(attribute_dict)

                product_details = json.load(json_file)
                if product_details is None:
                    print("Item id Json file Not exist", json_file_name)
                    continue

                flat_json = flatten_json(product_details)
                matched_attributes, combined_attribute = fuzzy_match_attributes(flat_json, list(attribute_dict.keys()))
                
                try:
                    count_not_applicable = get_count_not_applicable(matched_attributes, list(attribute_dict.keys()))
                except Exception as e:
                    print(f"Error processing item {item_id}, {json_file_name} : {e}")
                    continue

                attribute_hit_percentage = (n_of_attribute - count_not_applicable) / n_of_attribute * 100

                df_sorted.at[index, 'total_attributes'] = n_of_attribute
                df_sorted.at[index, 'count_not_applicable'] = count_not_applicable
                df_sorted.at[index, 'attribute_hit_percentage'] = attribute_hit_percentage
                df_sorted.at[index, 'attribute_names'] = list(attribute_dict.keys())
                df_sorted.at[index, 'keyval'] = json.dumps(matched_attributes)

                final_structure = {
                    'upc': row.get('Outlet UPC', ''),
                    'sku': row.get('SKU', ''),
                    'keyCat': row.get('CATEGORY_NAME', ''),
                    # 'site': retailer_sites.get(row['Retailer Name'], 'unknown'),
                    'retailer_name': row.get('Retailer Name', ''),
                    'country_id': 'us',
                    'data_source': 'gm',
                    'keyval': combined_attribute
                }
                df_sorted.at[index, 'value_data'] = json.dumps(final_structure)
                count += 1

                final_structure_json = json.dumps(final_structure)
                destination_blob_name = f'{output_directory}/{date_str}/{item_id}.json'
                upload_json_to_gcp(bucket_name, final_structure_json, destination_blob_name)


    ''''
    Update the original sheet with values
    '''
    
    csv_file_path = f"{date_str}/sheet_updated.csv"
    df_sorted.to_csv(csv_file_path, index=False)

    destination_blob_name = f'{output_directory}/{date_str}/sheet_updated.json'
    upload_csv_to_gcp(bucket_name, csv_file_path, destination_blob_name)

    print(f'Total rows updated : {count}')
