import requests
import json
import re
import io
import datetime
import pandas as pd
import numpy as np
import streamlit as st
from concurrent.futures import ThreadPoolExecutor, as_completed
from tools.core_utils import get_secure_endpoint, sanitize_error_log

# ==========================================
# TOOL 2: UNRESTRICTED MACHSHIP SEARCH
# ==========================================
def search_machship_connote(connote_number: str) -> str:
    token = st.secrets["machship"]["MACHSHIP_API_TOKEN"]
    connote_number = connote_number.strip().upper()
    headers = { "token": token, "Accept": "application/json" }

    try:
        if connote_number.startswith("MS"):
            ms_id = re.sub(r"\D", "", connote_number)
            base_url = get_secure_endpoint("machship_get", "aHR0cHM6Ly9saXZlLm1hY2hzaGlwLmNvbS9hcGl2Mi9jb25zaWdubWVudHMvZ2V0Q29uc2lnbm1lbnQ/aWQ9")
            url = f"{base_url}{ms_id}"
            response = requests.get(url, headers=headers, timeout=15)
            
            if response.status_code == 200:
                data = response.json()
                if data.get("object"):
                    consignment = data["object"]
                    carrier_node = consignment.get("carrier") or {}
                    carrier = carrier_node.get("name") or carrier_node.get("abbreviation") or "Carrier Not Assigned"
                    
                    status_node = consignment.get("status") or {}
                    status = status_node.get("name", "Unknown Status")
                    
                    return f"Machship Search Success: Connote {connote_number} is with {carrier}. Current Status: {status}."
                return "Machship Search Failed: Connote found but object node is empty."
            return f"Machship Search Failed: HTTP {response.status_code}"
        else:
            return "Machship Search Error: Only MS-prefixed connotes are supported in this function."
    except Exception as e:
        return f"Machship Search Crash: {sanitize_error_log(str(e))}"

# ==========================================
# LOCATION UTILITIES
# ==========================================
@st.cache_data(ttl=3600, show_spinner=False)
def fetch_australian_postcodes() -> list:
    # A local cache or API call to resolve postcodes.
    # Placeholder for standard utility function.
    return []

# ==========================================
# TOOL 7: BULK MATRIX GENERATOR
# ==========================================
def generate_bulk_matrix(file_bytes: bytes, margin_target: float = 0.19, excluded_carriers: list = None) -> tuple:
    if excluded_carriers is None:
        excluded_carriers = []

    try:
        df = pd.read_csv(io.BytesIO(file_bytes))
        
        # Standardise DataFrame to prevent NaN payload injection failures
        df = df.replace({np.nan: ""})
        
        if "Routing Status" not in df.columns:
            df["Routing Status"] = "Pending"
            
        token = st.secrets["machship"]["MACHSHIP_API_TOKEN"]
        headers = { 
            "token": token, 
            "Content-Type": "application/json",
            "Accept": "application/json" 
        }
        
        base_url = get_secure_endpoint("machship_routes", "aHR0cHM6Ly9saXZlLm1hY2hzaGlwLmNvbS9hcGl2Mi9yb3V0ZXMvcmV0dXJuUm91dGVz")

        def fetch_route(index: int, row: pd.Series) -> tuple:
            try:
                # Extract and sanitise core routing variables
                sender_suburb = str(row.get("Sender Suburb", "")).strip()
                sender_postcode = str(row.get("Sender Postcode", "")).strip()
                receiver_suburb = str(row.get("Receiver Suburb", "")).strip()
                receiver_postcode = str(row.get("Receiver Postcode", "")).strip()
                
                # Financial and physical attributes
                qty = row.get("Qty", 1)
                weight = row.get("Consign Customer Charge Weight", 1.0)
                cubic = row.get("Cubic", 0.01)
                
                # Apply strict fallback types
                qty = int(qty) if qty != "" else 1
                weight = float(weight) if weight != "" else 1.0
                cubic = float(cubic) if cubic != "" else 0.01

                # Check for critical missing location data
                if not sender_suburb or not sender_postcode or not receiver_suburb or not receiver_postcode:
                    return index, "Invalid Location Data", []

                # Construct robust Machship V2 Payload
                payload = {
                    "fromLocation": {
                        "suburb": sender_suburb,
                        "postcode": sender_postcode
                    },
                    "toLocation": {
                        "suburb": receiver_suburb,
                        "postcode": receiver_postcode
                    },
                    "items": [
                        {
                            "itemType": "Carton", 
                            "quantity": qty,
                            "weight": weight,
                            "cubic": cubic
                        }
                    ],
                    "despatchDateTimeLocal": datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
                }

                response = requests.post(base_url, headers=headers, json=payload, timeout=15)
                
                if response.status_code == 200:
                    data = response.json()
                    routes = data.get("object", {}).get("routes", [])
                    
                    if not routes:
                        return index, "No Valid Routes", []
                    
                    # Filter and compile unique carriers applying the mandated Gross Profit target
                    unique_options = []
                    seen_carriers = set()
                    
                    for route in routes:
                        carrier_name = route.get("carrier", {}).get("name", "Unknown Carrier")
                        if carrier_name in excluded_carriers or carrier_name in seen_carriers:
                            continue
                            
                        # Retrieve raw cost and apply standard margin (default 19% or explicit 22%)
                        base_cost = float(route.get("totalPrice", 0.0))
                        sell_price = base_cost / (1.0 - margin_target)
                        
                        unique_options.append({
                            "display": carrier_name,
                            "price": sell_price
                        })
                        seen_carriers.add(carrier_name)
                        
                        # Limit to top 3 routes for matrix clarity
                        if len(unique_options) >= 3:
                            break
                            
                    if unique_options:
                        return index, "Success", unique_options
                    
                return index, "No Valid Routes", []
                
            except Exception as e:
                return index, f"Crash: {sanitize_error_log(str(e))}", []

        # Execute threaded batch dispatch
        with ThreadPoolExecutor(max_workers=15) as executor:
            future_to_row = {executor.submit(fetch_route, index, row): index for index, row in df.iterrows()}
            
            for future in as_completed(future_to_row):
                idx, status, options = future.result()
                
                if status != "Success":
                    df.at[idx, "Routing Status"] = status
                else:
                    df.at[idx, "Routing Status"] = "Success"
                    if len(options) > 0:
                        df.at[idx, "Option 1 (Cheapest)"] = options[0]['display']
                        df.at[idx, "Option 1 Price"] = f"${options[0]['price']:.2f}"
                    if len(options) > 1:
                        df.at[idx, "Option 2 (Alternative)"] = options[1]['display']
                        df.at[idx, "Option 2 Price"] = f"${options[1]['price']:.2f}"
                    if len(options) > 2:
                        df.at[idx, "Option 3 (Alternative)"] = options[2]['display']
                        df.at[idx, "Option 3 Price"] = f"${options[2]['price']:.2f}"

        return True, df

    except Exception as e:
        return False, f"Matrix Engine Failure: {sanitize_error_log(str(e))}"
