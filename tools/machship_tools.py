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
# LOCATION & TEMPORAL UTILITIES
# ==========================================
@st.cache_data(ttl=3600, show_spinner=False)
def fetch_australian_postcodes() -> list:
    return []

def get_next_business_day() -> str:
    next_day = datetime.datetime.now() + datetime.timedelta(days=1)
    while next_day.weekday() >= 5:
        next_day += datetime.timedelta(days=1)
    return next_day.strftime("%Y-%m-%dT09:00:00")

# ==========================================
# TOOL 7: BULK MATRIX GENERATOR
# ==========================================
def generate_bulk_matrix(file_bytes: bytes, margin_target: float = 0.19, excluded_carriers: list = None) -> tuple:
    if excluded_carriers is None:
        excluded_carriers = []

    # GP Margin Sanitisation Gate (Corrects UI Payload Inversions)
    try:
        margin_target = float(margin_target)
        if margin_target > 1.0:
            if margin_target >= 10.0:
                margin_target = margin_target / 100.0  # e.g., 19 or 22 -> 0.19 or 0.22
            else:
                margin_target = margin_target - 1.0    # e.g., 1.19 -> 0.19
        if margin_target >= 1.0 or margin_target < 0.0:
            margin_target = 0.19 # Rigid fallback to baseline FCA rule
    except ValueError:
        margin_target = 0.19
        
    gp_divisor = 1.0 - margin_target

    try:
        df = pd.read_csv(io.BytesIO(file_bytes))
        
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
        dispatch_datetime = get_next_business_day()

        def fetch_route(index: int, row: pd.Series) -> tuple:
            try:
                sender_suburb = str(row.get("Sender Suburb", "")).strip()
                sender_postcode = str(row.get("Sender Postcode", "")).strip()
                receiver_suburb = str(row.get("Receiver Suburb", "")).strip()
                receiver_postcode = str(row.get("Receiver Postcode", "")).strip()
                
                if not sender_suburb or not sender_postcode or not receiver_suburb or not receiver_postcode:
                    return index, "Invalid Location Data", []

                raw_qty = row.get("Qty", 1)
                raw_weight = row.get("Consign Customer Charge Weight", 1.0)
                raw_cubic = row.get("Cubic", 0.01)
                item_name = str(row.get("Item", "Carton")).strip()
                
                if item_name.lower() in ["nan", "none", ""]:
                    item_name = "Carton"
                
                try:
                    qty = int(float(raw_qty)) if raw_qty != "" else 1
                    total_weight = float(raw_weight) if raw_weight != "" else 1.0
                    total_cubic = float(raw_cubic) if raw_cubic != "" else 0.01
                except ValueError:
                    qty, total_weight, total_cubic = 1, 1.0, 0.01

                per_item_weight = total_weight / qty if qty > 0 else total_weight
                per_item_cubic = total_cubic / qty if qty > 0 else total_cubic
                
                volume_cm3 = per_item_cubic * 1000000.0
                side_length = max(1, int(round(volume_cm3 ** (1.0/3.0), 0)))

                payload = {
                    "companyId": 52036,
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
                            "name": item_name,
                            "itemType": "Item", 
                            "quantity": qty,
                            "weight": round(per_item_weight, 2),
                            "length": side_length,
                            "width": side_length,
                            "height": side_length
                        }
                    ],
                    "despatchDateTimeLocal": dispatch_datetime
                }

                response = requests.post(base_url, headers=headers, json=payload, timeout=15)
                
                if response.status_code == 200:
                    data = response.json()
                    ms_object = data.get("object") or {}
                    routes = ms_object.get("routes", [])
                    
                    if not routes:
                        errors = data.get("errors") or []
                        error_msg = errors[0].get("errorMessage", "No Routes Available") if errors else "No Valid Routes"
                        return index, error_msg, []
                    
                    unique_options = []
                    seen_carriers = set()
                    
                    for route in routes:
                        carrier_name = route.get("carrier", {}).get("name", "Unknown Carrier")
                        if carrier_name in excluded_carriers or carrier_name in seen_carriers:
                            continue
                            
                        c_total = route.get('consignmentTotal') or route.get('price') or route
                        
                        cost_price = c_total.get('totalCostPrice')
                        sell_price = c_total.get('totalSellPrice')
                        
                        base_cost = 0.0
                        fuel_cost = 0.0
                        derived_from_cost = False
                        
                        if cost_price is not None and float(cost_price) > 0:
                            derived_from_cost = True
                            
                            val_ex_tax = c_total.get('totalCostPriceExTax')
                            if val_ex_tax is None:
                                val_ex_tax = c_total.get('costPriceExTax')
                            ex_tax = float(val_ex_tax) if val_ex_tax is not None else (float(cost_price) / 1.1)
                            
                            val_fuel = c_total.get('totalFuelLevyCostPrice')
                            if val_fuel is None:
                                val_fuel = c_total.get('fuelLevyCostPrice')
                                
                            if val_fuel is not None:
                                fuel_cost = float(val_fuel)
                            else:
                                val_base = c_total.get('totalBaseCostPrice')
                                if val_base is None:
                                    val_base = c_total.get('baseCostPrice')
                                fuel_cost = ex_tax - float(val_base) if val_base is not None else 0.0
                                
                            base_cost = ex_tax - fuel_cost
                            
                        elif sell_price is not None and float(sell_price) > 0:
                            val_ex_tax = c_total.get('totalSellPriceExTax')
                            if val_ex_tax is None:
                                val_ex_tax = c_total.get('sellPriceExTax')
                            ex_tax = float(val_ex_tax) if val_ex_tax is not None else (float(sell_price) / 1.1)
                            
                            val_fuel = c_total.get('totalFuelLevySellPrice')
                            if val_fuel is None:
                                val_fuel = c_total.get('fuelLevySellPrice')
                                
                            if val_fuel is not None:
                                fuel_cost = float(val_fuel)
                            else:
                                val_base = c_total.get('totalBaseSellPrice')
                                if val_base is None:
                                    val_base = c_total.get('baseSellPrice')
                                fuel_cost = ex_tax - float(val_base) if val_base is not None else 0.0
                                
                            base_cost = ex_tax - fuel_cost
                        
                        base_cost = abs(base_cost)
                        fuel_cost = abs(fuel_cost)
                        
                        applied_divisor = gp_divisor if derived_from_cost else 1.0
                        sell_base = abs(base_cost / applied_divisor) if base_cost > 0 else 0.0
                        sell_fuel = abs(fuel_cost / applied_divisor) if fuel_cost > 0 else 0.0
                        sell_ex_tax = sell_base + sell_fuel
                        
                        sell_gst = sell_ex_tax * 0.10
                        sell_total = sell_ex_tax + sell_gst
                        
                        unique_options.append({
                            "display": carrier_name,
                            "base": sell_base,
                            "fuel": sell_fuel,
                            "gst": sell_gst,
                            "total": sell_total
                        })
                        seen_carriers.add(carrier_name)
                        
                        if len(unique_options) >= 3:
                            break
                            
                    if unique_options:
                        all_zero = all(opt["total"] == 0.0 for opt in unique_options)
                        status_str = "Success (TMS Rate $0.00)" if all_zero else "Success"
                        return index, status_str, unique_options
                    
                return index, f"HTTP Rejection {response.status_code}", []
                
            except Exception as e:
                return index, f"Crash: {sanitize_error_log(str(e))}", []

        with ThreadPoolExecutor(max_workers=15) as executor:
            future_to_row = {executor.submit(fetch_route, index, row): index for index, row in df.iterrows()}
            
            for future in as_completed(future_to_row):
                idx, status, options = future.result()
                
                df.at[idx, "Routing Status"] = status
                if "Success" in status:
                    if len(options) > 0:
                        df.at[idx, "Option 1 (Cheapest)"] = options[0]['display']
                        df.at[idx, "Option 1 Base ($)"] = f"${options[0]['base']:.2f}"
                        df.at[idx, "Option 1 Fuel ($)"] = f"${options[0]['fuel']:.2f}"
                        df.at[idx, "Option 1 GST ($)"] = f"${options[0]['gst']:.2f}"
                        df.at[idx, "Option 1 Total ($)"] = f"${options[0]['total']:.2f}"
                    if len(options) > 1:
                        df.at[idx, "Option 2 (Alternative)"] = options[1]['display']
                        df.at[idx, "Option 2 Base ($)"] = f"${options[1]['base']:.2f}"
                        df.at[idx, "Option 2 Fuel ($)"] = f"${options[1]['fuel']:.2f}"
                        df.at[idx, "Option 2 GST ($)"] = f"${options[1]['gst']:.2f}"
                        df.at[idx, "Option 2 Total ($)"] = f"${options[1]['total']:.2f}"
                    if len(options) > 2:
                        df.at[idx, "Option 3 (Alternative)"] = options[2]['display']
                        df.at[idx, "Option 3 Base ($)"] = f"${options[2]['base']:.2f}"
                        df.at[idx, "Option 3 Fuel ($)"] = f"${options[2]['fuel']:.2f}"
                        df.at[idx, "Option 3 GST ($)"] = f"${options[2]['gst']:.2f}"
                        df.at[idx, "Option 3 Total ($)"] = f"${options[2]['total']:.2f}"

        return True, df

    except Exception as e:
        return False, f"Matrix Engine Failure: {sanitize_error_log(str(e))}"
